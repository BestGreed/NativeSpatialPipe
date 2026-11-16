"""Offline static-rotation comparison; never upgrades FOA semantics or timing."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .field_model import microphone_rotation_screen,rotation_matrix
from .field_viewer import render_geometry
from .source_inversion import decode_covariance,transform_covariance,covariance_metrics

ROOT=Path(__file__).resolve().parents[3]


def angle(a,b):
    a=np.asarray(a,dtype=float);b=np.asarray(b,dtype=float)
    return float(np.degrees(np.arccos(np.clip(a@b/(np.linalg.norm(a)*np.linalg.norm(b)),-1,1))))


def compare_records(a,b):
    relative=np.asarray(b['Rmic_palm'])@np.asarray(a['Rmic_palm']).T
    return dict(first=a['name'],second=b['name'],
        center_displacement_m=float(np.linalg.norm(np.asarray(b['position_palm_m'])-a['position_palm_m'])),
        microphone_rotation_degrees=float(np.degrees(np.arccos(np.clip((np.trace(relative)-1)/2,-1,1)))),
        global_direction_difference_degrees=angle(a['beam_direction_palm'],b['beam_direction_palm']),
        local_direction_difference_degrees=angle(a['beam_direction_mic'],b['beam_direction_mic']),
        W_level_change_db=float(20*np.log10(b['W_rms']/a['W_rms'])),
        channel_semantics_automatically_verified=False,precision_timing_automatically_verified=False)


def axis_gain_candidate(records):
    """One exploratory Y/X scale from first two poses; all later poses held out.

    Assumes unchanged pressure/velocity field at the acoustic centre. Translation
    or mic/room effects can violate this. Z scale and absolute sensitivity are
    not identified by this experiment. Never applies the candidate to data.
    """
    if len(records)<2:return None
    rows=[]
    for record in records:
        r=np.asarray(record['Rmic_palm']);v=np.asarray(record['normalized_pressure_cross_local'])
        rows.append((r[:,1]*v[1],r[:,0]*v[0]+r[:,2]*v[2]))
    a=rows[0][0]-rows[1][0];b=rows[0][1]-rows[1][1]
    if a@a<1e-8:return dict(identifiable=False,reason='rotation/response does not constrain relative Y scale')
    gain=float(-(a@b)/(a@a))
    if not .25<=gain<=4:return dict(identifiable=False,reason='candidate outside declared exploratory range .25..4')
    directions=[x*gain+y for x,y in rows]
    return dict(identifiable=True,experimental_only=True,channel_correction_xyz=[1,gain,1],
        training_indices=[0,1],heldout_indices=list(range(2,len(records))),
        corrected_cross_direction_difference_degrees=[angle(directions[0],d) for d in directions[1:]],
        uncorrected_cross_direction_difference_degrees=[angle(rows[0][0]+rows[0][1],x+y) for x,y in rows[1:]],
        applied_to_measurement_or_inversion=False,absolute_or_vertical_calibration_verified=False,
        warning='Exploratory rotation-equivariance fit; nonzero translation, near-field/mic response and reflection confounds remain.')


def summarize(field_directory,calibration):
    folder=Path(field_directory).resolve()
    load=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    field=load(folder/'field.json');full=load(folder/'covariances.json')
    g=render_geometry(field,calibration);r=np.asarray(g['raw_to_render_rotation_rows']);t=np.asarray(g['raw_to_render_translation_m'])
    if not field.get('foa_assumption_enabled'):raise ValueError('Explicit experimental FOA field required')
    def source_path(value):
        path=Path(value)
        return path if path.is_absolute() else ROOT/path
    scan_path=source_path(field['provenance']['scan_analysis']);scan=load(scan_path)
    capture=source_path(field['provenance']['capture']);start=scan['selection']['raw_started_lsl']+.2;end=scan['selection']['raw_finished_lsl']-.2
    poses=[]
    for line in (capture/'events.jsonl').open(encoding='utf-8'):
        e=json.loads(line)
        if e.get('kind')=='pose' and e.get('serial')==scan['serial'] and start<e['arrival_lsl']<end:poses.append(e)
    if len(poses)<30 or any(not p.get('valid') for p in poses) or len({p['epoch'] for p in poses})!=1:
        raise ValueError('Insufficient/invalid/reconnected comparison poses')
    if max(b['arrival_lsl']-a['arrival_lsl'] for a,b in zip(poses,poses[1:]))>.025:raise ValueError('Pose gap during comparison')
    qs=np.asarray([p['quaternion_wxyz'] for p in poses]);qs[qs@qs[0]<0]*=-1
    q=qs.mean(0);q/=np.linalg.norm(q)
    Rmic=r@rotation_matrix(microphone_rotation_screen(q,calibration['screen_world_pose'],calibration['probe']))
    details={d['window_index']:d for d in full['point_motion']};matrices=dict(full['points'])
    points=[dict(zip(field['points']['columns'],row)) for row in field['points']['rows']]
    weights=np.array([details[p['window_index']]['frames']*details[p['window_index']]['motion_quality'] for p in points]);weights/=weights.sum()
    c=sum(w*decode_covariance(matrices[p['window_index']]) for w,p in zip(weights,points));c=transform_covariance(c,r)
    positions=np.array([[p[k] for k in ('x_m','y_m','z_m')] for p in points]);position=r@(weights@positions)+t
    metrics=covariance_metrics(c);tracked=np.array([p['position_m'] for p in poses])
    return dict(name=capture.name,world_id=g['world_id'],position_palm_m=position.tolist(),Rmic_palm=Rmic.tolist(),
        W_rms=float(np.sqrt(c[0,0].real)),beam_direction_palm=metrics['maximum_direction'],
        beam_direction_mic=(Rmic.T@np.array(metrics['maximum_direction'])).tolist(),metrics=metrics,
        normalized_pressure_cross_local=(Rmic.T@c[1:,0].real/c[0,0].real).tolist(),
        maximum_static_tracker_deviation_m=float(np.linalg.norm(tracked-tracked.mean(0),axis=1).max()),
        maximum_static_orientation_deviation_degrees=max(min(angle(q,p['quaternion_wxyz']),angle(q,-np.asarray(p['quaternion_wxyz']))) for p in poses)*2,
        source_capture=str(capture),source_field=str(folder),
        field_sha256=hashlib.sha256((folder/'field.json').read_bytes()).hexdigest(),
        covariance_sha256=hashlib.sha256((folder/'covariances.json').read_bytes()).hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fields',nargs='+');parser.add_argument('--calibration',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();cal=json.loads(Path(args.calibration).read_text(encoding='utf-8'))
    records=[summarize(path,cal) for path in args.fields]
    result=dict(kind='experimental_static_rotation_comparison',records=records,
        comparisons=[compare_records(records[0],r) for r in records[1:]],
        axis_gain_candidate=axis_gain_candidate(records),
        channel_semantics_verified=False,normalization_verified=False,synchronization_verified=False,
        note='Static direction repeatability only; source geometry, translation, mic response and reflections remain confounded. No automatic calibration upgrade.')
    with Path(args.output).open('x',encoding='utf-8') as target:json.dump(result,target,indent=2,ensure_ascii=False,allow_nan=False)
    print(json.dumps(result['comparisons'],indent=2))


if __name__=='__main__':main()
