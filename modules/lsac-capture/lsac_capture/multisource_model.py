"""Experimental coherent equivalent-source regions, not a loudspeaker FR fit.

Unknown complex source covariances retain cross terms. Equivalent monopole
basis functions can interfere to represent directional radiation; individual
physical drivers are not assigned a cardioid or an omnidirectional pattern.
The receiver manifold and propagation approximations remain uncalibrated.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from .core import rotate
from .field_viewer import render_geometry
from .source_inversion import aggregate_spatial_bins,encode_covariance


def reflect_points(points,normal,offset):
    n=np.asarray(normal,dtype=float);n=n/np.linalg.norm(n);p=np.asarray(points,dtype=float)
    return p-2*(p@n-offset)[...,None]*n


def project_psd(matrices):
    values,vectors=np.linalg.eigh((matrices+matrices.swapaxes(-1,-2).conj())/2)
    return (vectors*np.maximum(values,0)[...,None,:])@vectors.swapaxes(-1,-2).conj()


def transfer_operator(positions,basis_points,frequencies,table_normal,table_offset,reflection=0.,speed=343.):
    """Helmholtz Green functions and declared ideal first-order receiver basis.

    This is a receive manifold, not a cardioid emission fit. Reflected pressure
    is added coherently before covariance. Source/receiver boundary effects
    and the actual microphone's radial filters are not known by this function.
    """
    positions=np.asarray(positions,dtype=float);sources=np.asarray(basis_points,dtype=float)
    frequency=np.asarray(frequencies,dtype=float)
    def direct(source):
        delta=source[None,:,:]-positions[:,None,:];distance=np.linalg.norm(delta,axis=-1)
        if np.any(distance<.02):raise ValueError('Receiver too close to an equivalent basis point')
        direction=delta/distance[...,None]
        manifold=np.concatenate((np.ones((*direction.shape[:-1],1)),direction),axis=-1)
        green=np.exp(-2j*np.pi*frequency[None,:,None]*distance[:,None,:]/speed)/(4*np.pi*distance[:,None,:])
        return green[:,:,None,:]*manifold.transpose(0,2,1)[:,None,:,:]
    image=reflect_points(sources,table_normal,table_offset)
    return (direct(sources)+complex(reflection)*direct(image))/np.sqrt(len(frequency))


def prediction(operator,spectra):
    return np.einsum('nfak,fkl,nfbl->nab',operator,spectra,operator.conj(),optimize=True)


def fit_covariances(operator,observed,weights=None,iterations=300,ridge=.001,diagonal_only=False):
    """Projected accelerated least squares with PSD constraints and backtracking."""
    if type(iterations) is not int or not 1<=iterations<=5000 or not np.isfinite(ridge) or ridge<0:raise ValueError('Invalid solver limits')
    h=np.asarray(operator);c=np.asarray(observed);n,f,_,k=h.shape
    weights=np.ones(n) if weights is None else np.asarray(weights,dtype=float)
    if c.shape!=(n,4,4) or not np.isfinite(h).all() or not np.isfinite(c).all() or np.any(weights<0) or weights.sum()<=0:
        raise ValueError('Invalid covariance fit data')
    scale=np.maximum(np.trace(c,axis1=1,axis2=2).real/4,.1)
    w=weights/(scale**2*weights.sum())
    adjoint=lambda e:np.einsum('nfak,nab,nfbl->fkl',h.conj(),w[:,None,None]*e,h,optimize=True)
    loss=lambda s:float(np.sum(w[:,None,None]*np.abs(prediction(h,s)-c)**2)+ridge*np.sum(abs(s)**2))
    def project(s):
        if diagonal_only:
            out=np.zeros_like(s);ix=np.arange(k);out[:,ix,ix]=np.maximum(np.diagonal(s,axis1=1,axis2=2).real,0);return out
        return project_psd(s)
    # Power iteration estimates a safe starting step; majorization checks the actual step.
    v=np.tile(np.eye(k)[None,:,:],(f,1,1)).astype(complex);v/=np.linalg.norm(v)
    lipschitz=1.
    for _ in range(12):
        vv=2*adjoint(prediction(h,v))+2*ridge*v;lipschitz=max(float(np.linalg.norm(vv)),1e-9);v=vv/lipschitz
    lipschitz*=1.1;s=np.zeros((f,k,k),complex);y=s.copy();momentum=1.;old_loss=loss(s)
    history=[]
    for step in range(iterations):
        gy=2*adjoint(prediction(h,y)-c)+2*ridge*y;ly=loss(y)
        for _ in range(24):
            candidate=project(y-gy/lipschitz);delta=candidate-y;value=loss(candidate)
            major=ly+float(np.vdot(gy,delta).real)+.5*lipschitz*float(np.vdot(delta,delta).real)
            if value<=major+1e-10:break
            lipschitz*=2
        else:raise ValueError('PSD solver backtracking did not converge')
        if value>old_loss+1e-9 and momentum>1:
            y=s.copy();momentum=1.;continue
        next_momentum=(1+np.sqrt(1+4*momentum**2))/2
        y=candidate+(momentum-1)/next_momentum*(candidate-s);s=candidate;momentum=next_momentum;old_loss=value
        if step%25==0:history.append(dict(iteration=step,objective=value))
    return s,dict(objective=old_loss,iterations=iterations,ridge=ridge,diagonal_only=diagonal_only,history=history)


def relative_error(predicted,observed,weights):
    numerator=np.sum(np.asarray(weights)[:,None,None]*abs(predicted-observed)**2)
    denominator=np.sum(np.asarray(weights)[:,None,None]*abs(observed)**2)
    return float(np.sqrt(numerator/max(denominator,1e-30)))


def run(field_directory,calibration_path,table_path,regions_path,output,iterations=300):
    paths=dict(field=Path(field_directory)/'field.json',covariances=Path(field_directory)/'covariances.json',
               calibration=Path(calibration_path),table=Path(table_path),regions=Path(regions_path))
    contents={name:path.read_bytes() for name,path in paths.items()};data={name:json.loads(value) for name,value in contents.items()}
    field=data['field'];cal=data['calibration'];table=data['table'];config=data['regions']
    if not field.get('foa_assumption_enabled'):raise ValueError('Explicit experimental FOA field required')
    g=render_geometry(field,cal)
    if table['world_id']!=g['world_id'] or config['world_id']!=g['world_id']:raise ValueError('Worlds differ')
    if field.get('provenance',{}).get('calibration_sha256')!=hashlib.sha256(contents['calibration']).hexdigest():
        raise ValueError('Field/calibration hashes differ; rebuild spatial field')
    bins,rejected=aggregate_spatial_bins(field,data['covariances'],g['raw_to_render_rotation_rows'],g['raw_to_render_translation_m'],.02)
    if len(bins)<12:raise ValueError('At least twelve occupied spatial cells required')
    positions=np.array([b['position_m'] for b in bins]);covariance=np.array([b['covariance'] for b in bins]);reference=float(np.median(covariance[:,0,0].real));observed=covariance/reference
    bandwidth=field['band_hz'][1]-field['band_hz'][0];weights=np.array([min(1,bandwidth*b['sum_duration_seconds']) for b in bins])
    world_basis=np.array(g['basis_world_columns']);plane_pose=table['table_world_pose'];normal=world_basis@rotate(plane_pose['quaternion_wxyz'],[0,0,1]);point=world_basis@(np.array(plane_pose['position_m'])-g['origin_world_m']);offset=float(normal@point)
    regions=config['regions'];points=[];ownership=[]
    for region_index,region in enumerate(regions):
        for delta in region['basis_offsets_m']:
            points.append((np.array(region['center_m'])+delta).tolist());ownership.append(region_index)
    frequencies=np.linspace(field['band_hz'][0],field['band_hz'][1],4);frequencies=(frequencies[:-1]+frequencies[1:])/2
    holdout=np.arange(len(bins))%4==0;train=~holdout;trials=[];best=None
    for reflection in (0.,.5,1.):
        h=transfer_operator(positions,points,frequencies,normal,offset,reflection)
        operator_scale=float(np.sqrt(np.mean(abs(h[:,:,0,:])**2)));h/=operator_scale
        spectra,fit=fit_covariances(h[train],observed[train],weights[train],iterations)
        predicted=prediction(h,spectra);record=dict(reflection_real_coefficient=reflection,train_relative_covariance_error=relative_error(predicted[train],observed[train],weights[train]),
            spatial_holdout_relative_covariance_error=relative_error(predicted[holdout],observed[holdout],weights[holdout]),fit=fit,operator_normalization=operator_scale)
        trials.append(record)
        # Select using training objective only. Holdout is not used to choose reflection.
        if best is None or fit['objective']<best[0]:best=(fit['objective'],h,spectra,predicted,record)
    _,h,spectra,predicted,chosen=best
    diagonal,diagonal_fit=fit_covariances(h[train],observed[train],weights[train],iterations,diagonal_only=True)
    diagonal_prediction=prediction(h,diagonal)
    region_summary=[]
    for i,region in enumerate(regions):
        indices=np.flatnonzero(np.array(ownership)==i)
        region_summary.append(dict(name=region['name'],center_m=region['center_m'],size_m=region['size_m'],
            placement_basis=region['placement_basis'],basis_indices=indices.tolist(),
            latent_basis_diagonal_sum_by_quadrature=[float(np.trace(s[np.ix_(indices,indices)]).real) for s in spectra],
            interpretation='Equivalent basis coefficient statistic, not driver FR, power or independently identified contribution'))
    hashes={name+'_sha256':hashlib.sha256(value).hexdigest() for name,value in contents.items()}
    if any(paths[name].read_bytes()!=value for name,value in contents.items()):raise ValueError('Input changed during model fit')
    result=dict(kind='experimental_coherent_multiregion_transfer_fit',world_id=g['world_id'],coordinate_frame='palm_render',render_geometry=g,
        physical_basis_version='palm-horizontal-intersection-v1',regions=region_summary,source_regions_measured=False,
        table_plane=dict(unit_normal=normal.tolist(),offset_m=offset),quadrature_frequencies_hz=frequencies.tolist(),
        frequency_quadrature_is_not_individually_measured_FR=True,
        source_covariances=[encode_covariance(s) for s in spectra],region_ownership=ownership,
        selected_model=chosen,trials=trials,diagonal_power_only_comparison=dict(fit=diagonal_fit,
            train_relative_covariance_error=relative_error(diagonal_prediction[train],observed[train],weights[train]),
            spatial_holdout_relative_covariance_error=relative_error(diagonal_prediction[holdout],observed[holdout],weights[holdout])),
        diagnostics=dict(spatial_cells=len(bins),training_cells=int(train.sum()),holdout_cells=int(holdout.sum()),
            rejected_inputs=rejected,reference_energy_adc2=reference,minimum_PSD_eigenvalue=float(np.linalg.eigvalsh(spectra).min()),
            latent_coherence_dominant_fractions=[float(np.linalg.eigvalsh(s)[-1]/max(np.trace(s).real,1e-30)) for s in spectra]),
        source_FR_known=False,physical_source_identification_verified=False,receiver_response_verified=False,
        actual_intensity_or_power_calibrated=False,provenance=hashes,
        assumptions=['Four source regions follow user layout; exact outlet coordinates are nominal priors, not measurements.',
            'Multiple coherent equivalent monopole basis points represent unknown radiation; no cardioid driver pattern is imposed.',
            'Free-space Green functions plus a coherent infinite-plane table image are a limited propagation approximation.',
            'The first-order receiver manifold is uncalibrated; actual H3-VR radial response and chassis diffraction are absent.',
            'Source covariance is PSD with cross terms; band-integrated data cannot identify individual driver FR or unique phase curves.',
            'Spatial-cell holdout is internal model checking, not independent listening or source-position validation.'])
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as target:json.dump(result,target,ensure_ascii=False,indent=2,allow_nan=False)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('field_directory');p.add_argument('calibration');p.add_argument('table_reference');p.add_argument('regions');p.add_argument('output');p.add_argument('--iterations',type=int,default=300);a=p.parse_args()
    result=run(a.field_directory,a.calibration,a.table_reference,a.regions,a.output,a.iterations)
    print(json.dumps(dict(selected_model=result['selected_model'],diagonal_power_only_comparison=result['diagonal_power_only_comparison'],diagnostics=result['diagnostics']),indent=2))


if __name__=='__main__':main()
