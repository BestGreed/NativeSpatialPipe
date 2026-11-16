"""Experimental local 3D band-energy/FOA covariance field; offline only.

One audio window is one moving microphone location, never four spatial points.
No coherent phase relation or Helmholtz reconstruction is inferred across points.
"""
import argparse
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
import json
import hashlib
import math
from pathlib import Path

import numpy as np

from .core import multiply,rotate,unit
from .scan_analysis import BAND_HZ,B_SERIAL,valid_pose
from .session import unpack24

POINT_COLUMNS=['window_index','x_m','y_m','z_m','energy_adc2',
    'proxy_re_x_adc2','proxy_re_y_adc2','proxy_re_z_adc2',
    'proxy_im_x_adc2','proxy_im_y_adc2','proxy_im_z_adc2','motion_diameter_m','rotation_variation_deg','support_eligible',
    'source_window_index','duration_seconds','motion_quality','start_sample','frames','segment_count','cell_i','cell_j','cell_k']
VOXEL_COLUMNS=['flat_index','energy_adc2','proxy_re_x_adc2','proxy_re_y_adc2','proxy_re_z_adc2',
    'proxy_im_x_adc2','proxy_im_y_adc2','proxy_im_z_adc2','nearest_distance_m','spatial_bin_count','geometry_rank','energy_variance_adc4']


def conjugate(q):
    q=unit(q)
    return [q[0],-q[1],-q[2],-q[3]]


def rotation_matrix(q):
    """Right-handed matrix; columns are the rotated local basis axes."""
    return np.column_stack([rotate(q,axis) for axis in ([1,0,0],[0,1,0],[0,0,1])])


def slerp(a,b,fraction):
    a=np.asarray(unit(a)); b=np.asarray(unit(b)); dot=float(a@b)
    if dot<0:b=-b;dot=-dot
    dot=min(1.,max(-1.,dot))
    if dot>.9995:
        q=(1-fraction)*a+fraction*b
        return (q/np.linalg.norm(q)).tolist()
    theta=math.acos(dot)
    return (math.sin((1-fraction)*theta)/math.sin(theta)*a+
            math.sin(fraction*theta)/math.sin(theta)*b).tolist()


def acoustic_center_screen(position,tracker_q,screen_pose,offset=(0,0,.10673)):
    world=np.asarray(position)+np.asarray(rotate(tracker_q,offset))
    return rotate(conjugate(screen_pose['quaternion_wxyz']),
                  (world-np.asarray(screen_pose['position_m'])).tolist())


def microphone_rotation_screen(tracker_q,screen_pose,probe):
    axis=np.asarray(probe['common_axis_tracker'],dtype=float)
    norm=np.linalg.norm(axis)
    if norm<=0 or not np.isfinite(axis).all():raise ValueError('Invalid common axis')
    theta=probe['common_axis_angle_radians']
    if not math.isfinite(theta):raise ValueError('Missing finite heading angle')
    qaxis=[math.cos(theta/2),*(axis/norm*math.sin(theta/2))]
    return unit(multiply(conjugate(screen_pose['quaternion_wxyz']),
        multiply(unit(tracker_q),multiply(qaxis,unit(probe['known_rotation_wxyz'])))))


def rotate_foa(samples,rotation):
    """Assumed AmbiX W,Y,Z,X -> b=W,X,Y,Z; W invariant under rotation."""
    samples=np.asarray(samples)
    if samples.ndim!=2 or samples.shape[1]!=4:raise ValueError('FOA needs frames x 4 coincident channels')
    b=samples[:,[0,3,1,2]].astype(np.result_type(samples.dtype,np.float64),copy=True)
    b[:,1:]=b[:,1:]@np.asarray(rotation).T
    return b


def covariance(b,analytic=True):
    """C_ij = .5 E[b_i conj(b_j)] for analytic signals; C00 is RMS^2."""
    b=np.asarray(b,dtype=np.complex128)
    if b.ndim!=2 or b.shape[1]!=4 or not len(b) or not np.isfinite(b).all():
        raise ValueError('Covariance requires finite frames x 4 data')
    c=(b.T@b.conj())/len(b)*(.5 if analytic else 1.)
    return (c+c.conj().T)/2


def analytic_band(samples,rate=48000):
    """Whole padded segment FFT band filter; output complex analytic channels."""
    x=np.asarray(samples)
    if x.ndim!=2 or x.shape[1]!=4 or not np.isfinite(x).all():raise ValueError('Expected finite four-channel samples')
    n=len(x)
    if n<2:raise ValueError('Insufficient samples for band filtering')
    frequencies=np.fft.rfftfreq(n,1/rate)
    selected=np.where((frequencies>=BAND_HZ[0])&(frequencies<=BAND_HZ[1]))[0]
    if not len(selected):raise ValueError('Segment has no frequency bins in target band')
    output=np.empty((n,4),dtype=np.complex64)
    for channel in range(4):
        spectrum=np.fft.rfft(x[:,channel].astype(np.float64))
        analytic_spectrum=np.zeros(n,dtype=np.complex128)
        analytic_spectrum[selected]=2*spectrum[selected]
        output[:,channel]=np.fft.ifft(analytic_spectrum).astype(np.complex64)
    return output


