import copy
import json
import math
import unittest

import numpy as np

from lsac_capture.calibration import A_SERIAL, summarize_static_pose
from lsac_capture.core import rotate
from lsac_capture.speaker_geometry import (FACE_PAIRS, fit_speaker_box,
    speaker_to_world, surface_from_static, validate_speaker_model, world_to_speaker)


WORLD = "speaker-rig-world"


def rotation(axis, degrees):
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    angle = math.radians(degrees) / 2.
    return [math.cos(angle), *(axis * math.sin(angle)).tolist()]


def strict_sample(position=(0., 0., 0.), quaternion=(1., 0., 0., 0.)):
    events = [dict(kind="pose", serial=A_SERIAL, epoch=0, valid=True,
                   arrival_lsl=10. + i * .01, optical_age_seconds=.005,
                   position_m=list(position), quaternion_wxyz=list(quaternion))
              for i in range(101)]
    return summarize_static_pose(events, A_SERIAL, 10., 11., WORLD)


def box_faces(center=(1.4, -2., .8), sizes=(.3, .4, .7), quaternion=None):
    quaternion = quaternion or rotation([1., 2., 3.], 47.)
    axes = np.column_stack([rotate(quaternion, a) for a in np.eye(3)])
    faces = {}
    for i, pair in enumerate(FACE_PAIRS):
        for name, sign in zip(pair, (1., -1.)):
            # Deliberately touch off-centre, at different places on each face.
            local = np.array([.17, -.21, .13]) * sizes * sign
            local[i] = sign * sizes[i] / 2.
            faces[name] = dict(world_id=WORLD,
                point_m=(np.asarray(center) + axes @ local).tolist(),
                normal_world=(sign * axes[:, i]).tolist())
    return faces, axes


