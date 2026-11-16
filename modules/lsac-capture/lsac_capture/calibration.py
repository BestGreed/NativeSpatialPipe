"""Explicit, offline spatial calibration in one unchanged tracking world.

No hardware I/O. Distances are metres and quaternions are active wxyz rotations.
"""
import math
import statistics
from collections import deque

from .core import fit_common_axis, multiply, probe_pose, rotate, unit

A_SERIAL = "LHR-C9F2A107"
B_SERIAL = "LHR-7712DB4C"
B_OFFSET_TRACKER_M = [0., 0., .10673]


def vector(value, length=3):
    if len(value) != length or any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError(f"Expected {length} finite components")
    return list(value)


def normal(value):
    value = vector(value)
    size = math.sqrt(sum(x*x for x in value))
    if size < 1e-10:
        raise ValueError("Zero direction")
    return [x/size for x in value]


def inverse_quaternion(q):
    w, x, y, z = unit(vector(q, 4))
    return [w, -x, -y, -z]


def checked_pose(pose):
    p = vector(pose["position_m"])
    raw = vector(pose["quaternion_wxyz"], 4)
    if abs(math.sqrt(sum(x*x for x in raw))-1.) > .01:
        raise ValueError("Pose quaternion is not unit length")
    return dict(position_m=p, quaternion_wxyz=unit(raw))


def compose_pose(parent, child):
    """world_from_child = world_from_parent * parent_from_child."""
    parent, child = checked_pose(parent), checked_pose(child)
    delta = rotate(parent["quaternion_wxyz"], child["position_m"])
    return dict(position_m=[a+b for a,b in zip(parent["position_m"], delta)],
                quaternion_wxyz=unit(multiply(parent["quaternion_wxyz"], child["quaternion_wxyz"])))


def pose_in_reference(world_pose, reference_world_pose):
    world_pose, reference_world_pose = checked_pose(world_pose), checked_pose(reference_world_pose)
    inverse = inverse_quaternion(reference_world_pose["quaternion_wxyz"])
    return dict(position_m=rotate(inverse, [a-b for a,b in zip(world_pose["position_m"], reference_world_pose["position_m"])]),
                quaternion_wxyz=unit(multiply(inverse, world_pose["quaternion_wxyz"])))


