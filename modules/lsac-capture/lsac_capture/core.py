"""Clock, rigid probe geometry, and tap-fit primitives; no hardware side effects."""
import math
import statistics


class TickUnwrapper:
    """Unwrap a single device clock. Reconnect requires a new instance/epoch.

    Reordered small negative steps are reported, not silently treated as wrap.
    A gap longer than half a 32-bit period is inherently ambiguous.
    """
    def __init__(self, epoch=0):
        self.epoch, self.last, self.high = epoch, None, 0

    def update(self, tick):
        if type(tick) is not int or not 0 <= tick < 2**32:
            raise ValueError("Expected raw uint32 tick")
        previous=None if self.last is None else self.high+self.last
        expanded=self.high+tick
        if previous is not None:
            if expanded-previous>2**31: expanded-=2**32  # late packet from previous wrap
            elif previous-expanded>2**31: expanded+=2**32
        reordered=previous is not None and expanded<previous
        wrap=not reordered and expanded//2**32>self.high//2**32
        if not reordered:
            self.high=(expanded//2**32)*2**32; self.last=tick
        return dict(raw_u32=tick, unwrapped_u64=expanded,
                    epoch=self.epoch, reordered=reordered, wrapped=wrap)


def unit(q):
    if len(q) != 4 or not all(math.isfinite(x) for x in q):
        raise ValueError("Quaternion must be finite wxyz")
    n = math.sqrt(sum(x*x for x in q))
    if n < 1e-12:
        raise ValueError("Zero quaternion")
    return [x/n for x in q]


def multiply(a, b):
    w,x,y,z = a; v,i,j,k = b
    return [w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v]


def rotate(q, v):
    q = unit(q)
    return multiply(multiply(q, [0,*v]), [q[0],-q[1],-q[2],-q[3]])[1:]


def probe_pose(position, tracker_wxyz, offset_local_m, fixed_wxyz, axis_local, angle_rad):
    """World_T_probe = World_T_tracker * known_local_T_probe.

    Only the mechanically unconstrained local-axis angle is calibrated. Offset
    is specified in tracker coordinates; no global yaw subtraction occurs.
    """
    norm = math.sqrt(sum(x*x for x in axis_local))
    if norm < 1e-12:
        raise ValueError("Missing common axis")
    r = [math.cos(angle_rad/2), *(x/norm*math.sin(angle_rad/2) for x in axis_local)]
    q = unit(multiply(unit(tracker_wxyz), multiply(r, unit(fixed_wxyz))))
    d = rotate(tracker_wxyz, offset_local_m)
    return [a+b for a,b in zip(position,d)], q


def axis_angle_observable(direction_local, axis_local, tolerance=1e-6):
    axis = [x / math.sqrt(sum(y*y for y in axis_local)) for x in axis_local]
    projection = sum(x*y for x,y in zip(direction_local,axis))
    return math.sqrt(sum((x-projection*y)**2 for x,y in zip(direction_local,axis))) > tolerance


def fit_common_axis(measured_local,reference_local,axis_local):
    """Fit ONLY R(local common axis, angle)*measured = reference.

    Vectors must already use the known nominal mechanical frame/offset. Does
    not solve translation, tilt, or six free parameters. Endfire is rejected.
    """
    if len(measured_local)!=len(reference_local) or not measured_local:
        raise ValueError("Need paired transverse direction vectors")
    n=math.sqrt(sum(x*x for x in axis_local))
    if not n: raise ValueError("Missing local axis")
    axis=[x/n for x in axis_local]; sine=cosine=0
    pairs=[]
    for m,r in zip(measured_local,reference_local):
        if not axis_angle_observable(m,axis) or not axis_angle_observable(r,axis):
            raise ValueError("Endfire direction cannot observe common-axis angle")
        def transverse(v):
            projection=sum(a*b for a,b in zip(v,axis))
            return [x-projection*a for x,a in zip(v,axis)]
        a=transverse(m); b=transverse(r)
        cross=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
        sine+=sum(x*y for x,y in zip(axis,cross)); cosine+=sum(x*y for x,y in zip(a,b))
        pairs.append((m,r))
    angle=math.atan2(sine,cosine)
    q=[math.cos(angle/2),*(a*math.sin(angle/2) for a in axis)]
    residual=[]
    for m,r in pairs:
        transformed=rotate(q,m)
        c=sum(a*b for a,b in zip(transformed,r))/math.sqrt(sum(x*x for x in transformed)*sum(x*x for x in r))
        residual.append(math.acos(max(-1,min(1,c))))
    return dict(angle_radians=angle,angle_degrees=math.degrees(angle),local_axis=axis,
                correction_wxyz=q,residual_radians=residual,
                equation="R(local_axis,angle) * measured_nominal_local = reference_local",
                translation_or_tilt_fitted=False,validated_on_withheld_trajectory=False)


def fit_taps(audio_seconds, imu_seconds, residual_limit=.012):
    """Robust affine audio = offset + rate*imu. Explicit paired event times.

    Pairing/detection and propagation/filter latency remain separate. This is
    audio–raw-IMU consistency, never a fused-pose latency calibration.
    """
    pairs = list(zip(imu_seconds,audio_seconds))
    if len(pairs) < 6 or len(audio_seconds) != len(imu_seconds):
        raise ValueError("Need >=6 explicitly paired taps across start and end")
    if not all(math.isfinite(x) and math.isfinite(y) for x,y in pairs):
        raise ValueError("Nonfinite tap time")
    slopes = [(y2-y1)/(x2-x1) for i,(x1,y1) in enumerate(pairs)
              for x2,y2 in pairs[i+1:] if abs(x2-x1) > 1]
    if not slopes:
        raise ValueError("Insufficient time span for drift")
    slope = statistics.median(slopes)
    offset = statistics.median(y-slope*x for x,y in pairs)
    indices = [i for i,(x,y) in enumerate(pairs) if abs(y-offset-slope*x) <= residual_limit]
    if len(indices) < 6:
        raise ValueError("Insufficient inlier taps")
    selected = [pairs[i] for i in indices]
    mx = statistics.mean(x for x,y in selected); my = statistics.mean(y for x,y in selected)
    denominator = sum((x-mx)**2 for x,y in selected)
    if denominator == 0:
        raise ValueError("Degenerate taps")
    slope = sum((x-mx)*(y-my) for x,y in selected) / denominator
    offset = my-slope*mx
    residuals = [y-offset-slope*x for x,y in pairs]
    return dict(offset_seconds=offset, rate=slope, drift_ppm=(slope-1)*1e6,
                inliers=indices, residuals_seconds=residuals,
                rms_inlier_seconds=math.sqrt(statistics.mean(residuals[i]**2 for i in indices)),
                interpretation="audio_to_raw_imu_only_not_fused_pose_latency")


def first_to_last(first_timestamp, frames, rate):
    if frames <= 0 or rate <= 0:
        raise ValueError("Bad audio block")
    return first_timestamp + (frames-1)/rate


def validate_config(c):
    if c.get("schema") != 1:
        raise ValueError("Unknown config schema")
    a = c["audio"]
    if not a.get("endpoint_id") or a["rate"] != 48000 or a["channels"] != 4 or a["valid_bits"] != 24:
        raise ValueError("Explicit H3VR 4ch/48k/24-bit endpoint required")
    if a["container_bits"] not in (24,32) or a.get("format") != "PCM":
        raise ValueError("PCM24 or 24-valid-in-32 only; no downmix/conversion")
    if a.get("ambi_order") != "WYZX" or a.get("normalization") != "SN3D" or not a.get("mic_position") or str(a["mic_position"]).startswith("TODO"):
        raise ValueError("Explicit AmbiX WYZX/SN3D and physical MicPosition required")
    if a.get("channel_order_verified") is not True or a.get("mode") not in ("shared","exclusive"):
        raise ValueError("Verify actual four-channel order and select explicit WASAPI mode")
    t = c["tracking"]
    serials = t["serials"]
    if t.get("backend") != "libsurvive" or len(serials) != 2 or len(set(serials)) != 2 or any(not x or x.startswith("TODO") for x in serials):
        raise ValueError("Two distinct confirmed libsurvive tracker serials required")
    if t.get("pair_device") is not False:
        raise ValueError("Pairing must remain disabled")
    p = c["probe"]
    unit(p["known_rotation_wxyz"])
    if len(p["known_offset_tracker_m"]) != 3 or not all(type(v) in (int,float) and math.isfinite(v) for v in p["known_offset_tracker_m"]):
        raise ValueError("Known mechanical offset required")
    if not p.get("mechanical_transform_confirmed") or not p.get("support_and_usb_strain_relief_confirmed"):
        raise ValueError("Confirm mechanical transform, support and strain relief first")
    return c
