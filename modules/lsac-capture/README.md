# LSAC continuous capture prototype

The speaker **three-shell scan workbench** is available through
`Tools.ps1 sphere-scan`. It adds six-face cabinet registration, active
spherical-harmonic geometry guidance, segmented acquisition, recovery, and
browser fullscreen. Start a device-free rehearsal with:

```powershell
.\modules\lsac-capture\Tools.ps1 sphere-scan --demo --workspace build/lsac/sphere-demo
```

Open `http://127.0.0.1:8769/`. Live mode reuses the existing independent A/B
tracking broker and four-channel recording pipeline. Guidance is explicitly
geometry-only; it does not certify acoustic uncertainty or CEA-2034 accuracy.
See setup and operating guide (reference outside this source tree).

Latest measurement priority: use the full Ambisonic spatial response across
125 Hz–16 kHz to evaluate direction-related structure and its stability across
the listening region; scalar FR is a tone/output constraint. The read-only
`Tools.ps1 coverage` observer now provides per-band/L/R/LR/near-middle-far
coverage navigation. See coverage guide (reference outside this source tree).
Its 45/65/85 cm screen-centre distances are proposed sampling layers; angular
acceptance and acoustic band quality are not certified by the display.

Purpose: measure the effective notebook output response so that NSp's
OLR-to-2.0 DSP can be designed and evaluated at representative listening
positions. Field plots, tracking and source-model experiments are diagnostic
tools, not the optimization target. Two controllable output channels should
be measured as the whole acoustic system, including internal crossover,
physical drivers and nearby surfaces; individual-driver FR or a dense
hemisphere reconstruction is not a prerequisite. Current 500 Hz pilots do
not establish a broadband DSP correction or a spatial-audio improvement.

Independent research module inside **Native Spatial Pipe**, branch `lsac`. It does not change the NSP renderer, EDID work, system defaults, USB drivers, or Steam configuration. The current target is continuous hand scanning: H3-VR four-channel audio + two independent trackers + raw IMU + known excitation reference. Band coverage/navigation/maps come later. This supersedes the early point-by-point FR workflow for this task.

**Current 6DoF result (2026-11-01):** receiver B now produces actual fused position and quaternion callbacks with the pinned source-built DLL. Two gen1 lighthouse positions were solved, 7,084 poses recorded, and configuration reuse after restart produced poses again. Physical movement/return/occlusion recovery and audio-pose timing remain unvalidated. See 6DoF evidence and operation (reference outside this source tree). Earlier compatibility/pairing blockers below are historical; do not repeat global diagnostics or pairing.

**Current toolkit:** use `Tools.ps1` for the shared A/B rig, A plane samples, B triplet/heading, combined capture/render and offline path analysis. Both Trackers have now streamed in one context; combined four-channel audio/A/B recording passed XDF replay. See current local toolchain and evidence (reference outside this source tree). Historical `Run.ps1 record`/`tracker-diagnostic` entries are not the current hardware workflow; receiver allowlists are now mandatory. H3-VR USB Ambisonic Mode must be checked: four USB channels alone do not establish AmbiX.

## What works now

- Windows native SetupAPI/HID attribute enumeration, stable MMDevice endpoint IDs, exact WASAPI format negotiation.
- Four-channel 48 kHz PCM24 capture, original packet bytes, int64 logical sample index, device sample position, **first-frame QPC in 100 ns**, flags and host arrival QPC. No downmix/nearest-format fallback.
- Capture worker → bounded queue → dedicated disk/LSL writer. On queue overflow, stop and mark incomplete; never silently bridge missing samples.
- LSL/XDF via unmodified official LabRecorderCLI; signed 24-bit values preserved in int32. LSL chunk timestamps explicitly refer to the **last** sample: `first + (frames-1)/48000`.
- Raw packet sidecars + replay checks with pyxdf clock sync and dejitter **disabled**; count and bit-exact audio checks, event count, raw hardware gap/flag reporting. Raw timestamps and processing timestamps are distinct.
- libsurvive v1.01 thin raw-IMU/pose/optical hooks, default callbacks preserved, per-device raw uint32/expanded clocks and reconnect epochs. Callback ID is not a full sample counter. Arrival timestamps are not claimed to be precision pose timing.
- Known rigid transform plus one **local common-axis** angular parameter; composition uses full quaternions. Axis-angle fitting rejects endfire/unobservable directions. Paired-tap affine offset/drift fitting retains residuals/outliers and does not claim fused-pose alignment.
- Deterministic sine/band-noise reference generation with exact samples, seed, digital level and hash. **No automatic output/playback command yet.** Output route and DAC timing remain unmeasured; do not use a file-generation timestamp as playback time.

