import copy
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

from lsac_capture.source_inversion import (aggregate_spatial_bins,beam_response,candidate_grid,candidate_responses,
    covariance_metrics,encode_covariance,farthest_point_selection,geometry_diagnostics,hemisphere,infer_support,run,selection_coverage,support_scores,transform_covariance)


def fixture(source=np.array([.04,.1,0.])):
    positions=np.array([[-.2,0,.25],[.2,0,.25],[-.15,.2,.35],[.15,.2,.35],
                        [-.1,-.05,.5],[.1,.3,.5],[.25,.1,.4],[-.25,.1,.4]])
    field=dict(coordinate_frame="screen",foa_assumption_enabled=True,channel_semantics_verified=False,synchronized=False,band_hz=[445.449359,561.231024],
               provenance=dict(world_id="fixture"),points=dict(columns=["window_index","x_m","y_m","z_m","energy_adc2","support_eligible","duration_seconds","motion_quality"],rows=[]))
    cov=dict(points=[],point_motion=[])
    for i,p in enumerate(positions):
        n=(source-p)/np.linalg.norm(source-p);v=np.r_[1,n];c=np.outer(v,v).astype(complex)
        field["points"]["rows"].append([i,*p,1,1,.1,1.])
        cov["points"].append([i,encode_covariance(c)])
    cal=dict(world_id="fixture",screen_world_pose=dict(position_m=[0,0,0],quaternion_wxyz=[1,0,0,0]),
             reference_world_pose=dict(position_m=[0,0,0],quaternion_wxyz=[math.sqrt(.5),-math.sqrt(.5),0,0]))
    return field,cov,cal


