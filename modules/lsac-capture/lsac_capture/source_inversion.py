"""Experimental equivalent-source support from full FOA covariances.

Only numpy; no device or raw-audio access. Scores are neither field energy nor
probability/confidence. Coherent sources/reflections can create virtual peaks.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .core import rotate
from .field_viewer import render_geometry

PHYSICAL_BASIS_VERSION="palm-horizontal-intersection-v1"
DEFAULT_ROI=[[-.4,.4],[-.15,.45],[-.25,.4]]


def _unit(vector):
    vector=np.asarray(vector,dtype=float); norm=np.linalg.norm(vector)
    if not np.isfinite(norm) or norm<1e-12: raise ValueError("Degenerate direction")
    return vector/norm


def decode_covariance(value):
    a=np.asarray(value,dtype=float)
    if a.shape!=(4,4,2) or not np.isfinite(a).all(): raise ValueError("Expected finite complex 4x4 covariance")
    c=a[:,:,0]+1j*a[:,:,1]; scale=max(np.linalg.norm(c),1e-30)
    if np.linalg.norm(c-c.conj().T)>scale*1e-6: raise ValueError("Non-Hermitian covariance")
    c=(c+c.conj().T)/2
    if c[0,0].real<=0 or np.linalg.eigvalsh(c)[0]<-scale*1e-6: raise ValueError("Non-positive covariance")
    return c


def encode_covariance(c):
    return np.stack((np.asarray(c).real,np.asarray(c).imag),axis=-1).tolist()


def transform_covariance(c,rotation):
    r=np.asarray(rotation,dtype=float)
    if r.shape!=(3,3) or not np.allclose(r@r.T,np.eye(3),atol=1e-8) or np.linalg.det(r)<.999999:
        raise ValueError("Expected proper orthogonal rotation")
    u=np.eye(4);u[1:,1:]=r
    return u@c@u.T


def beam_response(c,directions):
    n=np.asarray(directions,dtype=float)
    h=.5*np.concatenate((np.ones((*n.shape[:-1],1)),n),axis=-1)
    return np.maximum(0,np.einsum("...i,ij,...j->...",h.conj(),c,h).real)


def _quadratic_maximum(a,b):
    """Global max n' A n+2b'n on the unit sphere (trust-region secular root)."""
    eigen,vectors=np.linalg.eigh((a+a.T)/2); beta=vectors.T@b
    top=eigen[-1]; scale=max(float(np.max(np.abs(eigen))),float(np.linalg.norm(b)),1e-30)
    top_mask=top-eigen<=scale*1e-11
    lower=~top_mask; hard=np.zeros(3)
    hard[lower]=beta[lower]/(top-eigen[lower])
    if np.linalg.norm(beta[top_mask])<=scale*1e-11 and np.dot(hard,hard)<=1:
        hard[np.flatnonzero(top_mask)[0]]=math.sqrt(max(0,1-np.dot(hard,hard)))
        n=vectors@hard
    else:
        lo=top; hi=top+max(float(np.linalg.norm(beta))*2,scale*1e-10)
        for _ in range(90):
            middle=(lo+hi)/2; denominator=np.maximum(middle-eigen,scale*1e-15)
            if np.sum((beta/denominator)**2)>1:lo=middle
            else:hi=middle
        n=_unit(vectors@(beta/np.maximum(hi-eigen,scale*1e-15)))
    return float(n@a@n+2*b@n),n


def covariance_metrics(c):
    a=c[1:,1:].real;b=c[1:,0].real;constant=float(c[0,0].real)
    maximum,direction=_quadratic_maximum(a,b)
    minus_minimum,_=_quadratic_maximum(-a,-b)
    high=max(0,(constant+maximum)/4); low=max(0,(constant-minus_minimum)/4)
    eig=np.maximum(np.linalg.eigvalsh(c),0); v=np.r_[1,direction]
    amplitude=float(np.real(v.conj()@c@v)/(v@v)**2)
    residual=float(np.linalg.norm(c-amplitude*np.outer(v,v))/max(np.linalg.norm(c),1e-30))
    return dict(sphere_max_response_adc2=high,sphere_min_response_adc2=low,
                directional_contrast=(high-low)/max(high,1e-30),
                maximum_direction=direction.tolist(),dominant_eigenvalue_fraction=float(eig[-1]/max(sum(eig),1e-30)),
                ideal_single_plane_wave_covariance_relative_residual=residual,
                dominant_rank_is_not_single_physical_source_evidence=True)


def hemisphere(position,center,screen_up,screen_right):
    f=_unit(np.asarray(center)-position); up=_unit(screen_up)
    fallback=bool(abs(np.dot(f,up))>.95)
    if fallback: up=_unit(screen_right)
    right=_unit(np.cross(f,up)); up=_unit(np.cross(right,f))
    edges=np.deg2rad(np.linspace(-90,90,19)); centers=(edges[:-1]+edges[1:])/2
    azimuth,elevation=np.meshgrid(centers,centers,indexing="xy")
    directions=(np.cos(elevation)[...,None]*np.cos(azimuth)[...,None]*f+
                np.cos(elevation)[...,None]*np.sin(azimuth)[...,None]*right+
                np.sin(elevation)[...,None]*up).reshape(-1,3)
    solid=np.repeat((np.diff(np.sin(edges))*np.diff(edges)[0])[:,None],18,axis=1).reshape(-1)
    basis=dict(forward=f.tolist(),right=right.tolist(),up=up.tolist(),up_seed_fallback=fallback)
    grid=dict(azimuth_edges_degrees=np.rad2deg(edges).tolist(),elevation_edges_degrees=np.rad2deg(edges).tolist(),
              azimuth_centers_degrees=np.rad2deg(centers).tolist(),elevation_centers_degrees=np.rad2deg(centers).tolist(),
              flatten="azimuth_fastest",solid_angles_sr=solid.tolist(),solid_angle_sum_sr=float(sum(solid)))
    return directions,solid,basis,grid


def aggregate_spatial_bins(field,covariances,rotation,translation,bin_size=.01):
    """Linear C average within bin; each resulting bin has total inference weight 1."""
    if not math.isfinite(bin_size) or bin_size<=0: raise ValueError("Invalid bin size")
    columns=field["points"]["columns"]; rows=field["points"]["rows"]
    c_by_id={int(i):value for i,value in covariances["points"]}
    motion={int(row["window_index"]):row for row in covariances.get("point_motion",[])}
    bins={}; rejected=Counter(); r=np.asarray(rotation);t=np.asarray(translation)
    spatial=field.get("sampling_mode")=="spatial"
    full_positions={tuple(int(v) for v in row[0]):np.asarray(row[1],dtype=float) for row in covariances.get("spatial_bins",[])}
    if spatial:
        geometry=field.get("spatial_cell_geometry",{})
        if field.get("spatial_cell_coordinate_frame")!="palm_render" or not geometry:
            raise ValueError("Spatial cells require their physical palm geometry provenance")
        if not np.allclose(geometry["raw_to_render_rotation_rows"],r,atol=1e-8) or not np.allclose(geometry["raw_to_render_translation_m"],t,atol=1e-8):
            raise ValueError("Spatial cell basis differs from inversion physical frame")
        if abs(float(field["grid"]["spatial_bin_size_m"])-bin_size)>1e-10:
            raise ValueError("Cannot silently rebin pre-integrated spatial cells at another size")
    for raw in rows:
        row=dict(zip(columns,raw)); index=int(row["window_index"]); m=motion.get(index,{})
        if not m.get("support_eligible",row.get("support_eligible",True)):
            rejected["motion_ineligible"]+=1;continue
        duration=float(m.get("duration_seconds",row.get("duration_seconds",m.get("frames",4800)/48000)))
        quality=float(m.get("motion_quality",row.get("motion_quality",1.)))
        weight=duration*quality
        if not math.isfinite(weight) or weight<=0: rejected["nonpositive_duration_quality"]+=1;continue
        try:c=decode_covariance(c_by_id[index])
        except (KeyError,ValueError):rejected["invalid_covariance"]+=1;continue
        if abs(float(row["energy_adc2"])-c[0,0].real)>max(abs(c[0,0].real)*1e-4,1e-10):
            raise ValueError("field point C00 disagrees with full covariance; refuse mixed input artifacts")
        raw_position=full_positions[tuple(m["spatial_cell"])] if spatial else np.array([row[k] for k in ("x_m","y_m","z_m")])
        p=r@raw_position+t
        if not np.isfinite(p).all():rejected["invalid_position"]+=1;continue
        if spatial:
            key=tuple(int(v) for v in m["spatial_cell"])
            if key in bins:raise ValueError("Duplicate pre-integrated physical cell")
        else:key=tuple(int(v) for v in np.floor(p/bin_size))
        item=bins.setdefault(key,dict(bin_key=list(key),position_sum=np.zeros(3),c_sum=np.zeros((4,4),complex),
                                     sum_weight=0.,sum_duration_seconds=0.,source_window_indices=[],parent_indices=set(),segments=[],sum_frames=0))
        item["position_sum"]+=weight*p;item["c_sum"]+=weight*transform_covariance(c,r)
        item["sum_weight"]+=weight;item["sum_duration_seconds"]+=duration;item["source_window_indices"].append(index)
        item["sum_frames"]+=int(m.get("frames",row.get("frames",round(duration*48000))))
        item["parent_indices"].update(int(v) for v in m.get("source_indices",[row.get("source_window_index",m.get("source_window_index",index))]))
        item["segments"].extend(m.get("segments",[]))
    result=[]
    for key,item in sorted(bins.items()):
        result.append(dict(bin_key=list(key),position_m=item["position_sum"]/item["sum_weight"],
                           covariance=item["c_sum"]/item["sum_weight"],weight=1.,
                           duration_quality_weight_seconds=item["sum_weight"],sum_duration_seconds=item["sum_duration_seconds"],
                           source_window_indices=item["source_window_indices"],source_parent_indices=sorted(item["parent_indices"]),
                           point_count=len(item["source_window_indices"]),occupancy_segments=item["segments"],sum_frames=item["sum_frames"]))
    return result,dict(rejected)


def farthest_point_selection(bins,maximum=512,target_distance=.03):
    if type(maximum) is not int or not 1<=maximum<=512:raise ValueError("maximum points must be 1..512")
    if not math.isfinite(target_distance) or target_distance<=0:raise ValueError("Invalid target coverage distance")
    if not bins:return []
    positions=np.array([b["position_m"] for b in bins]);distance=np.linalg.norm(positions-positions.mean(0),axis=1)
    chosen=[int(np.argmax(distance))];remaining=np.full(len(bins),np.inf)
    while True:
        remaining=np.minimum(remaining,np.sum((positions-positions[chosen[-1]])**2,axis=1));remaining[chosen]=-1
        if np.max(remaining)<=target_distance**2 or len(chosen)>=min(maximum,len(bins)):break
        chosen.append(int(np.argmax(remaining)))
    return chosen


def selection_coverage(bins,selected_indices,target_distance):
    positions=np.array([b["position_m"] for b in bins]);selected=positions[selected_indices]
    nearest=np.sqrt(np.min(np.sum((positions[:,None,:]-selected[None,:,:])**2,axis=2),axis=1))
    uncovered=np.flatnonzero(nearest>target_distance*(1+1e-10))
    audit=[dict(bin_index=i,bin_key=b["bin_key"],position_m=b["position_m"].tolist(),point_count=b["point_count"],
                sum_duration_seconds=b["sum_duration_seconds"],duration_quality_weight_seconds=b["duration_quality_weight_seconds"],
                nearest_representative_distance_m=float(nearest[i])) for i,b in enumerate(bins)]
    return dict(target_distance_m=target_distance,max_nearest_representative_distance_m=float(np.max(nearest)),
                mean_nearest_representative_distance_m=float(np.mean(nearest)),uncovered_bin_count=len(uncovered),
                uncovered_bin_indices=uncovered.tolist(),coverage_target_met=not len(uncovered),
                all_valid_bins_remain_in_density_audit=True,all_bin_density_audit=audit,
                total_eligible_duration_seconds=sum(b["sum_duration_seconds"] for b in bins),
                total_eligible_frames=sum(b.get("sum_frames",0) for b in bins),
                full_precision_metadata_preferred_over_compact_field_columns=True)


def candidate_grid(roi=DEFAULT_ROI,spacing=.02):
    bounds=np.asarray(roi,dtype=float)
    if bounds.shape!=(3,2) or not np.isfinite(bounds).all() or np.any(bounds[:,1]<=bounds[:,0]):raise ValueError("Invalid ROI")
    if not math.isfinite(spacing) or spacing<=0:raise ValueError("Invalid grid spacing")
    shape=np.floor((bounds[:,1]-bounds[:,0])/spacing+1e-8).astype(int)+1
    if np.prod(shape)>150000:raise ValueError("ROI exceeds 150000 candidate cells")
    grid=np.indices(shape).reshape(3,-1,order="F").T*spacing+bounds[:,0]
    return grid,dict(origin_m=bounds[:,0].tolist(),spacing_m=spacing,shape=shape.tolist(),order="x_fastest",
                     requested_bounds_m=bounds.tolist(),effective_bounds_m=np.stack((grid.min(0),grid.max(0)),axis=1).tolist())


def candidate_responses(bins,candidates,r_min=.03,r_max=1.5):
    if not math.isfinite(r_min+r_max) or not 0<r_min<r_max:raise ValueError("Invalid point-to-candidate range")
    ratios=[];valid=[];outside=[]
    for b in bins:
        delta=candidates-b["position_m"];distance=np.linalg.norm(delta,axis=1)
        mask=(distance>=r_min)&(distance<=r_max);direction=delta/np.maximum(distance[:,None],1e-30)
        maximum=b["metrics"]["sphere_max_response_adc2"]
        response=beam_response(b["covariance"],direction)/max(maximum,1e-30)
        ratios.append(np.clip(response,0,1));valid.append(mask)
        outside.append(np.dot(direction,np.array(b["basis"]["forward"]))<0)
    return np.array(ratios),np.array(valid,dtype=bool),np.array(outside,dtype=bool)


def support_scores(ratios,valid,outlier_floor=.1,minimum_used_bin_fraction=.8,effective_weights=None):
    if not 0<outlier_floor<1 or not 0<minimum_used_bin_fraction<=1:raise ValueError("Invalid score parameters")
    count=valid.sum(0);coverage=count/max(len(valid),1)
    mask=(count>0)&(coverage>=minimum_used_bin_fraction)
    weights=np.ones(len(valid)) if effective_weights is None else np.asarray(effective_weights,dtype=float)
    if weights.shape!=(len(valid),) or not np.isfinite(weights).all() or np.any(weights<0) or np.any(weights>1):
        raise ValueError("Effective bin weights must be bounded 0..1")
    denominator=np.sum(weights[:,None]*valid,axis=0);mask&=denominator>1e-15
    log=np.log(outlier_floor+(1-outlier_floor)*np.clip(ratios,0,1))
    # Missing bins remain absent, not a zero response; eligibility and coverage are exported separately.
    mean=np.sum(np.where(valid,log,0)*weights[:,None],axis=0)/np.maximum(denominator,1e-30)
    raw=np.full(count.shape,np.nan);raw[mask]=np.exp(mean[mask])
    normalized=np.full(count.shape,np.nan)
    if mask.any():normalized[mask]=raw[mask]/np.max(raw[mask])
    return raw,normalized,mask,coverage


def score_distribution(raw,mask):
    values=raw[mask]
    if not len(values):return dict(eligible_cells=0,relative_range=0.,flat=True)
    relative_range=float((values.max()-values.min())/max(values.max(),1e-30))
    return dict(eligible_cells=len(values),minimum=float(values.min()),maximum=float(values.max()),
                median=float(np.median(values)),std=float(np.std(values)),relative_range=relative_range,
                quantiles=np.quantile(values,[0,.1,.5,.9,1]).tolist(),flat=relative_range<.01)


def competing_peaks(candidates,scores,mask,grid,maximum=5,separation=.08):
    order=np.flatnonzero(mask)[np.argsort(-scores[mask],kind="stable")];chosen=[]
    shape=np.asarray(grid["shape"]);origin=np.asarray(grid["origin_m"]);spacing=grid["spacing_m"]
    for i in order:
        if any(np.linalg.norm(candidates[i]-candidates[j])<separation for j in chosen):continue
        chosen.append(int(i))
        if len(chosen)>=maximum:break
    return [dict(rank=rank+1,flat_index=i,position_m=candidates[i].tolist(),support_score=float(scores[i]),
                 touches_roi_boundary=bool(np.any((np.rint((candidates[i]-origin)/spacing).astype(int)==0)|
                                                  (np.rint((candidates[i]-origin)/spacing).astype(int)==shape-1))),
                 interpretation="competing_equivalent_or_virtual_candidate_not_confirmed_physical_source")
            for rank,i in enumerate(chosen)]


def geometry_diagnostics(bins):
    positions=np.array([b["position_m"] for b in bins]);directions=np.array([b["metrics"]["maximum_direction"] for b in bins])
    a=np.mean(np.eye(3)[None,:,:]-directions[:,:,None]*directions[:,None,:],axis=0)
    eigen=np.linalg.eigvalsh(a);condition=float(eigen[-1]/eigen[0]) if eigen[0]>1e-12 else None
    spread=np.linalg.eigvalsh((positions-positions.mean(0)).T@(positions-positions.mean(0))/max(len(positions),1))
    rank=int(np.sum(spread>max(spread[-1]*1e-3,1e-10)))
    return dict(ray_projection_eigenvalues=eigen.tolist(),ray_condition_number=condition,
                position_covariance_eigenvalues_m2=spread.tolist(),position_rank=rank,
                near_parallel_directions=bool(eigen[0]<1e-3),collinear_or_insufficient_baseline=rank<2,
                geometrically_degenerate=bool(eigen[0]<1e-3 or rank<2),
                range_identifiable=False,reason="Unverified channels/timing, coherent multi-speaker/reflective model; diagnostics cannot certify range.")


def attenuation_diagnostic(bins,source):
    positions=np.array([b["position_m"] for b in bins]);r=np.linalg.norm(positions-source,axis=1)
    energy=np.array([b["covariance"][0,0].real for b in bins]);x=np.column_stack((1/np.maximum(r,.03)**2,np.ones(len(r))))
    weights=np.array([b.get("effective_bin_weight",1.) for b in bins]);root=np.sqrt(weights)
    weighted_x=x*root[:,None];weighted_energy=energy*root
    trials=[np.zeros(2),np.array([max(0,float(weighted_x[:,0]@weighted_energy/(weighted_x[:,0]@weighted_x[:,0]))),0]),
            np.array([0,max(0,float(weights@energy/sum(weights)))])]
    fitted=np.linalg.lstsq(weighted_x,weighted_energy,rcond=None)[0]
    if np.all(fitted>=0):trials.append(fitted)
    fit=min(trials,key=lambda v:float(np.sum(weights*(x@v-energy)**2)));predicted=x@fit
    return dict(model="E_adc2 = A / r_m^2 + B",A_relative_adc2_m2=float(fit[0]),B_adc2=float(fit[1]),
                relative_rmse=float(np.sqrt(np.sum(weights*(predicted-energy)**2)/sum(weights))/max(weights@energy/sum(weights),1e-30)),
                design_condition_number=float(np.linalg.cond(x)),minimum_distance_m=float(r.min()),maximum_distance_m=float(r.max()),
                conditional_only=True,physical_units_calibrated=False,
                warning="Conditional single equivalent point-source diagnostic only; coherent speakers/near field/reflections can violate inverse-square model.")


def infer_support(field,covariances,calibration,*,assume_ambix=False,maximum_points=512,roi=DEFAULT_ROI,
                  spacing=.02,bin_size=None,target_coverage_distance=.03,r_min=.03,r_max=1.5,outlier_floor=.1,minimum_used_bin_fraction=.8,
                  support_threshold=.8):
    if not assume_ambix:raise ValueError("Experimental inversion requires explicit --assume-ambix")
    if field.get("coordinate_frame")!="screen" or not field.get("foa_assumption_enabled"):
        raise ValueError("Expected screen FOA covariance field produced under explicit AmbiX assumption")
    if not 0<support_threshold<=1:raise ValueError("Invalid relative support threshold")
    geometry=render_geometry(field,calibration)
    if not geometry.get("enabled"):raise ValueError("Measured screen and palm reference required")
    r=np.asarray(geometry["raw_to_render_rotation_rows"]);t=np.asarray(geometry["raw_to_render_translation_m"])
    if bin_size is None:bin_size=float(field.get("grid",{}).get("spatial_bin_size_m",.02))
    bins,rejected=aggregate_spatial_bins(field,covariances,r,t,bin_size)
    if not bins:raise ValueError("No eligible spatial covariance bins")
    selected_indices=farthest_point_selection(bins,maximum_points,target_coverage_distance);selected=[bins[i] for i in selected_indices]
    coverage_audit=selection_coverage(bins,selected_indices,target_coverage_distance)
    band=np.asarray(field.get("band_hz"),dtype=float)
    if band.shape!=(2,) or not np.isfinite(band).all() or band[1]<=band[0]:
        raise ValueError("Physical bandwidth metadata required for occupancy statistical weight")
    bandwidth=float(band[1]-band[0])
    for b in bins:
        b["bandwidth_time_product"]=bandwidth*b["sum_duration_seconds"]
        b["effective_bin_weight"]=min(1.,b["bandwidth_time_product"])
    for audit,b in zip(coverage_audit["all_bin_density_audit"],bins):
        audit.update(effective_bin_weight=b["effective_bin_weight"],bandwidth_time_product=b["bandwidth_time_product"])
    weights=np.array([b["effective_bin_weight"] for b in selected])
    center=np.array(geometry["screen_render_pose"]["position_m"])
    screen_up=rotate(geometry["screen_render_pose"]["quaternion_wxyz"],[0,1,0])
    screen_right=rotate(geometry["screen_render_pose"]["quaternion_wxyz"],[1,0,0])
    directions_output=[];angular_grid=None
    for index,b in enumerate(selected):
        directions,solid,basis,angular_grid=hemisphere(b["position_m"],center,screen_up,screen_right)
        metrics=covariance_metrics(b["covariance"]);b.update(metrics=metrics,basis=basis)
        response=beam_response(b["covariance"],directions);normalizer=max(float(response@solid),1e-30)
        directions_output.append(dict(selection_index=index,bin_key=b["bin_key"],position_m=b["position_m"].tolist(),
                                     basis=basis,directions=directions.tolist(),beam_response_adc2=response.tolist(),
                                     directional_score_per_sr=(response/normalizer).tolist(),hemisphere_mass=(response*solid/normalizer).tolist(),
                                     beam_response_over_sphere_max=(response/max(metrics["sphere_max_response_adc2"],1e-30)).tolist(),
                                     covariance_palm=encode_covariance(b["covariance"]),metrics=metrics,
                                     source_window_indices=b["source_window_indices"],source_parent_indices=b["source_parent_indices"],
                                     source_occupancy_segments=b["occupancy_segments"],
                                     point_count=b["point_count"],inference_weight=b["effective_bin_weight"],
                                     effective_bin_weight=b["effective_bin_weight"],bandwidth_time_product=b["bandwidth_time_product"],
                                     duration_quality_weight_seconds=b["duration_quality_weight_seconds"],sum_duration_seconds=b["sum_duration_seconds"]))
    candidates,grid=candidate_grid(roi,spacing);ratios,valid,outside=candidate_responses(selected,candidates,r_min,r_max)
    raw,score,mask,coverage=support_scores(ratios,valid,outlier_floor,minimum_used_bin_fraction,weights)
    distribution=score_distribution(raw,mask)
    informative=sum(b["metrics"]["directional_contrast"]>=.05 for b in selected)
    uninformative=not informative or distribution["flat"]
    peaks=[] if uninformative else competing_peaks(candidates,score,mask,grid)
    support=mask&(score>=support_threshold);indices=np.flatnonzero(support)
    boundary=np.zeros(len(candidates),bool);shape=np.asarray(grid["shape"])
    cells=np.rint((candidates-np.asarray(grid["origin_m"]))/spacing).astype(int)
    boundary=np.any((cells==0)|(cells==shape-1),axis=1)
    sensitivity=[]
    for floor in (.05,.1,.2):
        rr,ss,mm,_=support_scores(ratios,valid,floor,minimum_used_bin_fraction,weights)
        meaningful=mm.any() and not score_distribution(rr,mm)["flat"] and not uninformative
        best=int(np.nanargmax(rr)) if meaningful else None
        sensitivity.append(dict(outlier_floor=floor,peak_position_m=candidates[best].tolist() if best is not None else None,
                                displacement_from_primary_m=float(np.linalg.norm(candidates[best]-peaks[0]["position_m"])) if best is not None and peaks else None))
    # Split whole spatial bins; correlated subwindows never cross this split.
    holdout=[]
    for parity in (0,1):
        subset=np.arange(len(selected))[np.arange(len(selected))%2==parity]
        if not len(subset):continue
        rr,ss,mm,_=support_scores(ratios[subset],valid[subset],outlier_floor,minimum_used_bin_fraction,weights[subset])
        meaningful=mm.any() and not score_distribution(rr,mm)["flat"] and not uninformative
        best=int(np.nanargmax(rr)) if meaningful else None
        holdout.append(dict(split=parity,selected_bin_indices=subset.tolist(),peak_position_m=candidates[best].tolist() if best is not None else None,
                            distribution=score_distribution(rr,mm)))
    displacement=float(np.linalg.norm(np.array(holdout[0]["peak_position_m"])-holdout[1]["peak_position_m"])) if len(holdout)==2 and all(h["peak_position_m"] is not None for h in holdout) else None
    selected_summary=[{k:v for k,v in d.items() if k in ("selection_index","bin_key","position_m","source_window_indices","source_parent_indices","point_count","inference_weight","effective_bin_weight","bandwidth_time_product","sum_duration_seconds","duration_quality_weight_seconds")} for d in directions_output]
    common=dict(coordinate_frame="palm_render",physical_basis_version=PHYSICAL_BASIS_VERSION,world_id=geometry["world_id"],render_geometry=geometry,
                foa_assumption_enabled=True,channel_semantics_verified=bool(field.get("channel_semantics_verified",False)),
                synchronized=bool(field.get("synchronized",False)),range_identifiable=False,
                time_basis=field.get("time_basis"),field_provenance=field.get("provenance",{}),
                input_sampling_mode=field.get("sampling_mode","legacy_windows"),selected_points_are_time_path=False,
                band_hz=field.get("band_hz"),covariance_definition=field.get("covariance_definition"),
                input_channel_order_assumed=field.get("input_channel_order_assumed"),normalization_assumed=field.get("normalization_assumed"))
    directions_document=dict(schema="lsac-direction-likelihoods-v1",**common,angular_grid=angular_grid,points=directions_output,
                             response_definition="h(n)^H C h(n), h=0.5*[1,nx,ny,nz]; full Hermitian C retained",
                             beam_white_noise_gain=.5,unit="ADC_count^2 for response; normalized overlapping beam score for mass",
                             hemisphere_is_attention_only=True,independent_ray_count=0,
                             limits="324 directions are correlated FOA beam samples, not independent energy rays or 10-degree angular resolution.")
    equal_raw,equal_score,equal_mask,_=support_scores(ratios,valid,outlier_floor,minimum_used_bin_fraction)
    equal_best=int(np.nanargmax(equal_raw)) if equal_mask.any() and not score_distribution(equal_raw,equal_mask)["flat"] and not uninformative else None
    equal_sensitivity=dict(equal_bin_peak_position_m=candidates[equal_best].tolist() if equal_best is not None else None,
                           weighted_peak_displacement_m=float(np.linalg.norm(candidates[equal_best]-peaks[0]["position_m"])) if equal_best is not None and peaks else None,
                           equal_bin_score_distribution=score_distribution(equal_raw,equal_mask))
    diagnostics=dict(score_distribution=distribution,informative_bin_count=informative,directional_contrast_threshold=.05,
                     uninformative=uninformative,geometry=geometry_diagnostics(selected),
                     selected_spatial_bin_count=len(selected),all_spatial_bin_count=len(bins),input_point_count=len(field["points"]["rows"]),
                     point_rejections=rejected,full_sphere_fallback_bin_count=int(np.any(outside&valid,axis=1).sum()),
                     full_sphere_fallback_candidate_bin_pairs=int((outside&valid).sum()),
                     outlier_floor_sensitivity=sensitivity,split_spatial_bins_holdout=holdout,holdout_peak_displacement_m=displacement,
                     support_threshold_sensitivity=[dict(relative_threshold=threshold,cells=int(np.sum(mask&(score>=threshold)))) for threshold in (.7,.8,.9)],
                     support_touches_roi_boundary=bool(np.any(support&boundary)),
                     eligible_cell_count=int(mask.sum()),excluded_cell_count=int((~mask).sum()),
                     used_bin_fraction_minimum=float(coverage[mask].min()) if mask.any() else None,
                     used_bin_fraction_maximum=float(coverage[mask].max()) if mask.any() else None,
                     selected_low_occupancy_bin_count=int(np.sum(weights<1)),all_low_occupancy_bin_count=sum(b["effective_bin_weight"]<1 for b in bins),
                     minimum_effective_bin_weight=float(min(weights)),effective_weight_sum=float(sum(weights)),
                     low_occupancy_equal_bin_sensitivity=equal_sensitivity)
    source=dict(schema="lsac-source-support-v1",**common,kind="experimental_equivalent_or_virtual_source_support",
                status="uninformative_no_unique_peak" if uninformative else "experimental_support_unvalidated",grid=grid,
                roi_units="m",roi_is_declared_software_prior_not_measured_boundary=True,
                unit="dimensionless_relative_support_not_energy_or_probability",
                voxels=dict(columns=["flat_index","support_score","used_bin_fraction"],
                            rows=[[int(i),round(float(score[i]),6),round(float(coverage[i]),6)] for i in indices]),
                candidate_mask=dict(excluded_flat_indices=np.flatnonzero(~mask).tolist(),
                                    missing_voxel_rows_mean="eligible_below_support_threshold_or_ineligible_see_excluded_flat_indices"),
                support_threshold= support_threshold,support_threshold_is_confidence=False,
                score_definition="max-normalized exp(bounded-weight mean across spatial bins of log(outlier_floor+(1-outlier_floor)*beam/sphere_max)); weights saturate at one",
                candidate_mask_definition="r_min<=candidate-to-bin distance<=r_max; used-bin fraction gate; missing values remain absent, not zero",
                selection=dict(method="farthest_point_until_declared_spatial_coverage_target_or_resource_cap",maximum_points_resource_cap=maximum_points,
                               spatial_bin_size_m=bin_size,bin_internal_weight="duration_seconds * motion_quality; normalize within each bin",
                               each_selected_bin_maximum_weight=1.,selected_original_bin_indices=selected_indices,
                               effective_bin_weight_rule="min(1, bandwidth_hz * accumulated_occupancy_seconds)",
                               bandwidth_hz=bandwidth,statistical_weight_rule_is_heuristic_not_calibrated_DOF=True,
                               coverage=coverage_audit,resource_cap_reached_before_coverage=not coverage_audit["coverage_target_met"],
                               source_subwindows_are_not_independent=True),
                range_prior_m=[r_min,r_max],minimum_used_bin_fraction=minimum_used_bin_fraction,outlier_floor=outlier_floor,
                selected_points=selected_summary,peaks=peaks,primary_peak=peaks[0] if peaks else None,
                best_candidate_interpretation="experimental equivalent/virtual candidate, not confirmed physical source",diagnostics=diagnostics,
                attenuation_diagnostic=attenuation_diagnostic(selected,np.array(peaks[0]["position_m"])) if peaks else None,
                limitations=["Channel order/SN3D, USB rotation and synchronization are not independently validated.",
                             "Coherent left/right speakers and reflections can produce rank-1 covariance and virtual crossings.",
                             "Near-field, diffraction and interference can violate this direction and inverse-square model.",
                             "Source support is not interpolated local field energy, physical source confirmation, confidence or W/m2.",
                             "Range remains unidentifiable pending external validation regardless of apparently sharp support."])
    return source,directions_document


def run(field_directory,calibration,output_directory,**settings):
    folder=Path(field_directory);calibration=Path(calibration);out=Path(output_directory)
    paths={"field":folder/"field.json","covariances":folder/"covariances.json","calibration":calibration}
    snapshots={k:p.read_bytes() for k,p in paths.items()}
    data={k:json.loads(value.decode("utf-8")) for k,value in snapshots.items()}
    out.mkdir(parents=True,exist_ok=True)
    targets=[out/"source-volume.json",out/"direction-likelihoods.json"]
    if any(p.exists() for p in targets):raise FileExistsError("Refusing to overwrite source inversion outputs")
    source,directions=infer_support(data["field"],data["covariances"],data["calibration"],**settings)
    provenance={k+"_sha256":hashlib.sha256(value).hexdigest() for k,value in snapshots.items()}
    expected=data["field"].get("provenance",{}).get("calibration_sha256")
    if expected and expected!=provenance["calibration_sha256"]:
        raise ValueError("Calibration file hash differs from field provenance")
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=provenance[k+"_sha256"] for k,p in paths.items()):
        raise RuntimeError("Input artifact changed during inference; no result written")
    provenance.update(field_directory=str(folder.resolve()),calibration=str(calibration.resolve()))
    for document in (source,directions):document["provenance"]=provenance
    payloads=[json.dumps(document,ensure_ascii=False,separators=(",",":"),allow_nan=False) for document in (source,directions)]
    if len(payloads[0].encode("utf-8"))>2_000_000:raise ValueError("Compact source volume exceeded 2MB limit")
    for path,payload in zip(targets,payloads):
        with path.open("x",encoding="utf-8") as stream:stream.write(payload)
    return dict(source_volume=str(targets[0]),direction_likelihoods=str(targets[1]),status=source["status"],
                selected_spatial_bins=source["diagnostics"]["selected_spatial_bin_count"],
                primary_peak=source["primary_peak"],range_identifiable=False,source_volume_bytes=len(payloads[0].encode("utf-8")))


def main(argv=None):
    p=argparse.ArgumentParser(description="Experimental equivalent-source support, never physical source confirmation.")
    p.add_argument("field_directory");p.add_argument("calibration");p.add_argument("output_directory")
    p.add_argument("--assume-ambix",action="store_true");p.add_argument("--maximum-points",type=int,default=512)
    p.add_argument("--spacing",type=float,default=.02);p.add_argument("--bin-size",type=float)
    p.add_argument("--target-coverage-distance",type=float,default=.03)
    p.add_argument("--roi",type=float,nargs=6,metavar=("XMIN","XMAX","YMIN","YMAX","ZMIN","ZMAX"))
    p.add_argument("--r-min",type=float,default=.03);p.add_argument("--r-max",type=float,default=1.5)
    p.add_argument("--outlier-floor",type=float,default=.1);p.add_argument("--minimum-used-bin-fraction",type=float,default=.8)
    p.add_argument("--support-threshold",type=float,default=.8);a=p.parse_args(argv)
    settings=vars(a).copy()
    for key in ("field_directory","calibration","output_directory"):settings.pop(key)
    if a.roi is None:settings["roi"]=DEFAULT_ROI
    else:settings["roi"]=np.array(a.roi).reshape(3,2).tolist()
    print(json.dumps(run(a.field_directory,a.calibration,a.output_directory,**settings),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