## Current hardware result and blocker

H3-VR is connected. In the present Windows configuration shared mode offers a stereo mix; **exclusive PCM24, 4ch, 48 kHz** is exact-supported and has been passively recorded for 3.01 s. Four channels are nonzero and not bit-identical. Channel order/normalisation, device MicPosition and USB rotation behaviour are **not yet physically verified**. PCM24 packet support alone does not establish AmbiX semantics or calibration.

OS inventory found Watchman receivers `4E482CC871-2YX`, `C0531DBB07-2YX` (28DE:2102), and 28DE:2300 serial `LHR-9A18E51C`. A pairing-disabled startup identifies the latter's model as **arpara**, treated as HMD by upstream, with a firmware-version warning. It is not evidence of two connected trackers. User reports the trackers still need pairing. No pairing has been performed.

The first native diagnostic exceeded its 6 s budget; its exact process was identified and stopped, with log preserved. Native startup/close is now isolated in a supervised child with a stop request and bounded termination; **that new isolation has not been hardware re-tested**. The old native config path also exposed an upstream Windows drive-letter handling issue; future workers use `./libsurvive-config.json` in their session directory. Do not blindly rerun, force firmware changes or use `--pair-device 1` as a receiver index. Pairing must be separately scoped to the confirmed receiver and one powered target tracker.

## Commands

Run from repository root; `Run.ps1` uses the existing bundled Python and isolated `work/lsac-python` dependencies. No global Python changes.

```powershell
& modules/lsac-capture/Run.ps1 inventory
& modules/lsac-capture/Run.ps1 probe-audio '{0.0.1.00000000}.{324d001e-318b-4f8c-b208-5cc6a9324821}'
& modules/lsac-capture/Run.ps1 simulate build/lsac/new-synthetic-session
& modules/lsac-capture/Run.ps1 check build/lsac/scan-synthetic-002
& modules/lsac-capture/Run.ps1 window build/lsac/scan-synthetic-002 0 4800
& modules/lsac-capture/Run.ps1 prepare-excitation build/lsac/new-reference --kind band-noise --band 500 1000 --peak-dbfs -60 --seconds 2 --seed 20261101
```

All output directories must be new. `inventory` is OS read-only. `probe-audio` activates an audio client and queries formats but does **not** initialize/start capture. In the execution sandbox, HID interface enumeration may be empty while USB device nodes are visible; the full-authority inventory has been saved separately. Do not interpret an empty sandbox list as incompatibility.

`audio-diagnostic <endpoint-id> <new-directory> --seconds 3` passively opens exclusive capture only. It labels channel semantics unverified and reports missing tracking. It is not a calibrated scan.

`record <filled-config.json> <new-directory> --seconds 10 --confirm-feature-reports` requires completing `probe.example.json`. It starts libsurvive normally with pairing explicitly disabled; **normal startup issues feature reports**, so this is not OS enumeration. Do not run until receiver/tracker identities, actual four-channel semantics, mechanical offset and strain relief are confirmed. For this installation, native tracker compatibility/exit remains a blocker. Preserve hardware logs and stop on native errors.