class PoseInterpolator:
    """Interpolates only valid bracketing poses of one epoch with a bounded gap."""
    def __init__(self,events,serial=B_SERIAL,max_gap=.025):
        self.poses=[e for e in events if e.get('kind')=='pose' and e.get('serial')==serial]
        self.poses.sort(key=lambda e:e['arrival_lsl'])
        self.times=[e['arrival_lsl'] for e in self.poses]
        self.max_gap=max_gap

    def check_interval(self,start,end):
        """Inspect every raw callback, including invalid ones between subframes."""
        lo=max(0,bisect_left(self.times,start)-1)
        hi=min(len(self.poses),bisect_right(self.times,end)+1)
        selected=self.poses[lo:hi]
        if not selected or start<self.times[0] or end>self.times[-1]:raise ValueError('unbracketed_pose')
        if any(not valid_pose(p) for p in selected):raise ValueError('invalid_pose')
        if len({p.get('epoch',0) for p in selected})!=1:raise ValueError('pose_epoch_boundary')
        if any(not 0<b['arrival_lsl']-a['arrival_lsl']<=self.max_gap for a,b in zip(selected,selected[1:])):
            raise ValueError('pose_gap')
        return [p for p in selected if start<=p['arrival_lsl']<=end]

    def at(self,stamp):
        i=bisect_left(self.times,stamp)
        if i<len(self.times) and abs(self.times[i]-stamp)<1e-10:
            pose=self.poses[i]
            if not valid_pose(pose):raise ValueError('invalid_pose')
            return list(pose['position_m']),unit(pose['quaternion_wxyz']),pose.get('epoch',0)
        if i==0 or i==len(self.poses):raise ValueError('unbracketed_pose')
        a,b=self.poses[i-1:i+1]
        dt=b['arrival_lsl']-a['arrival_lsl']
        if dt<=0 or dt>self.max_gap:raise ValueError('pose_gap')
        if a.get('epoch',0)!=b.get('epoch',0):raise ValueError('pose_epoch_boundary')
        if not valid_pose(a) or not valid_pose(b):raise ValueError('invalid_pose')
        fraction=(stamp-a['arrival_lsl'])/dt
        position=((1-fraction)*np.asarray(a['position_m'])+fraction*np.asarray(b['position_m'])).tolist()
        return position,slerp(a['quaternion_wxyz'],b['quaternion_wxyz'],fraction),a.get('epoch',0)


def motion_covariance(signal,start,frames,first_lsl,poses,screen_pose,probe,assume_ambix=False,step_frames=192):
    """Bandfilter first, then rotate XYZ in <=4ms pose-SLERP subframes."""
    if not 1<=step_frames<=192:raise ValueError('Rotation step must be <=4 ms at 48 kHz')
    values=np.asarray(signal[start:start+frames])
    if len(values)!=frames:raise ValueError('Signal window extends beyond filter segment')
    output=np.empty((frames,4),dtype=np.complex64)
    centers=[]; quaternions=[]; epochs=set()
    raw_poses=poses.check_interval(first_lsl,first_lsl+(frames-1)/48000)
    for pose in raw_poses:
        centers.append(acoustic_center_screen(pose['position_m'],pose['quaternion_wxyz'],screen_pose,probe['known_offset_tracker_m']))
        quaternions.append(microphone_rotation_screen(pose['quaternion_wxyz'],screen_pose,probe))
        epochs.add(pose.get('epoch',0))
    # Check both edges too: no unobserved gap may hide outside subframe centres.
    for local in (0,frames-1):
        p,q,epoch=poses.at(first_lsl+local/48000)
        epochs.add(epoch);centers.append(acoustic_center_screen(p,q,screen_pose,probe['known_offset_tracker_m']))
        quaternions.append(microphone_rotation_screen(q,screen_pose,probe))
    for lo in range(0,frames,step_frames):
        hi=min(frames,lo+step_frames)
        p,q,epoch=poses.at(first_lsl+(lo+(hi-lo-1)/2)/48000)
        epochs.add(epoch)
        mic_q=microphone_rotation_screen(q,screen_pose,probe)
        centers.append(acoustic_center_screen(p,q,screen_pose,probe['known_offset_tracker_m']))
        quaternions.append(mic_q)
        if assume_ambix:
            output[lo:hi]=rotate_foa(values[lo:hi],rotation_matrix(mic_q))
        else:
            output[lo:hi]=0
            output[lo:hi,0]=values[lo:hi,0]
    if len(epochs)!=1:raise ValueError('pose_epoch_boundary')
    positions=np.asarray(centers)
    # Bounding-box diagonal is a conservative footprint diameter proxy.
    footprint=float(np.linalg.norm(positions.max(axis=0)-positions.min(axis=0)))
    first=np.asarray(quaternions[0]); variation=max(math.degrees(2*math.acos(min(1.,abs(float(first@q))))) for q in quaternions)
    return covariance(output),dict(position_min_m=positions.min(axis=0).tolist(),position_max_m=positions.max(axis=0).tolist(),
        motion_diameter_m=footprint,rotation_variation_deg=variation,epoch=next(iter(epochs)),
        subframe_count=math.ceil(frames/step_frames),rotation_step_seconds=step_frames/48000)


