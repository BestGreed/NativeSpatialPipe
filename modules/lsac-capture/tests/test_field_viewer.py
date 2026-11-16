import json
import math
import re
import unittest
from unittest.mock import patch

from lsac_capture.field_viewer import render_geometry,render_viewer,validate_field,validate_source_volume,table_geometry,validate_multisource_fit,build_viewer
from lsac_capture.core import multiply,rotate


def field():
    return dict(coordinate_frame="screen",grid=dict(origin_m=[0,0,0],spacing_m=.015,shape=[2,2,2]),
                points=dict(columns=["window_index","x_m","y_m","z_m","energy_adc2"],rows=[[0,.01,.02,.03,100.]]),
                voxels=dict(columns=["flat_index","energy_adc2","nearest_distance_m"],rows=[[3,90.,.01]]),
                directional_experimental=False,foa_assumption_enabled=False,limitations=[])


def calibrated_field():
    def q(axis,angle):
        a=math.radians(angle)/2
        return [math.cos(a),*[math.sin(a)*v for v in axis]]
    raw_screen=multiply(q([1,0,0],-39),q([0,0,1],35))
    calibration=dict(world_id="same-world",screen_world_pose=dict(position_m=[0,.08,0],quaternion_wxyz=raw_screen),
                     reference_world_pose=dict(position_m=[0,0,0],quaternion_wxyz=q([1,0,0],-90)))
    source=field()
    source["provenance"]=dict(world_id="same-world")
    source["screen_reference"]=dict(world_from_screen=calibration["screen_world_pose"],
                                   reference_world_pose=calibration["reference_world_pose"])
    return source,calibration


def virtual_source():
    source,calibration=calibrated_field()
    geometry=render_geometry(source,calibration)
    volume=dict(schema="lsac-source-support-v1",coordinate_frame="palm_render",world_id="same-world",
                render_geometry=geometry,foa_assumption_enabled=True,
                grid=dict(origin_m=[0,0,0],spacing_m=.02,shape=[2,2,2]),
                voxels=dict(columns=["flat_index","support_score","used_bin_fraction"],rows=[[0,.8,1.]]),
                selected_points=[dict(selection_index=0,position_m=[0,.1,.2],bin_key=[0,1,2])],peaks=[],diagnostics={})
    directions=dict(coordinate_frame="palm_render",world_id="same-world",render_geometry=geometry,
                    angular_grid=dict(azimuth_edges_degrees=[-90,90],elevation_edges_degrees=[-90,90],
                                      azimuth_centers_degrees=[0],elevation_centers_degrees=[0]),
                    points=[dict(selection_index=0,position_m=[0,.1,.2],directions=[[0,0,-1]],beam_response_over_sphere_max=[.8])])
    return source,calibration,volume,directions


def multiregion():
    source,calibration=calibrated_field()
    fit=dict(world_id="same-world",coordinate_frame="palm_render",render_geometry=render_geometry(source,calibration),
             source_regions_measured=False,source_FR_known=False,
             regions=[dict(name=f"nominal_{i}",center_m=[i*.03,0,.1],size_m=[.02,.02,.04],placement_basis="nominal unmeasured",
                           latent_basis_diagonal_sum_by_quadrature=[1e9*(i+1)]) for i in range(4)],
             selected_model=dict(train_relative_covariance_error=.7661,spatial_holdout_relative_covariance_error=.7597),
             diagonal_power_only_comparison=dict(train_relative_covariance_error=.8851,spatial_holdout_relative_covariance_error=.8473),
             provenance=dict(field_sha256="deliberately-wrong"))
    return source,calibration,fit