class SourceInversionTests(unittest.TestCase):
    def test_ideal_single_source_near_truth_with_broad_nonconfidence_support(self):
        field,cov,cal=fixture()
        result,directions=infer_support(field,cov,cal,assume_ambix=True,roi=[[-.2,.2],[-.1,.3],[-.1,.2]])
        self.assertLess(np.linalg.norm(np.array(result["primary_peak"]["position_m"])-[.04,.1,0]),.025)
        self.assertGreater(len(result["voxels"]["rows"]),20)
        self.assertGreater(len(result["peaks"]),1)
        self.assertFalse(result["range_identifiable"])
        self.assertFalse(result["support_threshold_is_confidence"])
        json.dumps(result,allow_nan=False);json.dumps(directions,allow_nan=False)
        for point in directions["points"]:
            self.assertAlmostEqual(sum(point["hemisphere_mass"]),1)
            self.assertEqual(len(point["beam_response_adc2"]),324)

    def test_covariance_and_multidirection_response_rotation_invariance(self):
        rng=np.random.default_rng(42);a=rng.normal(size=(4,4))+1j*rng.normal(size=(4,4));c=a@a.conj().T
        theta=.83;r=np.array([[math.cos(theta),0,math.sin(theta)],[0,1,0],[-math.sin(theta),0,math.cos(theta)]])
        n=rng.normal(size=(100,3));n/=np.linalg.norm(n,axis=1)[:,None]
        changed=transform_covariance(c,r)
        np.testing.assert_allclose(beam_response(c,n),beam_response(changed,n@r.T),rtol=1e-12)
        self.assertAlmostEqual(c[0,0].real,changed[0,0].real)
        self.assertAlmostEqual(covariance_metrics(c)["sphere_max_response_adc2"],covariance_metrics(changed)["sphere_max_response_adc2"],places=10)

    def test_exact_hemisphere_solid_angle_and_pole_fallback(self):
        n,solid,basis,grid=hemisphere([0,-1,0],[0,0,0],[0,1,0],[1,0,0])
        self.assertAlmostEqual(sum(solid),2*math.pi,places=12)
        np.testing.assert_allclose(np.linalg.norm(n,axis=1),1,atol=1e-14)
        self.assertTrue(basis["up_seed_fallback"])
        self.assertTrue(np.all(n@np.array(basis["forward"])>0))

    def test_duplicate_dwell_does_not_increase_spatial_bin_weight_or_change_c(self):
        field,cov,_=fixture();first,_=aggregate_spatial_bins(field,cov,np.eye(3),np.zeros(3))
        for index in range(8,108):
            row=field["points"]["rows"][0].copy();row[0]=index;field["points"]["rows"].append(row)
            cov["points"].append([index,cov["points"][0][1]])
        second,_=aggregate_spatial_bins(field,cov,np.eye(3),np.zeros(3))
        self.assertEqual(len(first),len(second))
        for a,b in zip(first,second):
            np.testing.assert_allclose(a["covariance"],b["covariance"],atol=1e-12)
            self.assertEqual(a["weight"],b["weight"])

    def test_parallel_directions_and_collinear_baseline_are_flagged(self):
        bins=[dict(position_m=np.array([i*.02,0,.4]),metrics=dict(maximum_direction=[0,0,-1])) for i in range(8)]
        report=geometry_diagnostics(bins)
        self.assertTrue(report["near_parallel_directions"])
        self.assertTrue(report["collinear_or_insufficient_baseline"])
        self.assertTrue(report["geometrically_degenerate"])
        self.assertFalse(report["range_identifiable"])

    def test_uniform_isotropic_does_not_report_sharp_source(self):
        field,cov,cal=fixture()
        cov["points"]=[[index,encode_covariance(np.eye(4))] for index,_ in cov["points"]]
        result,_=infer_support(field,cov,cal,assume_ambix=True,roi=[[-.1,.1],[0,.2],[-.05,.05]])
        self.assertEqual(result["status"],"uninformative_no_unique_peak")
        self.assertIsNone(result["primary_peak"])
        self.assertEqual(result["peaks"],[])
        self.assertTrue(result["diagnostics"]["score_distribution"]["flat"])

    def test_coherent_two_sources_never_become_confirmed_single_physical_source(self):
        field,cov,cal=fixture();rows=field["points"]["rows"]
        for i,row in enumerate(rows):
            p=np.array(row[1:4]);v=np.zeros(4,complex)
            for source,phase in ((np.array([-.08,.1,0]),0),(np.array([.08,.1,0]),.7)):
                delta=source-p;v+=np.exp(1j*phase)*np.r_[1,delta/np.linalg.norm(delta)]
            cov["points"][i][1]=encode_covariance(np.outer(v,v.conj()))
            row[4]=float(abs(v[0])**2)
        result,directions=infer_support(field,cov,cal,assume_ambix=True,roi=[[-.15,.15],[0,.2],[-.1,.1]])
        self.assertFalse(result["range_identifiable"])
        self.assertIn("virtual",result["best_candidate_interpretation"])
        self.assertTrue(all(p["metrics"]["dominant_rank_is_not_single_physical_source_evidence"] for p in directions["points"]))

    def test_full_sphere_fallback_keeps_candidate_behind_attention_hemisphere(self):
        v=np.array([1,0,0,1]);c=np.outer(v,v)
        bins=[dict(position_m=np.array([0,0,.1]),covariance=c,basis=dict(forward=[0,0,-1]),metrics=covariance_metrics(c))]
        response,valid,outside=candidate_responses(bins,np.array([[0,0,.3]]))
        self.assertTrue(valid[0,0]);self.assertTrue(outside[0,0]);self.assertAlmostEqual(response[0,0],1)

    def test_coverage_mask_is_separate_from_missing_values(self):
        ratios=np.ones((5,2));valid=np.array([[1,1],[1,1],[1,0],[1,0],[0,0]],dtype=bool)
        raw,score,mask,coverage=support_scores(ratios,valid,minimum_used_bin_fraction=.8)
        self.assertTrue(mask[0]);self.assertFalse(mask[1]);self.assertTrue(np.isnan(score[1]))
        self.assertAlmostEqual(coverage[0],.8)

    def test_explicit_assumption_and_world_identity_required(self):
        field,cov,cal=fixture()
        with self.assertRaises(ValueError):infer_support(field,cov,cal)
        cal["world_id"]="wrong"
        with self.assertRaises(ValueError):infer_support(field,cov,cal,assume_ambix=True)

    def test_candidate_grid_x_fastest(self):
        p,grid=candidate_grid([[0,.04],[0,.02],[0,.02]],.02)
        np.testing.assert_allclose(p[:4],[[0,0,0],[.02,0,0],[.04,0,0],[0,.02,0]])
        self.assertEqual(grid["shape"],[3,2,2])

    def test_changed_input_hash_refused_before_output_write(self):
        original=b'{"provenance":{}}'
        with patch("pathlib.Path.read_bytes",side_effect=[original,b'{}',b'{}',b'changed']), \
             patch("pathlib.Path.mkdir"),patch("pathlib.Path.exists",return_value=False), \
             patch("pathlib.Path.open") as output, \
             patch("lsac_capture.source_inversion.infer_support",return_value=({},{})):
            with self.assertRaisesRegex(RuntimeError,"changed during inference"):
                run("fixture","calibration.json","output",assume_ambix=True)
            output.assert_not_called()

    def test_full_covariance_mismatch_is_rejected(self):
        field,cov,_=fixture();field["points"]["rows"][0][4]=100
        with self.assertRaisesRegex(ValueError,"C00 disagrees"):
            aggregate_spatial_bins(field,cov,np.eye(3),np.zeros(3))

    def test_selection_stops_by_spatial_coverage_and_reports_resource_shortfall(self):
        bins=[dict(position_m=np.array([i*.01,0,0]),bin_key=[i,0,0],point_count=1,
                   sum_duration_seconds=.1,duration_quality_weight_seconds=.1) for i in range(100)]
        selected=farthest_point_selection(bins,512,.03)
        report=selection_coverage(bins,selected,.03)
        self.assertLess(len(selected),100)
        self.assertTrue(report["coverage_target_met"])
        self.assertEqual(len(report["all_bin_density_audit"]),100)
        limited=farthest_point_selection(bins,2,.03)
        self.assertFalse(selection_coverage(bins,limited,.03)["coverage_target_met"])

    def test_short_occupancy_is_retained_but_not_a_full_statistical_vote(self):
        # One weak edge cell cannot veto a mature cell merely through equal counting.
        ratios=np.array([[1.,.1],[.1,1.]])
        raw,_,_,_=support_scores(ratios,np.ones_like(ratios,dtype=bool),effective_weights=[.02,1.])
        self.assertGreater(raw[1],raw[0])
        field,cov,cal=fixture();field["points"]["rows"][0][-2]=.0002
        result,directions=infer_support(field,cov,cal,assume_ambix=True,roi=[[-.1,.1],[0,.2],[-.05,.05]])
        self.assertEqual(result["diagnostics"]["selected_low_occupancy_bin_count"],1)
        self.assertTrue(any(0<p["effective_bin_weight"]<1 for p in directions["points"]))

    def test_preintegrated_physical_cells_keep_keys_and_disjoint_segments(self):
        field,cov,_=fixture();field["sampling_mode"]="spatial";field["spatial_cell_coordinate_frame"]="palm_render"
        field["spatial_cell_geometry"]=dict(raw_to_render_rotation_rows=np.eye(3).tolist(),raw_to_render_translation_m=[0,0,0])
        field["grid"]=dict(spatial_bin_size_m=.02)
        field["points"]["rows"]=field["points"]["rows"][:2]
        for row in field["points"]["rows"]:row[1:4]=[.02,0,.3]
        cov["point_motion"]=[dict(window_index=i,spatial_cell=[i,0,15],source_indices=[7,9],
                                  segments=[dict(start_sample=100,frames=10),dict(start_sample=200,frames=10)]) for i in range(2)]
        cov["spatial_bins"]=[[[i,0,15],[.02,0,.3],1,cov["points"][i][1]] for i in range(2)]
        bins,_=aggregate_spatial_bins(field,cov,np.eye(3),np.zeros(3),.02)
        self.assertEqual(len(bins),2)
        self.assertEqual(bins[0]["source_parent_indices"],[7,9])
        self.assertEqual(len(bins[0]["occupancy_segments"]),2)


if __name__=="__main__":unittest.main()