def spatial_bins(positions,covariances,bin_size=.01,weights=None,durations=None,source_indices=None):
    """Equal spatial bins prevent repeated dwelling from increasing neighbours."""
    positions=np.asarray(positions,dtype=float); covariances=np.asarray(covariances,dtype=np.complex128)
    if bin_size<=0 or not np.isfinite(positions).all():raise ValueError('Invalid spatial bins')
    weights=np.ones(len(positions)) if weights is None else np.asarray(weights,dtype=float)
    if len(weights)!=len(positions) or not np.isfinite(weights).all() or np.any(weights<0):
        raise ValueError('Spatial-bin covariance weights must be finite and nonnegative')
    durations=None if durations is None else np.asarray(durations,dtype=float)
    if durations is not None and (len(durations)!=len(positions) or not np.isfinite(durations).all() or np.any(durations<=0)):
        raise ValueError('Durations must be finite and positive')
    if source_indices is not None and len(source_indices)!=len(positions):raise ValueError('Source parent ids do not match positions')
    groups=defaultdict(list)
    for i,key in enumerate(np.floor(positions/bin_size).astype(np.int64)):
        groups[tuple(int(v) for v in key)].append(i)
    groups={key:ix for key,ix in groups.items() if weights[ix].sum()>0}
    return dict(positions=np.array([np.average(positions[ix],axis=0,weights=weights[ix]) for ix in groups.values()]).reshape(-1,3),
                covariances=np.array([np.average(covariances[ix],axis=0,weights=weights[ix]) for ix in groups.values()]).reshape(-1,4,4),
                source_window_counts=[len(ix) for ix in groups.values()],keys=list(groups),bin_size_m=bin_size,
                weight_sums=[float(weights[ix].sum()) for ix in groups.values()],
                duration_sums=[float(durations[ix].sum()) for ix in groups.values()] if durations is not None else None,
                source_parent_counts=[len({source_indices[i] for i in ix}) for ix in groups.values()] if source_indices is not None else None)


def interpolation_bins(positions,covariances,motion_diameters,bin_size=.01,max_motion_diameter=.04,
                       durations=None,qualities=None,source_indices=None):
    """Keep all measured points separately, but large moving averages cannot support voxels."""
    motion=np.asarray(motion_diameters,dtype=float)
    if not math.isfinite(max_motion_diameter) or max_motion_diameter<=0 or not np.isfinite(motion).all():
        raise ValueError('Invalid motion support bound')
    eligible=(motion>=0)&(motion<=max_motion_diameter)
    duration_values=np.ones(len(motion)) if durations is None else np.asarray(durations,dtype=float)
    quality_values=np.ones(len(motion)) if qualities is None else np.asarray(qualities,dtype=float)
    if len(duration_values)!=len(motion) or len(quality_values)!=len(motion) or not np.isfinite(quality_values).all() or np.any(quality_values<0):
        raise ValueError('Invalid duration/quality support weights')
    weights=duration_values*quality_values
    selected_sources=None if source_indices is None else np.asarray(source_indices)[eligible].tolist()
    result=spatial_bins(np.asarray(positions)[eligible],np.asarray(covariances)[eligible],bin_size,weights[eligible],
        duration_values[eligible] if durations is not None else None,selected_sources)
    return dict(result,eligible_mask=eligible,excluded_motion_count=int((~eligible).sum()),
                max_motion_diameter_m=max_motion_diameter)