def summarize_static_pose(events, serial, start_lsl, end_lsl, world_id, *, min_samples=30,
                          min_duration_seconds=.5, max_gap_seconds=.05,
                          max_position_deviation_m=.005, max_orientation_deviation_degrees=2.,
                          max_optical_age_seconds=.25, require_end_coverage_seconds=.1):
    """Strict static window of arrival-time poses; rejects reconnects and gaps.

    world_id must identify the common base-station geometry/configuration, not
    merely a device serial. This is averaging, not an accuracy certification.
    """
    if not world_id or not serial:
        raise ValueError("Explicit world_id and tracker serial required")
    if not math.isfinite(start_lsl) or not math.isfinite(end_lsl) or end_lsl <= start_lsl:
        raise ValueError("Invalid sample time window")
    thresholds=(min_duration_seconds,max_gap_seconds,max_position_deviation_m,
                max_orientation_deviation_degrees,max_optical_age_seconds,require_end_coverage_seconds)
    if (any(type(v) not in (int,float) or not math.isfinite(v) for v in thresholds) or
        type(min_samples) is not int or min_samples < 2 or min_duration_seconds <= 0 or
        max_gap_seconds <= 0 or max_position_deviation_m <= 0 or
        max_orientation_deviation_degrees <= 0 or max_optical_age_seconds <= 0 or
        require_end_coverage_seconds <= 0):
        raise ValueError("Invalid static sample thresholds")
    selected = []
    previous = None
    for event in events:
        if event.get("serial") != serial:
            continue
        stamp = event.get("arrival_lsl")
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            if event.get("kind") == "pose":
                raise ValueError("Nonfinite/missing pose arrival timestamp")
            continue
        if not start_lsl <= stamp <= end_lsl:
            continue
        if event.get("kind") in ("tracker_disconnect", "tracker_callback_error"):
            raise ValueError("Tracker failure/reconnect inside calibration window")
        if event.get("kind") != "pose":
            continue
        if event.get("synthetic"):
            raise ValueError("Synthetic poses cannot establish physical calibration")
        if event.get("valid") is not True:
            raise ValueError("Invalid pose inside calibration window")
        age = event.get("optical_age_seconds")
        if type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age < max_optical_age_seconds:
            raise ValueError("Missing/stale optical evidence")
        if previous is not None and stamp <= previous:
            raise ValueError("Pose arrival timestamps must increase strictly")
        pose = checked_pose(event)
        selected.append(dict(event, **pose))
        previous = stamp
    if len(selected) < min_samples:
        raise ValueError("Insufficient valid poses for static sampling")
    if len({e.get("epoch") for e in selected}) != 1 or selected[0].get("epoch") is None:
        raise ValueError("Missing epoch or reconnect across sample window")
    if selected[-1]["arrival_lsl"]-selected[0]["arrival_lsl"] < min_duration_seconds:
        raise ValueError("Static sample span too short")
    if selected[0]["arrival_lsl"]-start_lsl > require_end_coverage_seconds or end_lsl-selected[-1]["arrival_lsl"] > require_end_coverage_seconds:
        raise ValueError("Pose window boundaries are not covered; stale/incomplete sample")
    gaps = [b["arrival_lsl"]-a["arrival_lsl"] for a,b in zip(selected, selected[1:])]
    if max(gaps) > max_gap_seconds:
        raise ValueError("Pose gap exceeds static sampling threshold")
    positions = [e["position_m"] for e in selected]
    center = [statistics.mean(v[i] for v in positions) for i in range(3)]
    # Quaternion signs are equivalent; align them before averaging. Small static
    # windows are required, so a normalized arithmetic mean is well-conditioned.
    anchor = selected[0]["quaternion_wxyz"]
    aligned = []
    for e in selected:
        q = e["quaternion_wxyz"]
        sign = -1 if sum(a*b for a,b in zip(anchor,q)) < 0 else 1
        aligned.append([sign*x for x in q])
    average = unit([statistics.mean(q[i] for q in aligned) for i in range(4)])
    deviations = [math.sqrt(sum((v[i]-center[i])**2 for i in range(3))) for v in positions]
    orientation = [math.degrees(2*math.acos(min(1.,abs(sum(a*b for a,b in zip(average,q)))))) for q in aligned]
    if max(deviations) > max_position_deviation_m or max(orientation) > max_orientation_deviation_degrees:
        raise ValueError("Tracker was not sufficiently static; repeat sample")
    return dict(schema=1, kind="static_tracker_sample", serial=serial, world_id=world_id,
                epoch=selected[0]["epoch"], position_m=center, quaternion_wxyz=average,
                start_lsl=start_lsl, end_lsl=end_lsl, count=len(selected),
                observed_span_seconds=selected[-1]["arrival_lsl"]-selected[0]["arrival_lsl"],
                max_arrival_gap_seconds=max(gaps), max_position_deviation_m=max(deviations),
                rms_position_deviation_m=math.sqrt(statistics.mean(x*x for x in deviations)),
                max_orientation_deviation_degrees=max(orientation),
                quality_basis="static_spread_and_recent_optical_not_absolute_accuracy",
                absolute_accuracy_validated=False)


def sample_before_marker(events, serial, label, *, pre_seconds=2., end_gap_seconds=.1):
    """Stream the last matching marker with a bounded recent tracker cache.

    Only poses/failures for this tracker are retained. A matching marker freezes
    its preceding window, so later movement cannot change that snapshot.
    """
    if not math.isfinite(pre_seconds) or not math.isfinite(end_gap_seconds) or pre_seconds <= .5 or end_gap_seconds < 0:
        raise ValueError("Invalid marker sampling window")
    duration=pre_seconds+end_gap_seconds
    recent=deque(); latest_stamp=float("-inf"); overflow_stamp=float("-inf")
    marker=None; snapshot=None; snapshot_overflow=False
    for event in events:
        kind=event.get("kind")
        matching_marker=kind=="operator_marker" and event.get("label")==label
        relevant=event.get("serial")==serial and kind in ("pose","tracker_disconnect","tracker_callback_error")
        if not relevant and not matching_marker:
            continue
        stamp=event.get("arrival_lsl")
        if type(stamp) not in (int,float) or not math.isfinite(stamp):
            if relevant:
                raise ValueError("Tracker sample contains missing/nonfinite arrival timestamp")
            marker=event; snapshot=[]; snapshot_overflow=False
            continue
        latest_stamp=max(latest_stamp,stamp)
        while recent and recent[0].get("arrival_lsl",float("-inf")) < latest_stamp-duration:
            recent.popleft()
        if relevant:
            recent.append(event)
            # A nominal two-second window contains hundreds of poses. Guard
            # corrupt timestamps/floods without silently truncating a sample.
            if len(recent)>10000:
                overflow_stamp=recent.popleft()["arrival_lsl"]
        if matching_marker:
            marker=event
            snapshot=list(recent)
            snapshot_overflow=overflow_stamp>=stamp-duration
    if marker is None:
        raise ValueError("Operator marker not found")
    stamp=marker.get("arrival_lsl")
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or not marker.get("world_id"):
        raise ValueError("Marker needs finite arrival_lsl and world_id")
    if snapshot_overflow:
        raise ValueError("Static sample cache overflow; sample cannot be trusted")
    result=summarize_static_pose(snapshot,serial,stamp-end_gap_seconds-pre_seconds,
                                 stamp-end_gap_seconds,marker["world_id"])
    result["operator_marker"]=marker
    return result


