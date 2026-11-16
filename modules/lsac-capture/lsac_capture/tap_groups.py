"""Initial/end triplets in one continuous recording; audio/raw-IMU evidence only."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from .core import fit_taps
from .tap_triplet import analyze_recording,imu_gap_report


MARKERS=('initial_triplet_start','initial_triplet_end','ending_triplet_start','ending_triplet_end')


def group_windows(events,world_id=None):
    """Require one unambiguous, ordered marker quartet in this recording."""
    markers={label:[] for label in MARKERS}
    for event in events:
        if event.get('kind')=='operator_marker' and event.get('label') in markers:
            if world_id and event.get('world_id',world_id)!=world_id:
                raise ValueError('Triplet marker world_id differs from the recording')
            stamp=event.get('arrival_lsl')
            if not isinstance(stamp,(int,float)) or not math.isfinite(stamp):
                raise ValueError('Triplet marker time must be finite LSL')
            markers[event['label']].append(stamp)
    if any(len(markers[label])!=1 for label in MARKERS):
        raise ValueError('Exactly one of each initial/ending_triplet_start/end marker is required')
    stamps=[markers[label][0] for label in MARKERS]
    if any(b<=a for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Initial/end triplet marker windows must be increasing and disjoint')
    return dict(initial=dict(start_lsl=stamps[0],end_lsl=stamps[1]),
                ending=dict(start_lsl=stamps[2],end_lsl=stamps[3]))


def _analyze_windows(config,events,raw,windows,audio_threshold=40000.,imu_threshold=150.,tolerance=.004):
    if not math.isfinite(tolerance) or tolerance<=0: raise ValueError('Tolerance must be finite and positive')
    serial=config.get('alignment_tracker_serial','LHR-7712DB4C')
    imus=[e for e in events if e.get('kind')=='raw_imu' and e.get('serial')==serial and e.get('mask',3)&1]
    reports={}
    for label,window in windows.items():
        try:
            reports[label]=analyze_recording(config,events,raw,**window,
                audio_threshold=audio_threshold,imu_threshold=imu_threshold)
        except ValueError as exc:
            reports[label]=dict(status='invalid_triplet_window',selection=window,error=str(exc))
    result=dict(schema=1,tracker_serial=serial,world_id=config.get('world_id'),marker_labels=list(MARKERS),
                windows=windows,groups=reports,all_record_imu_timing=imu_gap_report(imus),
                status='triplet_group_validation_failed',audio_imu_only=True,
                fused_pose_timing_validated=False,acoustic_calibrated=False,
                limits=['Both groups use recording audio logical sample zero and first selected-tracker IMU tick as common origins.',
                        'Three IMU candidates per group must each have one unique audio partner; unmatched audio-only candidates are retained.',
                        'Whole-record IMU gaps are reported; they do not create cross-gap jerk events or silently invalidate clean boundary windows.',
                        'Affine raw-IMU/audio fit is provisional and does not validate fused pose timing, DAC timing or acoustic accuracy.'])
    if any(report.get('status')!='provisional_triplet_offset' for report in reports.values()):
        return result
    initial,ending=reports['initial'],reports['ending']
    if (initial['imu_origin_tick'],initial['imu_origin_epoch'],initial['audio_origin_lsl'],initial['selected_imu_epoch']) != \
       (ending['imu_origin_tick'],ending['imu_origin_epoch'],ending['audio_origin_lsl'],ending['selected_imu_epoch']):
        result['status']='incompatible_group_origins_or_epochs'
        return result
    pairs=[*initial['paired_taps'],*ending['paired_taps']]
    result['paired_taps']=pairs
    first_offset=initial['fit']['offset_seconds']
    residuals=[p['audio_seconds']-p['imu_seconds']-first_offset for p in ending['paired_taps']]
    result['held_out_group_validation']=dict(model='unit rate; median offset estimated only from initial three',
        initial_offset_seconds=first_offset,residuals_seconds=residuals,
        rms_seconds=math.sqrt(statistics.mean(x*x for x in residuals)),max_absolute_seconds=max(abs(x) for x in residuals),
        tolerance_seconds=tolerance,within_operational_tolerance=max(abs(x) for x in residuals)<=tolerance)
    try:
        result['affine_fit']=fit_taps([p['audio_seconds'] for p in pairs],[p['imu_seconds'] for p in pairs])
    except ValueError as exc:
        result.update(status='affine_fit_failed',affine_fit_error=str(exc))
        return result
    result['affine_fit']['within_operational_tolerance']=max(abs(x) for x in result['affine_fit']['residuals_seconds'])<=tolerance
    if not result['affine_fit']['within_operational_tolerance']:
        result['status']='affine_residual_limit_exceeded'
    elif not result['held_out_group_validation']['within_operational_tolerance']:
        result['status']='held_out_residual_limit_exceeded'
    else:
        result['status']='provisional_start_end_audio_imu_alignment'
    return result


def operator_windows(selection,marker_windows,audio_origin_lsl):
    """Human-authorized subwindows only; never infer a favorable group."""
    if not isinstance(selection,dict) or any(not isinstance(selection.get(key),str) or not selection[key].strip()
                                            for key in ('basis','operator_confirmation')):
        raise ValueError('Explicit selection needs nonempty basis and operator_confirmation')
    groups=selection.get('groups',{})
    if set(groups)!=set(marker_windows):raise ValueError('Selection needs exactly initial and ending groups')
    selected={}
    for label,marker in marker_windows.items():
        values=groups[label].get('audio_relative_seconds')
        if not isinstance(values,list) or len(values)!=2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in values):
            raise ValueError('Each operator selection needs two finite audio_relative_seconds')
        lo,hi=[audio_origin_lsl+value for value in values]
        if not marker['start_lsl']<=lo<hi<=marker['end_lsl']:
            raise ValueError('Operator-selected subwindow must remain within its original marker window')
        selected[label]=dict(start_lsl=lo,end_lsl=hi)
    return selected


def analyze_recording_groups(config,events,raw,audio_threshold=40000.,imu_threshold=150.,tolerance=.004,
                             selection=None,sensitivity=False):
    marker_windows=group_windows(events,config.get('world_id'))
    windows=marker_windows
    if selection is not None:
        audio=next((e for e in events if e.get('kind')=='audio_block'),None)
        if audio is None:raise ValueError('Operator selection requires recording audio origin')
        windows=operator_windows(selection,marker_windows,audio['lsl_first_frame'])
    original=_analyze_windows(config,events,raw,marker_windows,audio_threshold,imu_threshold,tolerance)
    result=_analyze_windows(config,events,raw,windows,audio_threshold,imu_threshold,tolerance) if selection is not None else original
    result['operator_selection']=selection
    if selection is not None:
        result['original_marker_windows']=marker_windows
        result['original_marker_validation_status']=original['status']
        result['original_marker_group_reports']=original['groups']
        excluded={}
        for label in windows:
            before=original['groups'][label]; after=result['groups'][label]
            item=dict(original_candidates_available='candidates' in before,
                      interpretation='Excluded by explicit operator-selected subwindow; no physical source is assigned to excluded events.')
            for field in ('audio_seconds','imu_seconds'):
                selected_candidates=set(after.get('candidates',{}).get(field,[]))
                item[field]=[v for v in before.get('candidates',{}).get(field,[]) if v not in selected_candidates]
            selected_pairs={(p['audio_seconds'],p['imu_tick']) for p in after.get('paired_taps',[])}
            item['original_pairs_excluded']=[p for p in before.get('paired_taps',[]) if (p['audio_seconds'],p['imu_tick']) not in selected_pairs]
            if 'error' in before:item['original_candidate_error']=before['error']
            excluded[label]=item
        result['excluded_candidate_review']=excluded
        result['limits'].append('Operator-selected review retains the original strict marker reports and excluded candidates; it is not an automatic acceptance of repeated taps.')
    if sensitivity:
        reports={}
        for threshold in sorted(set((20000.,40000.,80000.,audio_threshold))):
            checked=result if threshold==audio_threshold else _analyze_windows(config,events,raw,windows,threshold,imu_threshold,tolerance)
            reports[str(threshold)]=dict(status=checked['status'],audio_threshold=threshold,imu_threshold=imu_threshold,
                affine_fit=checked.get('affine_fit'),held_out_group_validation=checked.get('held_out_group_validation'),
                groups={label:{key:g.get(key) for key in ('status','quality_failures','error','paired_taps','candidates','unmatched_audio_candidates_seconds','selected_window_clipping_counts_per_channel','fit')}
                        for label,g in checked['groups'].items()})
        result['audio_threshold_sensitivity']=reports
        result['threshold_sensitivity_kind']='peak_and_onset_detection_threshold_stress'
        result['all_tested_audio_thresholds_passed']=all(r['status']=='provisional_start_end_audio_imu_alignment' for r in reports.values())
    return result


def analyze(folder,**options):
    folder=Path(folder)
    config=json.loads((folder/'session.json').read_text(encoding='utf-8'))['config']
    events=[json.loads(line) for line in (folder/'events.jsonl').open(encoding='utf-8') if line.strip()]
    source=None
    if isinstance(options.get('selection'),(str,Path)):
        path=Path(options['selection']); content=path.read_bytes()
        source=dict(path=str(path.resolve()),sha256=hashlib.sha256(content).hexdigest())
        options['selection']=json.loads(content.decode('utf-8'))
    result=analyze_recording_groups(config,events,(folder/'audio.pcm').read_bytes(),**options)
    if source is not None:result['operator_selection_source']=source
    result['source_session']=str(folder.resolve())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder');parser.add_argument('output',help='New JSON file; never overwritten')
    parser.add_argument('--audio-threshold',type=float,default=40000.)
    parser.add_argument('--imu-threshold',type=float,default=150.)
    parser.add_argument('--tolerance',type=float,default=.004)
    parser.add_argument('--selection-json',help='Explicit operator-confirmed subwindows inside original marker windows')
    parser.add_argument('--sensitivity',action='store_true',help='Compare selected groups at 20k/40k/80k audio thresholds')
    args=parser.parse_args()
    result=analyze(args.folder,audio_threshold=args.audio_threshold,imu_threshold=args.imu_threshold,tolerance=args.tolerance,
                   selection=args.selection_json,sensitivity=args.sensitivity)
    with Path(args.output).open('x',encoding='utf-8') as output:json.dump(result,output,indent=2,allow_nan=False)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