`tracker-diagnostic <new-directory> --seconds 6 --confirm-feature-reports` is the isolated diagnostic entry, pairing and calibration disabled. Do not execute again just because the command exists; complete the current hardware/firmware review and one-device pairing plan first.

The session output contains `raw.xdf`, `audio.pcm`, `events.jsonl`, config/clock anchor, native logs and raw tracker spool, summary and replay report. Use ignored `build/lsac/` for software evidence and `captures/lsac/` for future experiments. Do not commit raw ambient recordings or device configuration dumps to the source repository.

## Calibration inputs

`tap-fit <pairs.json>` takes explicitly paired `audio_seconds`, `imu_seconds`, and `groups` (`start`/`end`, 3–5 taps each). It estimates `audio = offset + rate*imu`, reports drift ppm, all residuals and rejected pairs. Event detection/pairing and sensor/filter/propagation delays are not assumed solved. Preserve a held-out trajectory. This is audio–raw-IMU checking, not proof of fused-pose timing.

`axis-fit <directions.json>` expects `measured_nominal_local`, `reference_local`, `common_axis_local`; nominal frame and known offset must already have been applied. It solves one angle only. A transverse reference source is required. It does not change a probe config or declare calibration complete. The common-axis/known-spacing mechanical model is accepted; no six-parameter fit is introduced.

Any `window` result is currently **arrival-time association only**, explicitly invalid for precision geometry until clock and dynamic alignment are validated. Never apply pyxdf dejitter to hide raw packet gaps. Geometric error from `v*dt` or `omega*dt` and phase error `2*pi*f*dt` are separate budgets.

## Source and runtime provenance

[upstreams.lock.json](upstreams.lock.json) records six original repositories, exact commits, root MIT license hashes, binary archive hashes and runtime versions. Current receiver sessions use locally built **878c74e...** plus the scoped Windows research patch; see `libsurvive-build-source.json`. Published v1.01 is historical diagnostic evidence only. ABI loading is pinned to the reviewed DLL hash. PyLSL's bundled liblsl is v1.18.0.b6; LabRecorder's release bundles a different 1.18 prerelease build. The successful local exchange is evidence of this tested combination, not universal compatibility.

Original sources stay in ignored `work/lsac-upstream`; binary releases and wheels stay in ignored `work/lsac-binaries` / `work/lsac-python`. No upstream code was copied into this module. Root project Apache-2.0 terms cover new adapter code; third-party packages retain their own licenses, including transitive bundled libraries. No redistribution package has been created.

Detailed phase plan, first capture results and unresolved hardware steps: 2026-11-01 progress (reference outside this source tree).

Receiver-specific next step (no HMD startup): pairing plan and one-shot command (reference outside this source tree). Timing statistic interpretation: raw packet analysis (reference outside this source tree).

## Capture thread scheduling

WASAPI acquisition and libsurvive poll/decode register on their own threads via
`AvSetMmThreadCharacteristicsW("Pro Audio")` and `AvSetMmThreadPriority(HIGH)`.
The current source-built Windows libsurvive defines `HID_NONBLOCKING`, so HID
reads execute on the registered poll thread. No process-wide realtime class or
MMCSS registry settings are changed. Writers, LSL forwarding and supervisors
keep their ordinary scheduling. Python's GIL and queue contention still apply.

`capture_scheduling` metadata preserves native thread ID, registration outcome,
task/relative priority and observed native priority before/after registration.
Registration failures abort capture; exit reverts MMCSS on the owning thread.
Windows dynamically controls the absolute priority: this is not a fixed level
20 or a guarantee of remaining above 15 under CPU pressure.

Local passive validation in `captures/lsac/mmcss-validation-001` observed both
capture threads at priority 25, then 9 after revert. Four-channel PCM and raw
IMU recording/XDF replay passed, with zero queue drops. This validates
registration and cleanup, not an improvement in sensor timing or RF losses.