def surface_from_sample(sample, surface_in_tracker):
    """Transform a physically defined contact surface; never guess A's origin.

    surface_in_tracker is tracker_from_surface, including contact-point offset
    and orientation. Surface +Z points out of the sampled screen/palm/table.
    """
    if sample.get("kind") != "static_tracker_sample" or not sample.get("world_id"):
        raise ValueError("A checked static sample with world_id is required")
    return compose_pose(sample, surface_in_tracker)


def calibrate_planes(screen_sample, reference_sample, reference_kind,
                     screen_surface_in_tracker, reference_surface_in_tracker):
    if reference_kind not in ("palm_rest", "desk"):
        raise ValueError("Reference must be palm_rest or desk; no floor/height calibration")
    if screen_sample.get("serial") != A_SERIAL or reference_sample.get("serial") != A_SERIAL:
        raise ValueError("Both plane samples must be from confirmed tracker A")
    if screen_sample.get("world_id") != reference_sample.get("world_id"):
        raise ValueError("Plane samples must share one unchanged tracking world")
    screen = surface_from_sample(screen_sample, screen_surface_in_tracker)
    reference = surface_from_sample(reference_sample, reference_surface_in_tracker)
    n_screen = rotate(screen["quaternion_wxyz"], [0,0,1])
    n_reference = rotate(reference["quaternion_wxyz"], [0,0,1])
    dot = max(-1., min(1.,sum(a*b for a,b in zip(n_screen,n_reference))))
    relative = pose_in_reference(screen, reference)
    opening = math.degrees(math.acos(-dot))
    return dict(schema=1, kind="screen_reference_calibration", world_id=screen_sample["world_id"],
                screen_world_pose=screen, reference_world_pose=reference,
                reference_kind=reference_kind, reference_from_screen=relative,
                sampled_point_distance_m=math.sqrt(sum(x*x for x in relative["position_m"])),
                screen_center_height_along_reference_normal_m=relative["position_m"][2],
                outward_normal_angle_degrees=math.degrees(math.acos(dot)),
                opening_angle_degrees=opening,
                angle_label="lid_opening_relative_to_palm_rest" if reference_kind=="palm_rest" else "screen_opening_relative_to_desk",
                angle_convention="acos(-dot(outward_normals)): closed=0,upright=90,flat_open=180; range 0..180",
                screen_sample=screen_sample, reference_sample=reference_sample,
                tracker_from_screen_surface=checked_pose(screen_surface_in_tracker),
                tracker_from_reference_surface=checked_pose(reference_surface_in_tracker),
                physical_registration_confirmed=False,
                limitations=["No floor or absolute desk height inferred.",
                             "Desk reference is not laptop lid angle when chassis is tilted.",
                             "Two nonparallel infinite planes do not have one fixed separation.",
                             "Move the laptop/lid/basestations after sampling: resample or track its rigid mounting."])


def calibrate_heading(sample, reference_world_direction, nominal_rotation_wxyz,
                      mic_forward_local, axis_local=(0,0,1), *, max_residual_degrees=5.):
    """Only one local common-axis angle is fitted after manual front alignment."""
    if sample.get("serial") != B_SERIAL or sample.get("kind") != "static_tracker_sample" or not sample.get("world_id"):
        raise ValueError("A checked B static pose sample with world_id is required")
    nominal = unit(vector(nominal_rotation_wxyz,4))
    axis = normal(axis_local)
    if math.sqrt(sum((a-b)**2 for a,b in zip(axis,[0,0,1]))) > 1e-6:
        raise ValueError("This back-to-back probe uses the reviewed tracker +Z common axis")
    target = rotate(inverse_quaternion(sample["quaternion_wxyz"]), normal(reference_world_direction))
    measured = rotate(nominal, normal(mic_forward_local))
    fit = fit_common_axis([measured], [target], axis)
    if not math.isfinite(max_residual_degrees) or max_residual_degrees <= 0 or math.degrees(max(fit["residual_radians"])) > max_residual_degrees:
        raise ValueError("Target cannot be matched by the one-axis model; check alignment/nominal axes")
    return dict(schema=1, kind="probe_heading_calibration", world_id=sample["world_id"],
                serial=B_SERIAL, sample=sample, reference_world_direction=normal(reference_world_direction),
                mic_forward_local=normal(mic_forward_local), fit=fit,
                probe=dict(known_offset_tracker_m=list(B_OFFSET_TRACKER_M),
                           offset_direction_zh="从Tracker原点沿共同轴指向安装面那一侧，朝向H3-VR声学中心",
                           distance_is_approximate=True, mounting="back_to_back_threaded_mount_faces",
                           known_rotation_wxyz=nominal, common_axis_tracker=axis,
                           common_axis_angle_radians=fit["angle_radians"],
                           mechanical_transform_confirmed=False),
                validated_on_withheld_trajectory=False,
                note="Manual front alignment only; no speaker playback or six-parameter fit.")


