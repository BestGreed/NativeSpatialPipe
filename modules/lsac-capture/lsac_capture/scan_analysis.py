"""Offline pose evidence and path-local band levels. No hardware or clock fitting.

Arrival previews are deliberately explicit. A tap fit alone never qualifies as
an audio--fused-pose time mapping. Unvisited space is not interpolated.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
import html
import json
import math
from pathlib import Path
import statistics

from .core import probe_pose, rotate, unit
from .session import unpack24

B_SERIAL = "LHR-7712DB4C"
BAND_HZ = (500 * 2 ** (-1 / 6), 500 * 2 ** (1 / 6))
DEFAULT_OFFSET_M = [0., 0., .10673]


def _finite_vector(value, length):
    return isinstance(value, (list, tuple)) and len(value) == length and all(
        isinstance(x, (int, float)) and math.isfinite(x) for x in value)


def valid_pose(event):
    q = event.get("quaternion_wxyz")
    return bool(event.get("valid")) and _finite_vector(event.get("position_m"), 3) and \
        _finite_vector(q, 4) and abs(math.sqrt(sum(x*x for x in q)) - 1) < .01


def third_octave_rms(samples, rate=48000):
    """Per-channel rectangular-window FFT band RMS in original ADC counts.

    Finite-window resolution is reported by the caller. This is a band-energy
    estimate, not a standards-certified fractional-octave filter or dB SPL.
    """
    import numpy as np
    values = np.asarray(samples, dtype=np.float64)
    if values.ndim != 2 or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("Expected finite frames x channels samples")
    if rate <= 2 * BAND_HZ[1]:
        raise ValueError("Sample rate too low for the 500 Hz band")
    n = len(values)
    bins = np.fft.rfftfreq(n, 1 / rate)
    selected = (bins >= BAND_HZ[0]) & (bins <= BAND_HZ[1])
    if not selected.any():
        raise ValueError("Window too short to contain a band FFT bin")
    power = np.abs(np.fft.rfft(values, axis=0)) ** 2 / n**2
    weights = np.full(len(bins), 2.)
    weights[0] = 1.
    if n % 2 == 0:
        weights[-1] = 1.
    return np.sqrt((power[selected] * weights[selected, None]).sum(axis=0)).tolist()


def _quantiles(values):
    if not values:
        return dict(count=0, median=None, minimum=None, maximum=None)
    return dict(count=len(values), median=statistics.median(values),
                minimum=min(values), maximum=max(values))


def stages_from_markers(events):
    markers = [e for e in events if e.get("kind") == "operator_marker" and
               isinstance(e.get("arrival_lsl"), (int, float))]
    markers.sort(key=lambda e: e["arrival_lsl"])
    last = max((e.get("arrival_lsl", float("-inf")) for e in events), default=0)
    return [dict(label=e.get("label", "unnamed"), start_lsl=e["arrival_lsl"],
                 end_lsl=markers[i+1]["arrival_lsl"] if i+1 < len(markers) else last + 1e-9)
            for i, e in enumerate(markers)]


def pose_validation(events, serial=B_SERIAL, stages=None):
    """Descriptive phase statistics; operator labels do not certify accuracy."""
    poses = [e for e in events if e.get("kind") == "pose" and e.get("serial") == serial]
    poses.sort(key=lambda e: e["arrival_lsl"])
    stages = stages if stages is not None else stages_from_markers(events)
    if not stages and poses:
        stages = [dict(label="unlabelled", start_lsl=poses[0]["arrival_lsl"],
                       end_lsl=poses[-1]["arrival_lsl"] + 1e-9)]
    output = []
    baseline = None
    for stage in stages:
        start, end = stage["start_lsl"], stage["end_lsl"]
        if not all(math.isfinite(x) for x in (start, end)) or end <= start:
            raise ValueError("Stage must have finite increasing LSL bounds")
        selected = [p for p in poses if start <= p["arrival_lsl"] < end]
        good = [p for p in selected if valid_pose(p)]
        gaps = [b["arrival_lsl"]-a["arrival_lsl"] for a, b in zip(selected, selected[1:])
                if a.get("epoch", 0) == b.get("epoch", 0)]
        backsteps = sum(b["pose_timecode_u64"] < a["pose_timecode_u64"]
                        for a, b in zip(selected, selected[1:])
                        if a.get("epoch", 0) == b.get("epoch", 0))
        center = [statistics.median(p["position_m"][axis] for p in good) for axis in range(3)] if good else None
        displacement = [math.dist(p["position_m"], center) for p in good] if good else []
        angular = []
        if good:
            first_q = unit(good[0]["quaternion_wxyz"])
            for p in good:
                dot = abs(sum(a*b for a, b in zip(first_q, unit(p["quaternion_wxyz"]))))
                angular.append(math.degrees(2*math.acos(min(1., dot))))
        row = dict(label=stage.get("label", "unnamed"), start_lsl=start, end_lsl=end,
                   pose_count=len(selected), valid_pose_count=len(good),
                   median_position_m=center, position_scatter_about_median_m=_quantiles(displacement),
                   angular_change_from_first_degrees=_quantiles(angular),
                   max_arrival_gap_seconds=max(gaps, default=None), provider_tick_backsteps=backsteps,
                   epochs=sorted(set(p.get("epoch", 0) for p in selected)),
                   first_valid_after_stage_start_seconds=good[0]["arrival_lsl"]-start if good else None)
        if center is not None:
            if baseline is None:
                baseline = center
            row["median_distance_from_first_phase_m"] = math.dist(center, baseline)
        output.append(row)
    return dict(serial=serial, phases=output, pose_count=len(poses),
                valid_pose_count=sum(valid_pose(p) for p in poses),
                accuracy_certified=False,
                interpretation="Scatter describes the selected movement; only a physically stationary phase measures repeatability.")


def pose_imu_clock_evidence(events, serial=B_SERIAL):
    """Nearest-arrival tick differences; never turns these into a clock fit."""
    imus = defaultdict(list)
    for e in events:
        if e.get("kind") == "raw_imu" and e.get("serial") == serial and "unwrapped_u64" in e:
            imus[e.get("epoch", 0)].append(e)
    for values in imus.values():
        values.sort(key=lambda e: e["arrival_lsl"])
    imu_times = {epoch:[e["arrival_lsl"] for e in values] for epoch, values in imus.items()}
    ticks, arrivals = [], []
    for p in events:
        if p.get("kind") != "pose" or p.get("serial") != serial:
            continue
        candidates = imus.get(p.get("epoch", 0), [])
        if not candidates:
            continue
        stamps = imu_times[p.get("epoch", 0)]
        index = bisect_left(stamps, p["arrival_lsl"])
        nearby = candidates[max(0, index-1):index+1]
        imu = min(nearby, key=lambda e: abs(e["arrival_lsl"]-p["arrival_lsl"]))
        difference = p["arrival_lsl"]-imu["arrival_lsl"]
        if abs(difference) <= .05:
            arrivals.append(difference)
            ticks.append(p["pose_timecode_u64"]-imu["unwrapped_u64"])
    return dict(serial=serial, nearest_arrival_difference_seconds=_quantiles(arrivals),
                native_pose_minus_raw_imu_ticks=_quantiles(ticks),
                validated=False, interpretation="Nearest host arrivals are not evidence of equal physical sample times; provider tick semantics remain unvalidated.")


def _mapping(calibration, serial):
    mappings = calibration.get("pose_clock_mappings", [])
    if calibration.get("pose_clock_mapping"):
        mappings = [*mappings, calibration["pose_clock_mapping"]]
    result = {}
    for m in mappings:
        if m.get("serial") != serial:
            continue
        bounds = m.get("provider_tick_range")
        if m.get("validated") is not True or m.get("pose_latency_validated") is not True or \
           m.get("provider_tick_semantics_confirmed") is not True:
            raise ValueError("Aligned analysis requires validated fused-pose clock and latency evidence")
        if not _finite_vector(bounds, 2) or bounds[1] <= bounds[0]:
            raise ValueError("Clock mapping needs a measured provider_tick_range")
        scale, offset = m.get("seconds_per_tick"), m.get("offset_lsl_seconds")
        if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0 or \
           not isinstance(offset, (int, float)) or not math.isfinite(offset):
            raise ValueError("Clock mapping needs finite positive scale and finite offset")
        epoch = m.get("epoch", 0)
        if epoch in result:
            raise ValueError("Ambiguous clock mappings for one epoch")
        result[epoch] = m
    if not result:
        raise ValueError("No fused-pose clock mapping; explicitly select arrival-preview for an unaligned preview")
    return result


def _geometry(calibration, config, mode):
    probe = calibration.get("probe", {})
    offset = probe.get("known_offset_tracker_m", DEFAULT_OFFSET_M)
    if not _finite_vector(offset, 3):
        raise ValueError("Probe offset must be a finite tracker-local metre vector")
    screen = calibration.get("screen_world_pose", calibration.get("world_from_screen"))
    if mode == "aligned" and (probe.get("mechanical_transform_confirmed") is not True or screen is None):
        raise ValueError("Aligned scan needs confirmed probe geometry and screen_world_pose")
    fixed = probe.get("known_rotation_wxyz")
    angle = probe.get("common_axis_angle_radians")
    if fixed is not None:
        unit(fixed)
    if angle is not None and (not isinstance(angle, (int, float)) or not math.isfinite(angle)):
        raise ValueError("Common-axis angle must be finite")
    if mode == "aligned" and (fixed is None or angle is None):
        raise ValueError("Aligned scan needs the fixed rotation and calibrated common-axis angle")
    if screen is not None:
        if not _finite_vector(screen.get("position_m"), 3) or not _finite_vector(screen.get("quaternion_wxyz"), 4):
            raise ValueError("Invalid screen_world_pose")
        unit(screen["quaternion_wxyz"])
        world = config.get("world_id", config.get("tracking", {}).get("world_id"))
        if not world or world != calibration.get("world_id"):
            raise ValueError("Screen calibration and recording must have the same explicit world_id")
    return probe, offset, screen


def _pose_points(events, serial, mode, mappings):
    points = []
    excluded = Counter()
    for e in events:
        if e.get("kind") != "pose" or e.get("serial") != serial:
            continue
        if mode == "arrival-preview":
            stamp = e.get("arrival_lsl")
        else:
            mapping = mappings.get(e.get("epoch", 0))
            tick = e.get("pose_timecode_u64")
            if mapping is None or not isinstance(tick, int):
                excluded["pose_without_epoch_mapping"] += 1
                continue
            if not mapping["provider_tick_range"][0] <= tick <= mapping["provider_tick_range"][1]:
                excluded["pose_outside_measured_mapping_range"] += 1
                continue
            stamp = mapping["offset_lsl_seconds"] + tick*mapping["seconds_per_tick"]
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            excluded["nonfinite_pose_timestamp"] += 1
            continue
        points.append((stamp, e))
    points.sort(key=lambda value: value[0])
    return points, dict(excluded)


def _audio_groups(events, audio_time_basis="packet-qpc", mode="arrival-preview"):
    """Never merge flagged, reordered, gapped, reformatted or reset packets."""
    groups, current, rejected = [], [], Counter()
    last = None
    for b in (e for e in events if e.get("kind") == "audio_block"):
        required = ("sample_start_i64", "frames", "pcm_byte_offset", "lsl_first_frame", "rate", "channels", "container_bits")
        if any(k not in b for k in required):
            raise ValueError("Audio sidecar missing required timing or byte-offset metadata")
        if b["frames"] <= 0 or b["sample_start_i64"] < 0 or b["pcm_byte_offset"] < 0:
            raise ValueError("Invalid audio block shape")
        if b["rate"] != 48000 or b["channels"] != 4 or b["container_bits"] not in (24, 32):
            raise ValueError("Expected exact four-channel 48kHz PCM24 or 24-valid-in-32")
        if not math.isfinite(b["lsl_first_frame"]):
            raise ValueError("Nonfinite audio timestamp")
        bad = bool(b.get("flags") or b.get("device_gap_frames") or b.get("discontinuity") or
                   b.get("timestamp_error") or b.get("timestamp_basis") == "arrival_fallback_invalid_hardware_timestamp")
        qpc = b.get("device_qpc_100ns_u64")
        if qpc is not None and (not isinstance(qpc,(int,float)) or not math.isfinite(qpc) or qpc<0):
            raise ValueError("Invalid raw audio QPC")
        sample_contiguous = last is not None and b["sample_start_i64"] == last["sample_start_i64"]+last["frames"] and \
            b.get("epoch", 0) == last.get("epoch", 0) and b["container_bits"] == last["container_bits"] and \
            b["pcm_byte_offset"] == last["pcm_byte_offset"]+last["frames"]*4*(last["container_bits"]//8) and \
            (last.get("device_position_u64") is None or b.get("device_position_u64") is None or
             b["device_position_u64"] == last["device_position_u64"]+last["frames"])
        packet_timing_contiguous = False
        if last is not None:
            delta=b["lsl_first_frame"]-last["lsl_first_frame"]
            nominal=last["frames"]/last["rate"]
            old_qpc=last.get("device_qpc_100ns_u64")
            raw_delta=(qpc-old_qpc)/1e7 if qpc is not None and old_qpc is not None else None
            if b.get("epoch",0)==last.get("epoch",0) and (delta<-.100 or raw_delta is not None and raw_delta<-.100):
                raise ValueError("Gross backwards audio timestamp step")
            packet_timing_contiguous=abs(delta-nominal)<=2/b["rate"] and \
                (raw_delta is None or abs(raw_delta-nominal)<=2/b["rate"])
        if sample_contiguous and not bad and not packet_timing_contiguous and mode=="aligned":
            raise ValueError("Packet QPC does not support aligned audio timing; nominal-sampleclock is available only for an explicit arrival-preview")
        contiguous = sample_contiguous and (audio_time_basis=="nominal-sampleclock" or packet_timing_contiguous)
        if bad or not contiguous:
            if current:
                groups.append(current)
            current = []
        if bad:
            rejected["flagged_audio_blocks"] += 1
            last = None
        else:
            if last is not None and not contiguous:
                rejected["audio_boundaries_not_bridged"] += 1
            current.append(b)
            last = b
    if current:
        groups.append(current)
    return groups, dict(rejected)


def playback_interval(events,index=None,edge_seconds=.1):
    """Select one complete host-API playback interval, never concatenate plays."""
    if not isinstance(edge_seconds,(int,float)) or not math.isfinite(edge_seconds) or not 0<=edge_seconds<=2:
        raise ValueError("Playback edge exclusion must be 0..2 seconds")
    intervals=[]; active=None
    for event in events:
        kind=event.get('kind')
        if kind not in ('playback_started','playback_finished'):continue
        stamp=event.get('arrival_lsl')
        if not isinstance(stamp,(int,float)) or not math.isfinite(stamp):
            raise ValueError("Playback boundary requires finite original arrival_lsl")
        if kind=='playback_started':
            if active is not None:raise ValueError("Overlapping/ambiguous playback starts")
            active=event
        else:
            if active is None:raise ValueError("Playback finish has no matching start")
            if stamp<=active['arrival_lsl']:raise ValueError("Playback interval must increase")
            intervals.append(dict(start_event=active,finish_event=event))
            active=None
    if active is not None:raise ValueError("Playback start has no matching finish")
    if not intervals:raise ValueError("No complete playback interval in this recording")
    if index is None:
        if len(intervals)!=1:raise ValueError("Multiple playback intervals; choose explicit playback_index")
        index=0
    if type(index) is not int or not 0<=index<len(intervals):raise ValueError("Playback index outside recorded intervals")
    selected=intervals[index]
    if selected['finish_event'].get('status')!='completed_not_acoustically_verified':
        raise ValueError("Selected playback did not finish normally")
    lo=selected['start_event']['arrival_lsl']+edge_seconds
    hi=selected['finish_event']['arrival_lsl']-edge_seconds
    if hi<=lo:raise ValueError("Playback edge exclusion removes the entire interval")
    return dict(scope='selected_playback_interval',playback_index=index,recorded_playback_count=len(intervals),
                start_lsl=lo,end_lsl=hi,edge_exclusion_seconds=edge_seconds,
                raw_started_lsl=selected['start_event']['arrival_lsl'],raw_finished_lsl=selected['finish_event']['arrival_lsl'],
                selected_host_duration_seconds=hi-lo,original_boundary_events=selected,
                timestamp_basis='original_host_API_playback_events_not_verified_acoustic_DAC_times')


def analyze_records(events, read_pcm, config, calibration=None, mode="aligned", window_seconds=.1,
                    max_pose_gap=.025, serial=B_SERIAL, audio_time_basis="packet-qpc",
                    playback_only=False,playback_index=None,playback_edge_seconds=.1):
    """Analyze authoritative events and byte reader; returns JSON-safe evidence.

    read_pcm(offset, byte_count) must return the exact requested packet bytes.
    A window uses its nearest single pose, with bracket gap and epoch checks;
    no missing trajectory is reconstructed.
    """
    import numpy as np
    if mode not in ("aligned", "arrival-preview"):
        raise ValueError("Mode must be aligned or arrival-preview")
    if audio_time_basis not in ("packet-qpc","nominal-sampleclock"):
        raise ValueError("Audio time basis must be packet-qpc or nominal-sampleclock")
    if audio_time_basis=="nominal-sampleclock" and mode!="arrival-preview":
        raise ValueError("Unvalidated nominal-sampleclock is allowed only in arrival-preview")
    if playback_index is not None and not playback_only:raise ValueError("playback_index requires playback_only")
    selection=playback_interval(events,playback_index,playback_edge_seconds) if playback_only else dict(scope='whole_recording')
    if not math.isfinite(window_seconds) or not .05 <= window_seconds <= 2 or \
       not math.isfinite(max_pose_gap) or not 0 < max_pose_gap <= .1:
        raise ValueError("Window must be .05..2 seconds and pose gap >0..0.1 seconds")
    calibration = calibration or {}
    probe, offset, screen = _geometry(calibration, config, mode)
    mappings = _mapping(calibration, serial) if mode == "aligned" else {}
    points, excluded = _pose_points(events, serial, mode, mappings)
    times = [p[0] for p in points]
    groups, audio_excluded = _audio_groups(events,audio_time_basis,mode)
    from .tap_analysis import audio_qpc_diagnostic
    qpc_diagnostic=audio_qpc_diagnostic([e for e in events if e.get("kind")=="audio_block"])
    qpc_diagnostic.pop("onset_time_basis",None)
    frames = round(window_seconds*48000)
    rows, rejection_counts = [], Counter()
    excluded_by_selection=0
    for group in groups:
        count = sum(b["frames"] for b in group)
        start = group[0]["sample_start_i64"]
        block_starts = [b["sample_start_i64"] for b in group]
        for local_start in range(0, count-frames+1, frames):
            logical_start = start+local_start
            logical_end = logical_start+frames
            # Window centres use the selected analysis axis; leave the raw
            # playback and packet timestamps unchanged in their source events.
            if audio_time_basis=='nominal-sampleclock':
                candidate_center=group[0]['lsl_first_frame']+(local_start+(frames-1)/2)/48000
            else:
                centre_block=group[max(0,bisect_left(block_starts,logical_start)-1)]
                if logical_start>=centre_block['sample_start_i64']+centre_block['frames']:
                    centre_block=group[bisect_left(block_starts,logical_start)]
                candidate_center=centre_block['lsl_first_frame']+(logical_start-centre_block['sample_start_i64']+(frames-1)/2)/48000
            if playback_only and not selection['start_lsl']<=candidate_center<=selection['end_lsl']:
                excluded_by_selection+=1
                continue
            parts = []
            first_lsl = group[0]["lsl_first_frame"]+local_start/48000 if audio_time_basis=="nominal-sampleclock" else None
            first_block_index = max(0, bisect_left(block_starts, logical_start)-1)
            for block_index in range(first_block_index, len(group)):
                b = group[block_index]
                if b["sample_start_i64"] >= logical_end:
                    break
                lo, hi = max(logical_start, b["sample_start_i64"]), min(logical_end, b["sample_start_i64"]+b["frames"])
                if hi <= lo:
                    continue
                if first_lsl is None:
                    first_lsl = b["lsl_first_frame"]+(lo-b["sample_start_i64"])/48000
                stride = 4*(b["container_bits"]//8)
                raw = read_pcm(b["pcm_byte_offset"]+(lo-b["sample_start_i64"])*stride, (hi-lo)*stride)
                if len(raw) != (hi-lo)*stride:
                    raise ValueError("Truncated PCM file; do not analyze partial audio")
                parts.append(unpack24(raw, b["container_bits"]))
            samples = np.concatenate(parts)
            stamp = first_lsl+(frames-1)/(2*48000)
            row = dict(start_sample=logical_start, frames=frames, audio_center_lsl=stamp,
                       coordinate_frame="screen" if screen else "tracking_world",
                       association=mode, audio_time_basis=audio_time_basis, accepted=False, rejection=None)
            if np.any((samples >= 8388607) | (samples <= -8388608)):
                row["rejection"] = "audio_clipping"
            else:
                index = bisect_left(times, stamp)
                nearby = points[max(0, index-1):index+1]
                if not nearby:
                    row["rejection"] = "no_pose"
                else:
                    pose_time, pose = min(nearby, key=lambda value: abs(value[0]-stamp))
                    row.update(pose_associated_lsl=pose_time, pose_delta_seconds=pose_time-stamp,
                               pose_provider_timecode_u64=pose.get("pose_timecode_u64"), pose_epoch=pose.get("epoch", 0))
                    if abs(pose_time-stamp) > max_pose_gap:
                        row["rejection"] = "stale_pose"
                    elif not valid_pose(pose):
                        row["rejection"] = "invalid_pose"
                    elif 0 < index < len(points) and (points[index][0]-points[index-1][0] > 2*max_pose_gap or
                             points[index][1].get("epoch", 0) != points[index-1][1].get("epoch", 0)):
                        row["rejection"] = "pose_gap_or_epoch_boundary"
                    else:
                        position = [a+b for a, b in zip(pose["position_m"], rotate(pose["quaternion_wxyz"], offset))]
                        orientation = None
                        if probe.get("common_axis_angle_radians") is not None and probe.get("known_rotation_wxyz") is not None:
                            _, orientation = probe_pose(pose["position_m"], pose["quaternion_wxyz"], offset,
                                probe["known_rotation_wxyz"], probe.get("common_axis_tracker", [0,0,1]), probe["common_axis_angle_radians"])
                        if screen:
                            inverse = unit(screen["quaternion_wxyz"])
                            inverse = [inverse[0], *(-v for v in inverse[1:])]
                            position = rotate(inverse, [a-b for a, b in zip(position, screen["position_m"])])
                            if orientation is not None:
                                from .core import multiply
                                orientation = unit(multiply(inverse, orientation))
                        rms = third_octave_rms(samples)
                        dbfs = [20*math.log10(v/8388608) if v > 0 else None for v in rms]
                        row.update(accepted=True, acoustic_center_position_m=position,
                                   acoustic_quaternion_wxyz=orientation, band_rms_adc_counts=rms, band_dbfs=dbfs)
            if row["rejection"]:
                rejection_counts[row["rejection"]] += 1
            rows.append(row)
    peaks = [max((r["band_dbfs"][ch] for r in rows if r["accepted"] and r["band_dbfs"][ch] is not None), default=None)
             for ch in range(4)]
    for row in rows:
        if row["accepted"]:
            row["band_db_re_session_max"] = [v-peak if v is not None and peak is not None else None
                                               for v, peak in zip(row["band_dbfs"], peaks)]
            row['band_db_re_selected_max']=row['band_db_re_session_max']
    good=[row for row in rows if row['accepted']]
    bounds=dict(coordinate_frame='screen' if screen else 'tracking_world',
                minimum=[min(row['acoustic_center_position_m'][axis] for row in good) for axis in range(3)],
                maximum=[max(row['acoustic_center_position_m'][axis] for row in good) for axis in range(3)]) if good else None
    return dict(schema=1, analysis="500Hz_third_octave_path_samples", mode=mode, serial=serial,
                source_capture_purpose=config.get("capture_purpose"),
                audio_time_basis=audio_time_basis, nominal_clock_unvalidated=audio_time_basis=="nominal-sampleclock",
                analysis_time_axis_derived=audio_time_basis=="nominal-sampleclock",
                raw_timestamps_modified=False, pyxdf_dejitter_applied=False, audio_qpc_timing=qpc_diagnostic,
                band_hz=list(BAND_HZ), sample_rate=48000, window_frames=frames, fft_bin_width_hz=48000/frames,
                window_function="rectangular", acoustic_calibrated=False, db_spl=False,
                precision_certified=False, interpolation="none", tracking_offset_local_m=offset,
                geometry_confirmed=probe.get("mechanical_transform_confirmed") is True,
                nominal_rotation_applied=probe.get("common_axis_angle_radians") is not None and probe.get("known_rotation_wxyz") is not None,
                window_count=len(rows), accepted_window_count=sum(r["accepted"] for r in rows),
                selection=selection,windows_outside_selection=excluded_by_selection,
                relative_level_reference_scope=selection['scope'],relative_level_reference_dbfs_per_channel=peaks,
                accepted_position_bounds_m=bounds,audio_exclusions_scope='whole_recording_context',
                rejections=dict(rejection_counts), excluded_pose_events=excluded, audio_exclusions=audio_excluded,
                channel_labels=config.get("audio", {}).get("channel_labels", ["USB_ch1","USB_ch2","USB_ch3","USB_ch4"]),
                incomplete_tail_frames_not_analyzed=sum(sum(b["frames"] for b in group) % frames for group in groups),
                channel_semantics_verified=config.get("audio", {}).get("channel_order_verified") is True,
                pose_validation=pose_validation(events, serial), pose_imu_clock_evidence=pose_imu_clock_evidence(events, serial),
                limitations=["arrival-preview uses host delivery times and is not audio-pose synchronization.",
                             "nominal-sampleclock derives analysis times from each continuous segment's first raw timestamp and logical samples/48000; it does not validate the sample rate, drift or packet QPC jitter, and is not pyxdf dejitter.",
                             "Band levels are digital levels, not dB SPL or source-normalized transfer functions.",
                             "Each position represents a finite audio window during movement, not a point measurement.",
                             "No values are inferred outside measured paths; no physical accuracy certification."], windows=rows)


def analyze_session(folder, calibration=None, **options):
    folder = Path(folder)
    metadata = json.loads((folder/"session.json").read_text(encoding="utf-8"))
    config = metadata["config"]
    events = [json.loads(line) for line in (folder/"events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if isinstance(calibration, (str, Path)):
        calibration = json.loads(Path(calibration).read_text(encoding="utf-8"))
    with (folder/"audio.pcm").open("rb") as source:
        def read_pcm(offset, length):
            source.seek(offset)
            return source.read(length)
        result = analyze_records(events, read_pcm, config, calibration, **options)
    result["source_session"] = str(folder.resolve())
    return result


def write_outputs(result, output):
    """Create a new directory with complete JSON, flat CSV and XY path plot."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/"scan-analysis.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    fields = ["start_sample", "frames", "audio_center_lsl", "association", "audio_time_basis", "accepted", "rejection", "coordinate_frame",
              "pose_delta_seconds", "x_m", "y_m", "z_m"]
    fields += [f"ch{ch}_{kind}" for kind in ("rms_counts", "dbfs", "db_re_max") for ch in range(1,5)]
    with (output/"path-levels.csv").open("w", newline="", encoding="utf-8") as source:
        writer = csv.DictWriter(source, fieldnames=fields)
        writer.writeheader()
        for row in result["windows"]:
            flat = {key:row.get(key) for key in fields}
            if row["accepted"]:
                flat.update(zip(("x_m","y_m","z_m"), row["acoustic_center_position_m"]))
                for name, key in (("rms_counts","band_rms_adc_counts"),("dbfs","band_dbfs"),("db_re_max","band_db_re_session_max")):
                    flat.update({f"ch{ch+1}_{name}":value for ch,value in enumerate(row[key])})
            writer.writerow(flat)
    good = [r for r in result["windows"] if r["accepted"]]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">',
             '<rect width="900" height="650" fill="white"/>',
             '<style>text{font:15px sans-serif;fill:#222}</style>',
             f'<text x="35" y="30">500 Hz third-octave path samples — {html.escape(result["mode"])} / {html.escape(result["audio_time_basis"])}</text>',
             f'<text x="35" y="55">XY projection; measured points only. Colour: USB ch1 dB relative to {html.escape(result["relative_level_reference_scope"])} maximum.</text>',
             '<text x="35" y="80">Digital levels, not dB SPL. No spatial interpolation or precision certification.</text>']
    if good:
        xs=[r["acoustic_center_position_m"][0] for r in good]
        ys=[r["acoustic_center_position_m"][1] for r in good]
        xmin,xmax,ymin,ymax=min(xs),max(xs),min(ys),max(ys)
        scale=min(760/max(xmax-xmin,.02),450/max(ymax-ymin,.02))
        for row in good:
            x,y,_=row["acoustic_center_position_m"]
            value=row["band_db_re_session_max"][0]
            amount=max(0.,min(1.,((value if value is not None else -30)+30)/30))
            color=f"rgb({round(230*amount)},{round(90+50*(1-amount))},{round(230*(1-amount))})"
            parts.append(f'<circle cx="{70+(x-xmin)*scale:.3f}" cy="{560-(y-ymin)*scale:.3f}" r="3" fill="{color}"><title>sample {row["start_sample"]}; xyz m: {x:.4f}, {y:.4f}, {row["acoustic_center_position_m"][2]:.4f}; ch1 relative dB: {value}</title></circle>')
        parts.append(f'<text x="35" y="605">X: {xmin:.4f}..{xmax:.4f} m; Y: {ymin:.4f}..{ymax:.4f} m; frame: {html.escape(good[0]["coordinate_frame"])}</text>')
    else:
        parts.append('<text x="35" y="150">No accepted spatial windows. Inspect JSON rejection reasons.</text>')
    parts.append('</svg>')
    (output/"path-levels.svg").write_text("\n".join(parts), encoding="utf-8")
    return dict(json=str((output/"scan-analysis.json").resolve()), csv=str((output/"path-levels.csv").resolve()),
                svg=str((output/"path-levels.svg").resolve()))