The MMCSS 3-versus-5 tap trial (`captures/lsac/tap-alignment-mmcss-001/three-vs-five.json`)
found a 0.059 ms difference between group median offsets. First-three offset
prediction of the later five had RMS 1.55 ms and maximum 2.41 ms residual,
within the provisional 4 ms operational tolerance (one nominal IMU interval).
An isolated later candidate remains in raw data and is excluded explicitly
from the requested compact 3+5 groups. Some channels still clipped; onset
thresholds 20k/40k/80k gave the same selected-event times. This single trial
does not establish statistical equivalence or an MMCSS improvement.

`tap-policy.json` sets the operational default to **3 taps per group**, still
at both start and end of a longer recording. `tap_capture` saves the policy in
each session; drift fitting still requires at least six matched taps over time.
Ambiguous detection or validation residuals above tolerance require a repeat,
with five taps as the fallback. Original timestamps are never overwritten.

## Offline three-dimensional field preview

`Tools.ps1 field-model --help` rebuilds band-limited statistics from recorded
four-channel PCM and existing pose/calibration artifacts. `field-view` creates
a Three.js viewer with measured acoustic-centre points and optional locally
supported interpolation. These commands never open HID or audio devices.

Run `Prepare-FieldViewer.ps1 -OutputDirectory <field-output>` once to obtain
the four official Three.js r186 assets. SHA-256 checks and the MIT license are
retained in the output's `vendor` directory; runtime rendering uses local files.
Serve the generated output with
`python -m http.server 8766 --bind 127.0.0.1 --directory <field-output>`
and open `http://127.0.0.1:8766/`.

FOA processing requires an explicit experimental AmbiX assumption until USB
channel order, signs, normalization and device orientation compensation have
been verified. W is scalar; co-located XYZ components rotate with microphone
pose before covariance averaging. Directional correlations are not calibrated
physical intensity. Interpolation averages linear power, leaves unsupported
regions empty and does not solve a wave equation. Grid spacing is a display
parameter, not measurement resolution. See
field model details (reference outside this source tree).

The calibrated laptop view uses the palm-rest plane as horizontal, with the
screen angle derived from both measured plane normals. The physical screen
horizontal follows their intersection; the Tracker's unverified in-plane axes
are retained only as the original data coordinates. Pass
`field-view <field.json> --output <index.html> --calibration <bundle.json>`
to bind the view to the same tracking world. Body dimensions and keyboard
details are schematic; the lid opening angle uses the measured calibration.

`Tools.ps1 source-inversion --help` runs the experimental direction/virtual-source
backprojection separately from local-energy interpolation. It consumes the
field's full FOA covariances and same-world calibration, balances spatial bins,
samples a screen-facing 18-by-18 hemisphere and evaluates candidate source
support in a declared finite region. Source support is dimensionless and is
not an energy density, a probability, an absolute intensity or a verified
physical-source location. The explicit AmbiX assumption remains required.

Local-field analysis now defaults to `--mode spatial --spatial-cell-size .02`:
all samples from valid analysis segments contribute directly to occupied
2 cm cells in the palm-horizontal frame. Repeated visits improve each cell's
statistics without creating extra spatial neighbours. The <=4 ms integration
step approximates motion; it is not a point-selection period. The older
adaptive 100/20 ms mode remains available explicitly for comparison.
Optical/pose gaps and audio validity checks remain in force.

`multisource-fit` is the experimental four-region complex-covariance model;
unknown driver responses remain latent, coherent cross terms are retained,
and measured table geometry enters the propagation hypotheses. It does not
fit a cardioid emission pattern to the laptop or identify individual FR.
`field-view --table-reference <table.json> --multisource-fit <fit.json>` shows
measured planes, categorical nominal regions and residuals. See
multisource model and checks (reference outside this source tree).
`direction-check` compares static-rotation records without automatically
certifying channel conventions or angular accuracy. Playback preparation now
accepts fixed `--channel-gains 1 0` for the first stereo output channel, with
the other channel kept exactly silent and no system-volume change.