def adaptive_window_covariances(signal,start,frames,first_lsl,poses,screen_pose,probe,assume_ambix=False,
                                step_frames=192,motion_target=.02,subwindow_seconds=.02,adaptive=True):
    """Validate the entire parent first; only then split high-motion windows.

    Analytic band filtering happened before this function, so a 20 ms child
    is a short covariance average, not a separate low-resolution FFT filter.
    """
    if not math.isfinite(motion_target) or motion_target<=0:raise ValueError('Motion target must be finite and positive')
    if not math.isfinite(subwindow_seconds) or not .02<=subwindow_seconds<=.05:
        raise ValueError('Adaptive covariance subwindow must be 20..50 ms')
    parent_c,parent_motion=motion_covariance(signal,start,frames,first_lsl,poses,screen_pose,probe,assume_ambix,step_frames)
    count=min(5,max(1,frames//round(subwindow_seconds*48000))) if adaptive and parent_motion['motion_diameter_m']>motion_target else 1
    boundaries=[round(i*frames/count) for i in range(count+1)]
    estimates=[]
    for child in range(count):
        lo,hi=boundaries[child:child+2]
        if count==1:c,motion=parent_c,dict(parent_motion)
        else:c,motion=motion_covariance(signal,start+lo,hi-lo,first_lsl+lo/48000,poses,screen_pose,probe,assume_ambix,step_frames)
        center_lsl=first_lsl+(lo+(hi-lo-1)/2)/48000
        p,q,_=poses.at(center_lsl)
        position=acoustic_center_screen(p,q,screen_pose,probe['known_offset_tracker_m'])
        diameter=motion['motion_diameter_m']
        estimates.append(dict(local_start_frame=lo,frames=hi-lo,duration_seconds=(hi-lo)/48000,
            center_lsl=center_lsl,position_m=position,covariance=c,motion=motion,
            motion_quality=1/(1+(diameter/motion_target)**2)))
    return dict(parent_covariance=parent_c,parent_motion=parent_motion,split_count=count,estimates=estimates)


def accumulate_spatial(signal,start,frames,first_lsl,poses,screen,probe,accumulators,
                       source_index,absolute_start,assume_ambix=False,cell_size=.02,step_frames=192,cell_transform=None):
    """Assign every valid sample to a spatial cell, never a time-window gate.

    Acoustic position interpolates block endpoints per sample; rotation uses the
    <=4ms midpoint. Endpoint-linear position error is estimated at the midpoint.
    Caller validates the full original parent before this function mutates bins.
    """
    if not math.isfinite(cell_size) or cell_size<=0 or not 1<=step_frames<=192:
        raise ValueError('Invalid spatial integration cell/step')
    poses.check_interval(first_lsl,first_lsl+(frames-1)/48000)
    for lo in range(0,frames,step_frames):
        hi=min(frames,lo+step_frames); n=hi-lo
        t0=first_lsl+lo/48000; t1=first_lsl+(hi-1)/48000
        pa,qa,_=poses.at(t0);pb,qb,_=poses.at(t1);pm,qm,_=poses.at((t0+t1)/2)
        a=np.asarray(acoustic_center_screen(pa,qa,screen,probe['known_offset_tracker_m']))
        b=np.asarray(acoustic_center_screen(pb,qb,screen,probe['known_offset_tracker_m']))
        mid=np.asarray(acoustic_center_screen(pm,qm,screen,probe['known_offset_tracker_m']))
        positions=a[None,:]+np.linspace(0,1,n)[:,None]*(b-a)[None,:]
        error=float(np.linalg.norm(mid-(a+b)/2))
        diameter=float(np.linalg.norm(b-a));quality=1/(1+(diameter/cell_size)**2)
        values=signal[start+lo:start+hi]
        if len(values)!=n:raise ValueError('Signal window extends beyond filter segment')
        rotated=rotate_foa(values,rotation_matrix(microphone_rotation_screen(qm,screen,probe))) if assume_ambix else np.column_stack((values[:,0],np.zeros((n,3))))
        cell_positions=positions if cell_transform is None else positions@np.asarray(cell_transform['rotation']).T+np.asarray(cell_transform['translation'])
        keys=np.floor(cell_positions/cell_size).astype(np.int64)
        boundaries=[0]+(np.flatnonzero(np.any(keys[1:]!=keys[:-1],axis=1))+1).tolist()+[n]
        for left,right in zip(boundaries,boundaries[1:]):
            key=tuple(int(v) for v in keys[left]); count=right-left;weight=count*quality
            if key not in accumulators:
                accumulators[key]=dict(frames=0,weight=0.,position=np.zeros(3),matrix=np.zeros((4,4),complex),
                    minimum=np.full(3,np.inf),maximum=np.full(3,-np.inf),segments=[],source_indices=set(),position_error=0.)
            cell=accumulators[key];v=positions[left:right]
            cell['frames']+=count;cell['weight']+=weight;cell['position']+=v.sum(axis=0)*quality
            cell['matrix']+=covariance(rotated[left:right])*weight
            cell['minimum']=np.minimum(cell['minimum'],v.min(axis=0));cell['maximum']=np.maximum(cell['maximum'],v.max(axis=0))
            cell['source_indices'].add(source_index);cell['position_error']=max(cell['position_error'],error)
            sample=absolute_start+lo+left
            if cell['segments'] and cell['segments'][-1]['start_sample']+cell['segments'][-1]['frames']==sample:
                cell['segments'][-1]['frames']+=count
            else:cell['segments'].append(dict(start_sample=sample,frames=count))
    return accumulators


def local_gaussian(position,bins,radius=.04,min_bins=3):
    """Nonnegative Gaussian weights average linear PSD matrices, never dB."""
    points=np.asarray(bins['positions']); matrices=np.asarray(bins['covariances'])
    distances=np.linalg.norm(points-np.asarray(position),axis=1)
    selected=distances<=radius
    count=int(selected.sum())
    nearest=float(distances.min()) if len(distances) else None
    result=dict(known=count>=min_bins,spatial_bin_count=count,nearest_distance_m=nearest)
    if count<min_bins:return result
    d=distances[selected]; weights=np.exp(-.5*(d/(radius/2))**2);weights/=weights.sum()
    c=np.tensordot(weights,matrices[selected],axes=(0,0)); c=(c+c.conj().T)/2
    p=points[selected]; center=np.sum(p*weights[:,None],axis=0);delta=p-center
    geometry=(delta*weights[:,None]).T@delta
    eigenvalues=np.linalg.eigvalsh(geometry)
    rank=int(np.sum(eigenvalues>max(float(eigenvalues.max())*1e-3,1e-12)))
    energy=matrices[selected,0,0].real
    variance=float(np.sum(weights*(energy-c[0,0].real)**2))
    return dict(result,covariance=c,geometry_rank=rank,energy_variance_adc4=variance)


def _compact(value):
    if value is None:return None
    if isinstance(value,(int,np.integer)):return int(value)
    value=float(value)
    if not math.isfinite(value):raise ValueError('Nonfinite model output')
    return float(format(value,'.5g'))


def _proxies(c,enabled):
    return [float(v) for v in c[1:,0].real]+[float(v) for v in c[1:,0].imag] if enabled else [None]*6


def _complex_matrix(c):
    return [[[float(z.real),float(z.imag)] for z in row] for row in c]


def _read_filter_segment(capture,events,start,end):
    audio=[e for e in events if e.get('kind')=='audio_block']
    selected=[b for b in audio if b['sample_start_i64']<end and b['sample_start_i64']+b['frames']>start]
    pieces=[]; cursor=start;previous=None
    with (capture/'audio.pcm').open('rb') as source:
        for block in selected:
            if block.get('flags') or block.get('device_gap_frames') or block.get('discontinuity') or block.get('timestamp_error'):
                raise ValueError('Audio flags/gap inside FFT filter segment; do not bridge')
            if block.get('rate')!=48000 or block.get('channels')!=4 or block['container_bits'] not in (24,32):
                raise ValueError('Expected fixed 4ch/48k PCM24 data')
            if previous and (block.get('epoch',0)!=previous.get('epoch',0) or
                block['sample_start_i64']!=previous['sample_start_i64']+previous['frames'] or
                block.get('device_position_u64',0)!=previous.get('device_position_u64',0)+previous['frames']):
                raise ValueError('Audio epoch/device/logical gap inside filter segment')
            lo=max(start,block['sample_start_i64']);hi=min(end,block['sample_start_i64']+block['frames'])
            if lo!=cursor:raise ValueError('Missing logical audio inside filter segment')
            stride=4*(block['container_bits']//8)
            source.seek(block['pcm_byte_offset']+(lo-block['sample_start_i64'])*stride)
            raw=source.read((hi-lo)*stride)
            if len(raw)!=(hi-lo)*stride:raise ValueError('Truncated PCM')
            pieces.append(unpack24(raw,block['container_bits']));cursor=hi;previous=block
    if cursor!=end:raise ValueError('Incomplete FFT filter segment')
    return np.concatenate(pieces)


def build_field(capture,calibration,scan_analysis,output,assume_ambix=False,bin_size=.01,spacing=.015,
                radius=.04,min_bins=3,rotation_step_frames=192,padding_seconds=.5,max_motion_diameter=None,
                motion_target=.02,subwindow_seconds=.02,adaptive=True,max_json_bytes=1100000,mode='spatial',spatial_cell_size=.02):
    if mode not in ('adaptive','spatial'):raise ValueError('Unknown field mode')
    if mode=='spatial':bin_size=spatial_cell_size
    capture=Path(capture).resolve();output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    if any((output/name).exists() for name in ('field.json','covariances.json')):
        raise FileExistsError('field.json/covariances.json already exists; do not overwrite')
    def load(value):return json.loads(Path(value).read_text(encoding='utf-8')) if isinstance(value,(str,Path)) else value
    cal=load(calibration);analysis=load(scan_analysis)
    metadata=json.loads((capture/'session.json').read_text(encoding='utf-8'));config=metadata['config']
    if analysis.get('mode')!='arrival-preview' or analysis.get('audio_time_basis')!='nominal-sampleclock':
        raise ValueError('This experimental model requires explicit arrival-preview + nominal-sampleclock input')
    if config.get('world_id')!=cal.get('world_id') or not config.get('world_id'):
        raise ValueError('Capture and calibration world_id differ')
    if analysis.get('source_session') and Path(analysis['source_session']).resolve()!=capture:
        raise ValueError('Scan analysis belongs to a different capture')
    if analysis.get('selection',{}).get('scope')!='selected_playback_interval':
        raise ValueError('Use playback-only scan analysis to exclude triplet and resting levels')
    rows=[(i,row) for i,row in enumerate(analysis['windows']) if row.get('accepted')]
    if not rows:raise ValueError('No accepted input windows')
    if not all(math.isfinite(v) and v>0 for v in (bin_size,spacing,radius)) or min_bins<3:
        raise ValueError('Positive grid sizes and at least three distinct spatial bins required')
    # Support geometry is independent of the display grid's voxel spacing.
    max_motion_diameter=radius if max_motion_diameter is None else max_motion_diameter
    if not math.isfinite(max_motion_diameter) or not 0<max_motion_diameter<=radius:
        raise ValueError('Motion support diameter bound must be positive and cannot exceed support radius')
    if not math.isfinite(padding_seconds) or not 0<=padding_seconds<=2:raise ValueError('Padding must be 0..2 seconds')
    screen=cal['screen_world_pose'];probe=cal['probe']
    offset=probe['known_offset_tracker_m']
    if len(offset)!=3 or not all(math.isfinite(v) for v in offset):raise ValueError('Invalid acoustic offset')
    events=[json.loads(line) for line in (capture/'events.jsonl').open(encoding='utf-8') if line.strip()]
    origins=[r['audio_center_lsl']-(r['start_sample']+(r['frames']-1)/2)/48000 for _,r in rows]
    if max(origins)-min(origins)>2/48000:raise ValueError('Input spans separate nominal audio anchors; do not bridge FFT segment')
    origin=float(np.median(origins));selection=analysis['selection']
    total=max(e['sample_start_i64']+e['frames'] for e in events if e.get('kind')=='audio_block')
    start=max(0,math.floor((selection['raw_started_lsl']-origin-padding_seconds)*48000))
    end=min(total,math.ceil((selection['raw_finished_lsl']-origin+padding_seconds)*48000))
    samples=_read_filter_segment(capture,events,start,end)
    clip_counts=np.sum((samples>=8388607)|(samples<=-8388608),axis=0).tolist()
    analytic=analytic_band(samples);del samples
    poses=PoseInterpolator(events,analysis.get('serial',B_SERIAL))
    point_rows=[];point_covariances=[];point_positions=[];point_details=[];parent_audit=[];rejected=Counter()
    split_parent_count=0;spatial_accumulators={};spatial_geometry=None;cell_transform=None
    if mode=='spatial':
        from .field_viewer import render_geometry
        spatial_geometry=render_geometry(dict(provenance=dict(world_id=config['world_id'])),cal)
        cell_transform=dict(rotation=spatial_geometry['raw_to_render_rotation_rows'],translation=spatial_geometry['raw_to_render_translation_m'])
    for index,row in rows:
        local=row['start_sample']-start
        if mode=='spatial':
            try:
                c,motion=motion_covariance(analytic,local,row['frames'],origin+row['start_sample']/48000,
                    poses,screen,probe,assume_ambix,rotation_step_frames)
                accumulate_spatial(analytic,local,row['frames'],origin+row['start_sample']/48000,poses,screen,probe,
                    spatial_accumulators,index,row['start_sample'],assume_ambix,spatial_cell_size,rotation_step_frames,cell_transform)
                parent_audit.append(dict(source_window_index=index,start_sample=row['start_sample'],frames=row['frames'],
                    duration_seconds=row['frames']/48000,status='spatial_integrated',covariance=_complex_matrix(c),**motion))
            except ValueError as exc:
                rejected[str(exc)]+=1
                parent_audit.append(dict(source_window_index=index,status='rejected_before_spatial_integration',reason=str(exc)))
            continue
        try:
            estimated=adaptive_window_covariances(analytic,local,row['frames'],origin+row['start_sample']/48000,
                poses,screen,probe,assume_ambix,rotation_step_frames,motion_target,subwindow_seconds,adaptive)
        except ValueError as exc:
            rejected[str(exc)]+=1
            parent_audit.append(dict(source_window_index=index,start_sample=row['start_sample'],frames=row['frames'],
                duration_seconds=row['frames']/48000,status='rejected_before_split',reason=str(exc)))
            continue
        split_parent_count+=int(estimated['split_count']>1)
        center_p,center_q,_=poses.at(row['audio_center_lsl'])
        parent_position=acoustic_center_screen(center_p,center_q,screen,probe['known_offset_tracker_m'])
        disagreement=float(np.linalg.norm(np.asarray(parent_position)-np.asarray(row['acoustic_center_position_m'])))
        parent_audit.append(dict(source_window_index=index,start_sample=row['start_sample'],frames=row['frames'],
            duration_seconds=row['frames']/48000,status='split' if estimated['split_count']>1 else 'kept',
            child_count=estimated['split_count'],covariance=_complex_matrix(estimated['parent_covariance']),
            center_position_m=parent_position,preview_center_disagreement_m=disagreement,**estimated['parent_motion']))
        for child in estimated['estimates']:
            c=child['covariance'];motion=child['motion'];position=child['position_m'];point_id=len(point_rows)
            point_positions.append(position);point_covariances.append(c)
            point_rows.append([point_id,*position,float(c[0,0].real),*_proxies(c,assume_ambix),
                               motion['motion_diameter_m'],motion['rotation_variation_deg']])
            point_details.append(dict(window_index=point_id,source_window_index=index,
                start_sample=row['start_sample']+child['local_start_frame'],frames=child['frames'],
                duration_seconds=child['duration_seconds'],motion_quality=child['motion_quality'],
                parent_frames=row['frames'],parent_start_sample=row['start_sample'],parent_child_count=estimated['split_count'],
                source_parent_center_difference_m=float(np.linalg.norm(np.asarray(position)-parent_position)),
                preview_center_disagreement_m=disagreement,**motion))
    if mode=='spatial':
        for key,cell in spatial_accumulators.items():
            i=len(point_rows);p=cell['position']/cell['weight'];c=cell['matrix']/cell['weight']
            diameter=float(np.linalg.norm(cell['maximum']-cell['minimum']))
            point_positions.append(p.tolist());point_covariances.append(c)
            point_rows.append([i,*p.tolist(),float(c[0,0].real),*_proxies(c,assume_ambix),diameter,None])
            point_details.append(dict(window_index=i,source_window_index=-1,source_indices=sorted(cell['source_indices']),
                spatial_cell=list(key),start_sample=None,frames=cell['frames'],duration_seconds=cell['frames']/48000,
                motion_quality=cell['weight']/cell['frames'],segments=cell['segments'],segment_count=len(cell['segments']),
                continuous_time_interval=False,position_min_m=cell['minimum'].tolist(),position_max_m=cell['maximum'].tolist(),
                motion_diameter_m=diameter,rotation_variation_deg=None,preview_center_disagreement_m=0.,
                maximum_endpoint_linear_midpoint_error_m=cell['position_error']))
    if not point_rows:raise ValueError('No windows have valid within-window bracketing poses')
    bins=interpolation_bins(point_positions,point_covariances,[row[11] for row in point_rows],bin_size,max_motion_diameter,
        [detail['duration_seconds'] for detail in point_details],[detail['motion_quality'] for detail in point_details],
        [detail['source_window_index'] for detail in point_details])
    if mode=='spatial':
        # Cell keys already belong to the physical palm frame. Never rebin their
        # raw-screen centroids, which would merge/split cells after rotation.
        eligible=np.ones(len(point_details),dtype=bool)
        bins=dict(positions=np.asarray(point_positions),covariances=np.asarray(point_covariances),
            eligible_mask=eligible,excluded_motion_count=0,max_motion_diameter_m=max_motion_diameter,
            keys=[tuple(d['spatial_cell']) for d in point_details],source_window_counts=[1]*len(point_details),
            duration_sums=[d['duration_seconds'] for d in point_details],
            weight_sums=[d['duration_seconds']*d['motion_quality'] for d in point_details],
            source_parent_counts=[len(d['source_indices']) for d in point_details],bin_size_m=spatial_cell_size)
    for row,detail,eligible in zip(point_rows,point_details,bins['eligible_mask']):
        row.extend([int(eligible),detail['source_window_index'],detail['duration_seconds'],detail['motion_quality'],
                    detail['start_sample'],detail['frames'],detail.get('segment_count',1),*detail.get('spatial_cell',[None,None,None])])
        detail['support_eligible']=bool(eligible)
        if not eligible:detail['support_exclusion']='motion_footprint_exceeds_independent_hard_bound'
    positions=bins['positions'];all_positions=np.asarray(point_positions)
    grid_origin=np.floor((all_positions.min(axis=0)-radius)/spacing)*spacing
    grid_last=np.ceil((all_positions.max(axis=0)+radius)/spacing)*spacing
    shape=np.rint((grid_last-grid_origin)/spacing).astype(int)+1
    if int(np.prod(shape))>250000:raise ValueError('Requested grid exceeds 250k cells; narrow region or explicitly change spacing')
    voxels=[];voxel_covariances=[]
    nx,ny,nz=[int(v) for v in shape]
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                position=grid_origin+spacing*np.array([i,j,k])
                value=local_gaussian(position,bins,radius,min_bins)
                if not value['known']:continue
                flat=i+nx*(j+ny*k);c=value['covariance']
                voxels.append([flat,float(c[0,0].real),*_proxies(c,assume_ambix),value['nearest_distance_m'],
                    value['spatial_bin_count'],value['geometry_rank'],value['energy_variance_adc4']])
                voxel_covariances.append([flat,_complex_matrix(c)])
    compact_points=[[_compact(v) for v in row] for row in point_rows]
    compact_voxels=[[_compact(v) for v in row] for row in voxels]
    def content_hash(value):
        data=Path(value).read_bytes() if isinstance(value,(str,Path)) else json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(data).hexdigest()
    field=dict(schema=1,sampling_mode=mode,spatial_cell_coordinate_frame='palm_render' if mode=='spatial' else None,
        spatial_cell_geometry=spatial_geometry,kind='experimental_local_band_field',coordinate_frame='screen',
        units=dict(position='m',energy='ADC_count^2',proxy='ADC_count^2',variance_proxy='ADC_count^4'),
        scalar_basis='USB_ch1 band energy; pressure W only under the explicit unverified AmbiX assumption',
        band_hz=list(BAND_HZ),analytic_covariance_scale=.5,
        covariance_definition='C_ij = 0.5 mean(b_i conjugate(b_j)); b=[W,X,Y,Z] under assumption',
        directional_experimental=True,foa_assumption_enabled=bool(assume_ambix),channel_semantics_verified=False,
        input_channel_order_assumed='W,Y,Z,X' if assume_ambix else None,normalization_assumed='SN3D' if assume_ambix else None,
        synchronized=False,acoustic_calibrated=False,absolute_phase_across_points=False,
        time_basis='arrival-preview + derived nominal sampleclock; no validated audio-pose synchronization',
        point_window_index_semantics='spatial cell id; disjoint occupancy segments in covariances.json, never connect these points as a temporal path' if mode=='spatial' else 'unique output point id; use start_sample/frames for time continuity and source_window_index for parent dependence',
        screen_reference=dict(width_m=.30155,height_m=.18847,dimensions_basis='nominal primary display dimensions; not independently measured',
            axes=dict(x='tracker-defined in-plane axis; physical screen horizontal unverified',
                      y='tracker-defined in-plane axis; physical screen vertical unverified',
                      z='screen outward normal'),
            physical_in_plane_heading_verified=False,
            opening_angle_deg=cal.get('plane_calibration',{}).get('opening_angle_degrees'),
            opening_angle_basis='calibration estimate, not independent mechanical measurement',
            world_from_screen=screen,reference_world_pose=cal.get('reference_world_pose')),
        grid=dict(origin_m=grid_origin.tolist(),spacing_m=spacing,shape=shape.tolist(),
            flatten='i + nx*(j + ny*k)',origin_is_voxel_center=True,mask_storage='missing_flat_indices_are_unknown',
            support_radius_m=radius,gaussian_sigma_m=radius/2,min_distinct_spatial_bins=min_bins,spatial_bin_size_m=bin_size),
        points=dict(columns=POINT_COLUMNS,rows=compact_points),voxels=dict(columns=VOXEL_COLUMNS,rows=compact_voxels),
        diagnostics=dict(input_accepted_windows=len(rows),field_point_count=len(point_rows),window_rejections=dict(rejected),
            original_scan_rejected_window_count=len(analysis['windows'])-len(rows),original_rejected_parents_reclaimed=0,
            source_parent_window_count=len(rows),adaptive_split_parent_count=split_parent_count,
            unsplit_parent_window_count=sum(a.get('status')=='kept' for a in parent_audit),
            adaptive_enabled=bool(adaptive and mode=='adaptive'),sampling_mode=mode,spatial_integration_cell_m=spatial_cell_size if mode=='spatial' else None,
            maximum_position_midpoint_approximation_error_m=max((d.get('maximum_endpoint_linear_midpoint_error_m',0.) for d in point_details),default=0.),adaptive_motion_target_m=motion_target,adaptive_subwindow_target_seconds=subwindow_seconds,
            source_accepted_duration_seconds=sum(row['frames']/48000 for _,row in rows),
            represented_duration_seconds=sum(d['duration_seconds'] for d in point_details),
            minimum_point_duration_seconds=min(d['duration_seconds'] for d in point_details),
            maximum_point_duration_seconds=max(d['duration_seconds'] for d in point_details),
            minimum_nominal_complex_time_bandwidth=min(d['duration_seconds'] for d in point_details)*(BAND_HZ[1]-BAND_HZ[0]),
            nominal_time_bandwidth_caveat='disjoint cell occupancy and correlated narrowband samples do not equal independent DOF' if mode=='spatial' else None,
            point_weighting='within bin duration_seconds * motion_quality; different spatial bins have equal total weight before distance kernel',
            spatial_bin_count=len(positions),source_windows_per_bin=bins['source_window_counts'],
            interpolation_eligible_window_count=int(bins['eligible_mask'].sum()),interpolation_excluded_motion_count=bins['excluded_motion_count'],
            max_interpolation_motion_diameter_m=max_motion_diameter,
            supported_voxels=len(voxels),total_grid_cells=int(np.prod(shape)),unknown_grid_cells=int(np.prod(shape))-len(voxels),
            filter_sample_range=[start,end],filter_duration_seconds=(end-start)/48000,padding_seconds=padding_seconds,
            fft_filter_input_clipping_counts_per_channel=clip_counts,rotation_step_seconds=rotation_step_frames/48000,
            maximum_motion_diameter_m=max(row[11] for row in point_rows),maximum_rotation_variation_deg=max((row[12] for row in point_rows if row[12] is not None),default=None),
            maximum_preview_center_disagreement_m=max(d['preview_center_disagreement_m'] for d in point_details),
            compact_numeric_significant_digits=5,geometry_rank_relative_eigenvalue_tolerance=.001),
        provenance=dict(capture=str(capture),calibration=str(calibration) if isinstance(calibration,(str,Path)) else 'provided_object',
            scan_analysis=str(scan_analysis) if isinstance(scan_analysis,(str,Path)) else 'provided_object',world_id=config['world_id'],
            calibration_sha256=content_hash(calibration),scan_analysis_sha256=content_hash(scan_analysis),
            events_sha256=content_hash(capture/'events.jsonl'),pcm_sha256=content_hash(capture/'audio.pcm'),
            offset_local_m=offset,playback_selection=selection,covariances_file='covariances.json'),
        limitations=['Channel order/SN3D and USB rotation behavior are not independently verified.',
            'Directional Re/Im(C_i0) are FOA correlation proxies, not calibrated acoustic intensity in W/m^2.',
            'Whole padded segment FFT bandfilter has finite/periodic boundary effects; clipping remains in the source filter segment and is reported.',
            'Pose SLERP uses host arrivals and bounded valid brackets; it does not validate fused-pose latency.',
            'Each point has a moving/rotating footprint; 4ms subframe rotations approximate within-subframe motion.',
            'Spatial mode directly integrates every accepted-parent sample into cells; cumulative occupancy may be disjoint. Four ms is an integration approximation, not an eligibility threshold.',
            'Spatial positions use block endpoint interpolation; midpoint discrepancy is diagnostic, not a guaranteed error bound. Rotation is block-midpoint constant.',
            'Original rejected scan parents remain excluded; their raw valid subsegments have not been independently revalidated.',
            'A spatial cell covariance mixes times, repeated passes and orientations after rotation, and is not an independent instantaneous observation.',
            'Adaptive children share their source recording and parent window; they are not extra independent samples. Short 20ms covariance averages have few time-bandwidth degrees of freedom.',
            'Nonnegative Gaussian averages act on linear covariance matrices, never dB; dwell bins are equally spatially counted.',
            'Local rank and energy variance are geometry/variation proxies, not calibrated confidence intervals.',
            'Uncovered cells are unknown; no coherent cross-point phase or Helmholtz reconstruction.'])
    encoded=json.dumps(field,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(encoded.encode('utf-8'))>max_json_bytes:
        raise ValueError('Compact field exceeds JSON size budget; explicitly change spacing/support or volume')
    full=dict(schema=1,definition=field['covariance_definition'],basis='screen FOA under explicit assumption' if assume_ambix else 'scalar C00 only; directional components not estimated',
        points=[[row[0],_complex_matrix(c)] for row,c in zip(point_rows,point_covariances)],
        point_motion=point_details,source_window_audit=parent_audit,
        spatial_bin_weight_summaries=[dict(key=list(key),point_count=count,sum_duration_seconds=duration,
            sum_duration_quality=weight,source_parent_count=parent_count)
            for key,count,duration,weight,parent_count in zip(bins['keys'],bins['source_window_counts'],bins['duration_sums'],bins['weight_sums'],bins['source_parent_counts'])],
        spatial_bins=[[list(key),position.tolist(),count,_complex_matrix(c)]
            for key,position,count,c in zip(bins['keys'],bins['positions'],bins['source_window_counts'],bins['covariances'])],
        voxels=voxel_covariances)
    with (output/'field.json').open('x',encoding='utf-8') as target:target.write(encoded)
    with (output/'covariances.json').open('x',encoding='utf-8') as target:json.dump(full,target,separators=(',',':'),allow_nan=False)
    return dict(field=str(output/'field.json'),covariances=str(output/'covariances.json'),
        field_json_bytes=len(encoded.encode('utf-8')),**field['diagnostics'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture');parser.add_argument('calibration');parser.add_argument('scan_analysis');parser.add_argument('output')
    parser.add_argument('--assume-ambix',action='store_true',help='Explicit experimental W,Y,Z,X/SN3D assumption; not independent verification')
    parser.add_argument('--mode',choices=['adaptive','spatial'],default='spatial')
    parser.add_argument('--spatial-cell-size',type=float,default=.02)
    parser.add_argument('--bin-size',type=float,default=.01);parser.add_argument('--spacing',type=float,default=.015)
    parser.add_argument('--radius',type=float,default=.04);parser.add_argument('--min-bins',type=int,default=3)
    parser.add_argument('--max-motion-diameter',type=float,help='Independent hard footprint limit; default support radius (not grid spacing)')
    parser.add_argument('--motion-target',type=float,default=.02,help='Independent footprint target for adaptive splitting and quality (metres)')
    parser.add_argument('--subwindow-seconds',type=float,default=.02,help='20..50ms covariance child target; filtering remains whole segment')
    parser.add_argument('--no-adaptive',action='store_true',help='Keep original parent windows; retain independent hard motion bound')
    args=parser.parse_args()
    print(json.dumps(build_field(args.capture,args.calibration,args.scan_analysis,args.output,args.assume_ambix,
        args.bin_size,args.spacing,args.radius,args.min_bins,max_motion_diameter=args.max_motion_diameter,
        motion_target=args.motion_target,subwindow_seconds=args.subwindow_seconds,adaptive=not args.no_adaptive,mode=args.mode,spatial_cell_size=args.spatial_cell_size),indent=2))


if __name__=='__main__':main()
