"""Offline six-face speaker-box registration in an unchanged tracking world.

Axes are mathematical labels: x=right, y=front, z=up, with x cross y = z.
Contact points may lie anywhere on their respective faces. No tracker contact
offset, cabinet dimensions, acoustic centre, or absolute accuracy is inferred.
"""
import copy
import json
import math

import numpy as np

from .calibration import checked_pose, surface_from_sample
from .core import rotate


FACE_PAIRS = (("right", "left"), ("front", "back"), ("top", "bottom"))
FACE_NAMES = frozenset(name for pair in FACE_PAIRS for name in pair)
AXES = ("x", "y", "z")
DIMENSIONS = ("width", "depth", "height")


def _vector(value, name):
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain three finite numbers") from exc
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must contain three finite numbers")
    return result


def _number(value, name, minimum=None, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} is below its allowed minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds its allowed maximum")
    return value


def _world(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("An explicit nonempty world_id is required")
    return value


def _unit(value, name):
    value = _vector(value, name)
    length = float(np.linalg.norm(value))
    if not math.isfinite(length) or length < 1e-12:
        raise ValueError(f"{name} must have a finite nonzero length")
    return value / length


def _angle(a, b):
    return math.degrees(math.acos(float(np.clip(np.dot(a, b), -1., 1.))))


def _checked_static(sample):
    """Require the evidence fields emitted by strict static-pose sampling.

    This validates a saved summary, not the underlying physical contact. The
    default sampler's spread/span/gap limits are retained as operating limits.
    """
    if not isinstance(sample, dict) or sample.get("schema") != 1 or sample.get("kind") != "static_tracker_sample":
        raise ValueError("A complete summarize_static_pose result is required")
    _world(sample.get("world_id"))
    if not isinstance(sample.get("serial"), str) or not sample["serial"]:
        raise ValueError("Static sample needs a tracker serial")
    if type(sample.get("epoch")) is not int or sample["epoch"] < 0:
        raise ValueError("Static sample needs a nonnegative epoch")
    if type(sample.get("count")) is not int or sample["count"] < 30:
        raise ValueError("Static sample needs at least 30 poses")
    start = _number(sample.get("start_lsl"), "start_lsl")
    end = _number(sample.get("end_lsl"), "end_lsl")
    span = _number(sample.get("observed_span_seconds"), "observed_span_seconds", .5)
    if end <= start or span > end - start + 1e-9:
        raise ValueError("Static sample has inconsistent time bounds")
    _number(sample.get("max_arrival_gap_seconds"), "max_arrival_gap_seconds", 0., .05 + 1e-12)
    maximum = _number(sample.get("max_position_deviation_m"), "max_position_deviation_m", 0., .005)
    _number(sample.get("rms_position_deviation_m"), "rms_position_deviation_m", 0., maximum + 1e-12)
    _number(sample.get("max_orientation_deviation_degrees"), "max_orientation_deviation_degrees", 0., 2.)
    if sample.get("quality_basis") != "static_spread_and_recent_optical_not_absolute_accuracy":
        raise ValueError("Static sample has no strict sampling provenance")
    try:
        checked_pose(sample)
        json.dumps(sample, allow_nan=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Static sample contains invalid pose or non-JSON evidence") from exc


def _contact_pose(contact_transform):
    if not isinstance(contact_transform, dict) or "position_m" not in contact_transform:
        raise ValueError("Explicit tracker_from_surface contact position is required")
    position = _vector(contact_transform["position_m"], "contact position_m").tolist()
    if "quaternion_wxyz" in contact_transform:
        try:
            result = checked_pose(dict(position_m=position, quaternion_wxyz=contact_transform["quaternion_wxyz"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid explicit contact rotation") from exc
        if "normal_tracker" in contact_transform:
            expected = _unit(contact_transform["normal_tracker"], "normal_tracker")
            if np.linalg.norm(expected - rotate(result["quaternion_wxyz"], [0., 0., 1.])) > 1e-6:
                raise ValueError("Contact rotation and normal_tracker disagree")
        return result
    if "normal_tracker" not in contact_transform:
        raise ValueError("Explicit contact quaternion_wxyz or normal_tracker is required")
    normal = _unit(contact_transform["normal_tracker"], "normal_tracker")
    # Minimal rotation from +Z; rotation about the surface normal is irrelevant.
    if normal[2] < -1. + 1e-12:
        quaternion = [0., 1., 0., 0.]
    else:
        quaternion = np.array([1. + normal[2], -normal[1], normal[0], 0.])
        quaternion = (quaternion / np.linalg.norm(quaternion)).tolist()
    return dict(position_m=position, quaternion_wxyz=quaternion)


def surface_from_static(sample, contact_transform):
    """Map an explicit tracker contact point and outward normal into the world.

    contact_transform is tracker_from_surface: position_m plus either an active
    quaternion_wxyz (surface +Z is outward) or a normal_tracker vector. Missing
    offsets or orientations are rejected rather than replaced by defaults.
    """
    _checked_static(sample)
    contact = _contact_pose(contact_transform)
    surface = surface_from_sample(sample, contact)
    return dict(schema=1, kind="speaker_face_sample", world_id=sample["world_id"],
                point_m=surface["position_m"],
                normal_world=rotate(surface["quaternion_wxyz"], [0., 0., 1.]),
                world_from_surface=surface, tracker_from_surface=contact,
                static_sample=copy.deepcopy(sample), absolute_accuracy_validated=False)


def _limits(minimum, maximum):
    _number(minimum, "min_dimension_m", 1e-6)
    _number(maximum, "max_dimension_m", minimum)
    if maximum <= minimum:
        raise ValueError("Dimension maximum must exceed minimum")


def _face_data(faces, world_id):
    if not isinstance(faces, dict) or set(faces) != FACE_NAMES:
        raise ValueError("Exactly front/back/left/right/top/bottom faces are required")
    result = {}
    for name in sorted(FACE_NAMES):
        face = faces[name]
        if not isinstance(face, dict) or face.get("world_id") != world_id:
            raise ValueError(f"Face {name} does not share world_id")
        point = _vector(face.get("point_m"), f"{name}.point_m")
        normal = _unit(face.get("normal_world"), f"{name}.normal_world")
        result[name] = (point, normal)
    return result


def fit_speaker_box(faces, world_id, *, angle_tolerance_degrees=10.,
                    min_dimension_m=.01, max_dimension_m=10.):
    """Fit an orthogonal box to six explicitly labelled outward contact planes.

    Opposite normals and near-orthogonality are checked before the closest
    proper rotation is fitted. Face contact points determine plane positions;
    they need not be face centres. The enclosing radius describes this fitted
    cabinet box, not a measured frequency-dependent acoustic centre or source.
    """
    world_id = _world(world_id)
    _limits(min_dimension_m, max_dimension_m)
    _number(angle_tolerance_degrees, "angle_tolerance_degrees", 1e-6, 30.)
    data = _face_data(faces, world_id)
    pair_errors, approximate_axes = {}, []
    for axis_name, (positive, negative) in zip(AXES, FACE_PAIRS):
        plus, minus = data[positive][1], data[negative][1]
        error = _angle(plus, -minus)
        if error > angle_tolerance_degrees:
            raise ValueError(f"Opposite {positive}/{negative} normals are not antiparallel")
        pair_errors[axis_name] = error
        approximate_axes.append(_unit(plus - minus, f"{axis_name} axis"))
    approximate = np.column_stack(approximate_axes)
    orthogonality_errors = {}
    for i, j in ((0, 1), (0, 2), (1, 2)):
        error = abs(90. - _angle(approximate[:, i], approximate[:, j]))
        orthogonality_errors[AXES[i] + AXES[j]] = error
        if error > angle_tolerance_degrees:
            raise ValueError("Face axes are not sufficiently orthogonal")
    if np.linalg.det(approximate) <= 0:
        raise ValueError("Face labels form a left-handed frame; require right cross front = up")
    u, singular_values, vt = np.linalg.svd(approximate)
    rotation = u @ vt
    if np.linalg.det(rotation) <= 0:
        raise ValueError("Cannot fit a proper right-handed speaker frame")
    centre_coordinates, sizes, face_errors = [], [], {}
    for i, (positive, negative) in enumerate(FACE_PAIRS):
        axis = rotation[:, i]
        for name, sign in ((positive, 1.), (negative, -1.)):
            error = _angle(data[name][1], sign * axis)
            face_errors[name] = error
            if error > angle_tolerance_degrees:
                raise ValueError(f"Face {name} normal is inconsistent with the fitted box")
        upper, lower = float(axis @ data[positive][0]), float(axis @ data[negative][0])
        size = upper - lower
        if not min_dimension_m <= size <= max_dimension_m:
            raise ValueError(f"{DIMENSIONS[i]} is inverted, collapsed, or outside dimension limits")
        sizes.append(size)
        centre_coordinates.append((upper + lower) / 2.)
    centre = rotation @ np.asarray(centre_coordinates)
    model = dict(schema=1, kind="speaker_box", world_id=world_id,
                 center_m=centre.tolist(),
                 axes_world={name: rotation[:, i].tolist() for i, name in enumerate(AXES)},
                 dimensions_m=dict(zip(DIMENSIONS, sizes)),
                 enclosing_radius_m=float(np.linalg.norm(sizes) / 2.),
                 faces=copy.deepcopy(faces),
                 diagnostics=dict(opposite_normal_error_degrees=pair_errors,
                     axis_orthogonality_error_degrees=orthogonality_errors,
                     fitted_face_normal_error_degrees=face_errors,
                     approximate_axes_singular_values=singular_values.tolist(),
                     fitted_axes_determinant=float(np.linalg.det(rotation)),
                     angle_tolerance_degrees=angle_tolerance_degrees,
                     dimension_limits_m=[min_dimension_m, max_dimension_m],
                     coordinate_convention="x=right,y=front,z=up; cross(x,y)=z",
                     normal_fit_is_not_absolute_accuracy=True,
                     acoustic_center_measured=False),
                 absolute_accuracy_validated=False)
    validate_speaker_model(model, world_id=world_id,
                           min_dimension_m=min_dimension_m, max_dimension_m=max_dimension_m)
    return model


def validate_speaker_model(model, world_id=None, *, min_dimension_m=.01,
                           max_dimension_m=10., orthogonality_tolerance=1e-6):
    """Validate a JSON box model; return it unchanged for caller convenience."""
    _limits(min_dimension_m, max_dimension_m)
    _number(orthogonality_tolerance, "orthogonality_tolerance", 1e-12, .01)
    if not isinstance(model, dict) or model.get("schema") != 1 or model.get("kind") != "speaker_box":
        raise ValueError("Expected schema=1 speaker_box model")
    actual_world = _world(model.get("world_id"))
    if world_id is not None and actual_world != _world(world_id):
        raise ValueError("Speaker model world_id mismatch")
    _vector(model.get("center_m"), "center_m")
    axes = model.get("axes_world")
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        raise ValueError("Speaker model requires axes_world x/y/z")
    rotation = np.column_stack([_vector(axes[name], f"axes_world.{name}") for name in AXES])
    if np.max(np.abs(rotation.T @ rotation - np.eye(3))) > orthogonality_tolerance or abs(np.linalg.det(rotation) - 1.) > orthogonality_tolerance:
        raise ValueError("Speaker axes must be orthonormal and right-handed")
    dimensions = model.get("dimensions_m")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
        raise ValueError("Speaker model requires width/depth/height")
    sizes = [_number(dimensions[name], name, min_dimension_m, max_dimension_m) for name in DIMENSIONS]
    radius = _number(model.get("enclosing_radius_m"), "enclosing_radius_m", 0.)
    if not math.isclose(radius, float(np.linalg.norm(sizes) / 2.), rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError("Enclosing radius is inconsistent with the box dimensions")
    if "faces" in model:
        _face_data(model["faces"], actual_world)
    try:
        json.dumps(model, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Speaker model must contain finite JSON data") from exc
    return model


def world_to_speaker(model, p):
    """Transform one world point to the box's metric x/right,y/front,z/up frame."""
    validate_speaker_model(model)
    rotation = np.column_stack([model["axes_world"][name] for name in AXES])
    return (rotation.T @ (_vector(p, "world point") - np.asarray(model["center_m"]))).tolist()


def speaker_to_world(model, p):
    """Transform one metric speaker-frame point into the common tracking world."""
    validate_speaker_model(model)
    rotation = np.column_stack([model["axes_world"][name] for name in AXES])
    return (rotation @ _vector(p, "speaker point") + np.asarray(model["center_m"])).tolist()