def microphone_pose(tracker_pose, heading):
    if heading.get("kind") != "probe_heading_calibration":
        raise ValueError("Heading calibration required")
    p = checked_pose(tracker_pose)
    geometry = heading["probe"]
    position, quaternion = probe_pose(p["position_m"], p["quaternion_wxyz"],
                                      geometry["known_offset_tracker_m"], geometry["known_rotation_wxyz"],
                                      geometry["common_axis_tracker"], geometry["common_axis_angle_radians"])
    return dict(position_m=position, quaternion_wxyz=quaternion)


def target_geometry(width_px, height_px, width_mm, height_mm, diameter_mm):
    if any(type(v) is not int or v <= 0 for v in (width_px,height_px)):
        raise ValueError("Native screen pixel resolution must be positive integers")
    if any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0 for v in (width_mm,height_mm,diameter_mm)):
        raise ValueError("Physical dimensions must be positive millimetres")
    if diameter_mm >= min(width_mm,height_mm):
        raise ValueError("Tracker target does not fit active screen area")
    return dict(width_px=width_px,height_px=height_px,width_mm=width_mm,height_mm=height_mm,
                diameter_mm=diameter_mm, center_px=[width_px/2,height_px/2],
                radius_px=[diameter_mm*width_px/width_mm/2,diameter_mm*height_px/height_mm/2],
                pixel_policy="native_1_to_1_pixels; OS effective DPI is not physical PPI",
                physical_size_verified=False)


def scaled_target_geometry(nominal, circle_pixel_scale):
    """Fit pixel diameter, keeping the physical Tracker diameter unchanged.

    A smaller drawn target means the display's effective millimetre dimensions
    are larger than nominal. Both axes use the same pixel scale factor.
    """
    if type(circle_pixel_scale) not in (int,float) or not math.isfinite(circle_pixel_scale) or circle_pixel_scale <= 0:
        raise ValueError("Target pixel scale must be finite and positive")
    width=nominal.get("nominal_width_mm",nominal["width_mm"])
    height=nominal.get("nominal_height_mm",nominal["height_mm"])
    result=target_geometry(nominal["width_px"],nominal["height_px"],width/circle_pixel_scale,
                           height/circle_pixel_scale,nominal["diameter_mm"])
    result.update(nominal_width_mm=width,nominal_height_mm=height,
                  effective_width_mm=result["width_mm"],effective_height_mm=result["height_mm"],
                  circle_pixel_scale=circle_pixel_scale,user_adjusted=abs(circle_pixel_scale-1)>1e-12,
                  fit_basis="operator_matches_fixed_tracker_outer_envelope_not_ruler_verification",
                  physical_tracker_diameter_mm=nominal["diameter_mm"])
    return result


def target_svg(geometry):
    g = target_geometry(**{k:geometry[k] for k in ("width_px","height_px","width_mm","height_mm","diameter_mm")})
    w,h = g["width_px"],g["height_px"]
    x,y = g["center_px"]; rx,ry = g["radius_px"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<rect width="{w}" height="{h}" fill="#111827"/>
<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="none" stroke="#ffffff" stroke-width="2"/>
<path d="M {x-12},{y} H {x+12} M {x},{y-12} V {y+12}" stroke="#60a5fa" stroke-width="2"/>
<path d="M {x},{y-ry-12} v -25 m -8,8 l 8,-8 l 8,8" fill="none" stroke="#ffffff" stroke-width="3"/>
<text x="{x}" y="{max(24,y-ry-55)}" text-anchor="middle" fill="white" font-family="sans-serif" font-size="20">TRACKER A / SCREEN UP / USB PORT DOWN</text>
<text x="{x}" y="{min(h-20,y+ry+40)}" text-anchor="middle" fill="white" font-family="sans-serif" font-size="16">{g['diameter_mm']:g} mm / native pixels / verify size with ruler</text>
</svg>'''