class FieldViewerTests(unittest.TestCase):
    def test_embedded_data_is_safe_json_and_no_remote_data_fetch(self):
        source=field();source["limitations"]=['</script><script>alert("x")</script> & 文本']
        html=render_viewer(source)
        encoded=re.search(r'<script id="field-data" type="application/json">(.*?)</script>',html,re.S)[1]
        self.assertNotIn("</script>",encoded)
        self.assertEqual(json.loads(encoded),source)
        self.assertIn('./vendor/three.module.js',html)
        self.assertIn('./vendor/OrbitControls.js',html)
        self.assertNotIn('fetch(',html)
        self.assertIn('window.__LSAC_VIEWER__',html)

    def test_rejects_wrong_coordinate_frame_invalid_grid_and_unknown_indices(self):
        source=field();source["coordinate_frame"]="world"
        with self.assertRaises(ValueError):validate_field(source)
        source=field();source["grid"]["spacing_m"]=0
        with self.assertRaises(ValueError):validate_field(source)
        source=field();source["voxels"]["rows"][0][0]=8
        with self.assertRaises(ValueError):validate_field(source)
        source=field();source["voxels"]["rows"]*=2
        with self.assertRaises(ValueError):validate_field(source)

    def test_direction_is_opt_in_and_unknown_space_not_filled(self):
        html=render_viewer(field())
        self.assertIn('id="showPoints" type="checkbox" checked',html)
        self.assertIn('id="showVoxels" type="checkbox">',html)
        self.assertIn('id="showProxy" type="checkbox">',html)
        self.assertIn('field.directional_experimental&&field.foa_assumption_enabled',html)
        self.assertIn('未覆盖区域保持空白',html)
        self.assertIn('非 dB SPL',html)
        self.assertIn('USB_ch1（假定 W）',html)

    def test_two_plane_intersection_corrects_unverified_inplane_rotation(self):
        source,calibration=calibrated_field()
        geometry=render_geometry(source,calibration)
        self.assertTrue(geometry["legacy_inplane_axis_unverified"])
        self.assertGreater(abs(geometry["raw_screen_x_dot_palm_up"]),.4)
        self.assertEqual(geometry["in_plane_heading_basis"],"intersection_of_screen_and_palm_planes")
        self.assertAlmostEqual(geometry["opening_angle_degrees"],129.)
        bottom=geometry["screen_bottom_render_endpoints_m"]
        expected_height=.08-.18847/2*math.sin(math.radians(129))
        self.assertAlmostEqual(bottom[0][1],expected_height)
        self.assertAlmostEqual(bottom[1][1],expected_height)
        self.assertAlmostEqual(bottom[0][2],bottom[1][2])
        physical=geometry["screen_render_pose"]
        normal=rotate(physical["quaternion_wxyz"],[0,0,1])
        self.assertAlmostEqual(normal[0],0.)
        self.assertAlmostEqual(normal[1],math.sin(math.radians(39)))
        self.assertAlmostEqual(normal[2],math.cos(math.radians(39)))

    def test_raw_points_keep_distances_under_shared_render_transform(self):
        source,calibration=calibrated_field()
        original=json.loads(json.dumps(source))
        geometry=render_geometry(source,calibration)
        rotation=geometry["raw_to_render_rotation_rows"];translation=geometry["raw_to_render_translation_m"]
        def transform(point):return [sum(a*b for a,b in zip(row,point))+t for row,t in zip(rotation,translation)]
        a=[.1,-.2,.35];b=[-.05,.11,.25]
        self.assertAlmostEqual(math.dist(a,b),math.dist(transform(a),transform(b)))
        basis=geometry["basis_world_columns"]
        for i in range(3):
            for j in range(3):self.assertAlmostEqual(sum(a*b for a,b in zip(basis[i],basis[j])),float(i==j))
        render_viewer(source,calibration)
        self.assertEqual(source,original)

    def test_rejects_cross_world_bundle_and_changed_raw_screen_calibration(self):
        source,calibration=calibrated_field()
        calibration=dict(calibration,world_id="other-world")
        with self.assertRaises(ValueError):render_geometry(source,calibration)
        source,calibration=calibrated_field()
        modified=dict(calibration,screen_world_pose=dict(calibration["screen_world_pose"],position_m=[.1,.08,0]))
        with self.assertRaises(ValueError):render_geometry(source,modified)

    def test_legacy_field_and_explicit_same_world_bundle_both_render(self):
        source,calibration=calibrated_field()
        html=render_viewer(source,calibration)
        encoded=re.search(r'<script id="render-data" type="application/json">(.*?)</script>',html,re.S)[1]
        metadata=json.loads(encoded)
        self.assertTrue(metadata["enabled"])
        self.assertIn('id="focusLaptop"',html)
        self.assertIn('id="viewScreen"',html)
        self.assertEqual(render_geometry(field())["raw_to_render_translation_m"],[0,0,0])

    def test_virtual_support_is_separate_embedded_dimensionless_data(self):
        source,calibration,volume,directions=virtual_source()
        html=render_viewer(source,calibration,volume,directions)
        encoded=re.search(r'<script id="source-data" type="application/json">(.*?)</script>',html,re.S)[1]
        payload=json.loads(encoded)
        self.assertEqual(payload["volume"]["voxels"]["rows"][0][1],.8)
        self.assertIn("接收反投影支持 · 无量纲对照分数",html)
        self.assertIn('id="showSourceVolume" type="checkbox">',html)
        self.assertIn('id="showSourcePoints" type="checkbox">',html)
        self.assertIn("不是笔记本心形发射模型",html)
        self.assertIn("不是声能、概率、置信度",html)
        self.assertIn('id="showSourcePaths" type="checkbox">',html)
        self.assertIn("sourceVolume.selected_points",html)

    def test_virtual_support_rejects_wrong_basis_world_energy_units_and_direction_lengths(self):
        source,calibration,volume,directions=virtual_source()
        geometry=render_geometry(source,calibration)
        changed=json.loads(json.dumps(volume));changed["render_geometry"]["origin_world_m"][0]+=.01
        with self.assertRaises(ValueError):validate_source_volume(changed,geometry)
        changed=dict(volume,world_id="wrong-world")
        with self.assertRaises(ValueError):validate_source_volume(changed,geometry)
        changed=json.loads(json.dumps(volume));changed["voxels"]["rows"][0][1]=100.
        with self.assertRaises(ValueError):validate_source_volume(changed,geometry)
        changed=json.loads(json.dumps(directions));changed["points"][0]["directions"][0]=[0,0,-10]
        with self.assertRaises(ValueError):validate_source_volume(volume,geometry,changed)

    def test_spatial_sampling_preserves_disjoint_duration_and_nullable_time_index(self):
        source,calibration=calibrated_field()
        source["sampling_mode"]="spatial"
        source["points"]["columns"] += ["start_sample","frames","duration_seconds","segment_count","cell_i","cell_j","cell_k"]
        source["points"]["rows"][0] += [None,1216128,25.336,4,1,2,3]
        html=render_viewer(source,calibration)
        embedded=json.loads(re.search(r'<script id="field-data" type="application/json">(.*?)</script>',html,re.S)[1])
        self.assertEqual(embedded,source)
        self.assertEqual(embedded["sampling_mode"],"spatial")
        row=dict(zip(embedded["points"]["columns"],embedded["points"]["rows"][0]))
        self.assertIsNone(row["start_sample"])
        self.assertEqual(row["duration_seconds"],25.336)
        self.assertEqual(row["segment_count"],4)

    def test_table_plane_is_recomputed_from_world_pose_not_cached_old_palm_values(self):
        source,calibration=calibrated_field();geometry=render_geometry(source,calibration)
        reference=dict(world_id="same-world",table_world_pose=dict(position_m=[.15,-.03,.1],
                        quaternion_wxyz=calibration["reference_world_pose"]["quaternion_wxyz"]),
                       table_plane_palm=dict(unit_normal=[1,0,0],offset_m=999))
        table=table_geometry(reference,geometry)
        for a,b in zip(table["unit_normal_render"],[0,1,0]):self.assertAlmostEqual(a,b)
        self.assertAlmostEqual(table["offset_m"],-.03)
        self.assertAlmostEqual(table["table_height_at_palm_origin_m"],-.03)
        normal=rotate(table["quaternion_wxyz"],[0,0,1])
        for a,b in zip(normal,table["unit_normal_render"]):self.assertAlmostEqual(a,b)
        center=table["center_render_m"]
        self.assertAlmostEqual(sum(a*b for a,b in zip(center,normal)),table["offset_m"])
        self.assertFalse(table["is_floor_reference"])
        html=render_viewer(source,calibration,table_reference=reference)
        self.assertIn("桌面参考平面（范围示意）",html)
        encoded=re.search(r'<script id="render-data" type="application/json">(.*?)</script>',html,re.S)[1]
        self.assertAlmostEqual(json.loads(encoded)["table_reference"]["offset_m"],-.03)
        with self.assertRaises(ValueError):table_geometry(dict(reference,world_id="other-world"),geometry)

    def test_multiregion_overlay_is_categorical_and_not_coefficient_heatmap(self):
        source,calibration,fit=multiregion()
        html=render_viewer(source,calibration,multisource_fit=fit)
        payload=json.loads(re.search(r'<script id="multisource-data" type="application/json">(.*?)</script>',html,re.S)[1])
        self.assertEqual(payload,fit)
        self.assertNotIn('id="showRegions" type="checkbox" checked',html)
        self.assertIn("盒的位置/尺寸未实测",html)
        self.assertIn("不能与旧 1/r² 残差直接比较",html)
        self.assertNotIn("latent_basis_diagonal_sum_by_quadrature",html.split('<script type="module">')[1])

    def test_multiregion_requires_same_geometry_and_four_named_regions(self):
        source,calibration,fit=multiregion();geometry=render_geometry(source,calibration)
        with self.assertRaises(ValueError):validate_multisource_fit(dict(fit,world_id="other-world"),geometry)
        with self.assertRaises(ValueError):validate_multisource_fit(dict(fit,regions=fit["regions"][:3]),geometry)
        modified=json.loads(json.dumps(fit));modified["render_geometry"]["origin_world_m"][0]+=.01
        with self.assertRaises(ValueError):validate_multisource_fit(modified,geometry)

    def test_multiregion_build_rejects_field_hash_mismatch_before_writing(self):
        source,calibration,fit=multiregion()
        def read_text(path,**kwargs):
            return json.dumps(fit if path.name=="multisource-fit.json" else source)
        with patch("pathlib.Path.read_text",autospec=True,side_effect=read_text), \
             patch("pathlib.Path.read_bytes",return_value=b"actual-field-data"), \
             patch("pathlib.Path.write_text") as write:
            with self.assertRaises(ValueError):build_viewer("field.json","index.html",multisource_fit_path="multisource-fit.json")
            write.assert_not_called()


if __name__=="__main__":unittest.main()