class SpeakerGeometryTests(unittest.TestCase):
    def test_rotated_box_from_off_centre_contacts_and_round_trip(self):
        center, sizes = [1.4, -2., .8], [.3, .4, .7]
        faces, axes = box_faces(center, sizes)
        original = copy.deepcopy(faces)
        model = fit_speaker_box(faces, WORLD)
        np.testing.assert_allclose(model["center_m"], center, atol=1e-12)
        np.testing.assert_allclose(list(model["dimensions_m"].values()), sizes, atol=1e-12)
        fitted_axes = np.column_stack(list(model["axes_world"].values()))
        np.testing.assert_allclose(fitted_axes, axes, atol=1e-12)
        self.assertAlmostEqual(model["enclosing_radius_m"], np.linalg.norm(sizes) / 2.)
        for p in ([0., 0., 0.], [.1, -.2, .3], [-2., 3., 1.]):
            np.testing.assert_allclose(world_to_speaker(model, speaker_to_world(model, p)), p, atol=1e-12)
        self.assertEqual(faces, original)
        self.assertIs(validate_speaker_model(model, WORLD), model)
        self.assertEqual(json.loads(json.dumps(model, allow_nan=False))["kind"], "speaker_box")

    def test_small_normal_noise_fits_a_proper_orthogonal_frame(self):
        faces, _ = box_faces()
        rng = np.random.default_rng(12)
        for face in faces.values():
            face["normal_world"] = (np.array(face["normal_world"]) + rng.normal(0., .003, 3)).tolist()
        model = fit_speaker_box(faces, WORLD)
        matrix = np.column_stack(list(model["axes_world"].values()))
        np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(matrix), 1.)
        np.testing.assert_allclose(model["center_m"], [1.4, -2., .8], atol=.003)
        np.testing.assert_allclose(list(model["dimensions_m"].values()), [.3, .4, .7], atol=.003)
        self.assertGreater(max(model["diagnostics"]["fitted_face_normal_error_degrees"].values()), 0.)

    def test_missing_extra_face_and_world_mismatch_are_rejected(self):
        faces, _ = box_faces()
        missing = copy.deepcopy(faces); missing.pop("bottom")
        extra = copy.deepcopy(faces); extra["unknown"] = extra["top"]
        different = copy.deepcopy(faces); different["front"]["world_id"] = "other"
        for invalid in (missing, extra, different):
            with self.subTest(invalid=list(invalid)):
                with self.assertRaises(ValueError):
                    fit_speaker_box(invalid, WORLD)
        with self.assertRaises(ValueError):
            fit_speaker_box(faces, "")

    def test_flipped_normal_left_handed_labels_and_inverted_dimension_fail(self):
        faces, _ = box_faces()
        flipped = copy.deepcopy(faces)
        flipped["top"]["normal_world"] = (-np.array(flipped["top"]["normal_world"])).tolist()
        mirrored = copy.deepcopy(faces)
        mirrored["front"], mirrored["back"] = mirrored["back"], mirrored["front"]
        inverted = copy.deepcopy(faces)
        inverted["left"]["point_m"], inverted["right"]["point_m"] = inverted["right"]["point_m"], inverted["left"]["point_m"]
        for invalid in (flipped, mirrored, inverted):
            with self.assertRaises(ValueError):
                fit_speaker_box(invalid, WORLD)

    def test_nonorthogonal_normals_invalid_values_and_dimension_limits_fail(self):
        faces, _ = box_faces(quaternion=[1., 0., 0., 0.])
        skew = copy.deepcopy(faces)
        skew["front"]["normal_world"] = [1., 1., 0.]
        skew["back"]["normal_world"] = [-1., -1., 0.]
        bad_point = copy.deepcopy(faces); bad_point["left"]["point_m"][0] = float("nan")
        bad_normal = copy.deepcopy(faces); bad_normal["left"]["normal_world"] = [0., 0., 0.]
        tiny, _ = box_faces(sizes=(.001, .4, .7))
        enormous, _ = box_faces(sizes=(11., .4, .7))
        for invalid in (skew, bad_point, bad_normal, tiny, enormous):
            with self.assertRaises(ValueError):
                fit_speaker_box(invalid, WORLD)

    def test_contact_offset_and_surface_normal_follow_tracker_rotation(self):
        q = rotation([0., 0., 1.], 90.)
        sample = strict_sample([2., 3., 4.], q)
        contact = dict(position_m=[.1, 0., .02], quaternion_wxyz=rotation([0., 1., 0.], 90.))
        face = surface_from_static(sample, contact)
        np.testing.assert_allclose(face["point_m"], [2., 3.1, 4.02], atol=1e-12)
        np.testing.assert_allclose(face["normal_world"], [0., 1., 0.], atol=1e-12)
        self.assertEqual(face["world_id"], WORLD)
        self.assertFalse(face["absolute_accuracy_validated"])
        face["static_sample"]["position_m"][0] = 99.
        self.assertEqual(sample["position_m"][0], 2.)

    def test_explicit_contact_normal_is_supported_and_missing_geometry_rejected(self):
        sample = strict_sample()
        for normal in ([1., 2., 3.], [0., 0., -1.]):
            face = surface_from_static(sample, dict(position_m=[.02, .03, .04], normal_tracker=normal))
            np.testing.assert_allclose(face["normal_world"], np.array(normal) / np.linalg.norm(normal), atol=1e-12)
        for contact in ({}, {"normal_tracker": [0., 0., 1.]}, {"position_m": [0., 0., 0.]},
                        dict(position_m=[0., 0., 0.], quaternion_wxyz=[1., 0., 0., 0.], normal_tracker=[1., 0., 0.])):
            with self.assertRaises(ValueError):
                surface_from_static(sample, contact)

    def test_incomplete_or_unstable_static_contact_is_rejected(self):
        sample = strict_sample()
        contact = dict(position_m=[0., 0., 0.], normal_tracker=[0., 0., 1.])
        changes = ({"count": 29}, {"max_position_deviation_m": .006},
                   {"max_orientation_deviation_degrees": 2.1},
                   {"max_arrival_gap_seconds": .06}, {"observed_span_seconds": .4},
                   {"world_id": ""}, {"epoch": None}, {"quality_basis": "unknown"})
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    surface_from_static(dict(sample, **change), contact)
        incomplete = copy.deepcopy(sample); incomplete.pop("max_position_deviation_m")
        with self.assertRaises(ValueError):
            surface_from_static(incomplete, contact)

    def test_model_validation_rejects_bad_axes_radius_world_and_nonfinite_data(self):
        model = fit_speaker_box(box_faces()[0], WORLD)
        scaled = copy.deepcopy(model); scaled["axes_world"]["x"] = [2. * v for v in scaled["axes_world"]["x"]]
        mirrored = copy.deepcopy(model); mirrored["axes_world"]["z"] = [-v for v in mirrored["axes_world"]["z"]]
        radius = copy.deepcopy(model); radius["enclosing_radius_m"] *= 2.
        center = copy.deepcopy(model); center["center_m"][0] = float("inf")
        for invalid in (scaled, mirrored, radius, center):
            with self.assertRaises(ValueError):
                validate_speaker_model(invalid)
        with self.assertRaises(ValueError):
            validate_speaker_model(model, "other-world")


if __name__ == "__main__":
    unittest.main()
