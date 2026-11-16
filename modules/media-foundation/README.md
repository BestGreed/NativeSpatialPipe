# Source-built Dolby MFT and application routing

Experimental integration for Windows x64. The Rust backend compiles vendored EAC3/JOC and TrueHD sources; it does not load the precompiled Harletty DLL. The native COM wrapper implements synchronous preview and asynchronous spatial IMFTransform modes. Input codecs are DDPlus, TrueHD and AC3; the currently tested output contract is 48 kHz stereo PCM16/Float32.

FSA runs this optional module through the Zig scheduler when `mediaFoundation.enabled` is true in the playback JSON. The default is off. The integration build stages the controller and its DLLs beside FSA. Source builds, config dispatch and queue ownership passed isolated tests. Live injection and device playback with the merged build remain field checks. [Module integration](../../docs/core/lsac-module-integration.md)

The standard object sink is implemented through ISpatialAudioClient / ISpatialAudioObjectRenderStream. It receives an explicit presentation clock and endpoint through INspSpatialControl. The MF output remains stereo PCM: in spatial mode it carries silence with the real sample timestamps/durations, maintaining the player's timeline while the decoded objects go to the standard spatial stream. It is not an object-format MF output media type. The diagnostic stereo preview remains a separate mode.

Spatial mode defaults to asynchronous MFT events, with IMFMediaEventGenerator, unlock, shutdown, NeedInput/HaveOutput and complete-packet drain support. Only one input/output request is outstanding. HaveOutput waits for room in the bounded spatial queue, so a full queue does not turn ProcessOutput into a fatal backpressure error. Preview remains synchronous. Registration advertises the configured model; change mode before registering/starting a fresh player process.

## Build and tests

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File modules/media-foundation/Build.ps1 -Tests
python -B tools/mat-capture/TestSourceMftDecoder.py
```

The build defaults to the pinned local Rust/LLVM toolchain and locked offline Cargo dependencies. Use `-Python`, `-LlvmBin`, `-RustBin`, `-CargoHome`, `-MinHookDirectory`, `-RustTarget` or `-TargetDirectory` to select explicit local inputs and an isolated output. Use `tools/Build-Product.ps1 -Python <path> -MediaFoundation` to build and stage the FSA package. Python generates SDK COM signatures and runs diagnostic tests only; the deployed decoding/routing hot path is native C++ and Rust. Outputs are under build/media-foundation: NspDolbyMft.dll, nsp_mf_decoder.dll, libunwind.dll, NspMftRoute.dll, NspMftRouteControl.exe and the standalone test host. Copy nsp-mft.example.ini beside the DLLs as nsp-mft.ini; its routing and preview defaults are disabled.

The independent test host consumes BPK1 fixture packets and checks real IMFTransform negotiation, input backpressure, output PTS/duration, PCM, TEST_ONLY, flush, drain of complete samples, and COM unload. Source backend tests additionally split both real codecs into arbitrary 127-byte chunks and verify byte-identical PCM against original sample boundaries.

`-Tests` also builds NspMixerTest.exe and NspQueueTest.exe. The mixer host compares PCM, gain ramps, object bindings, Size and final block positions against the original sample-first algorithm; it accepts optional decoder DLL, JOC BPK and TrueHD BPK paths. The queue host stresses bounded ownership transfer, capacity wakeups and frame reclamation away from the audio consumer. See [the optimization results](../../docs/research/mft-hot-path-optimization-20261101.md).

## Registration and low-interference routing

The new unique CLSID is `{7C860C4B-817C-481F-AC76-6D4CC2B5549D}`. DllRegisterServer registers COM InprocServer32/ThreadingModel=Both and MFT audio-decoder input/output types. It refuses to overwrite a different DLL's same CLSID; DllUnregisterServer only removes a registration pointing to this exact DLL path. Machine registration requires an administrator process. Installation and uninstallation must use the same staged DLL path. No approval/signature/security-policy lists are modified.

Global enumeration is not sufficient to guarantee application selection. In local tests, the MFT appeared behind another decoder with ordinary filters and was excluded by APPROVED_ONLY. The documented IMFPluginControl preferred list is process-scoped, so setting it in an installer would not impose a universal priority on both players.

The configurable fallback uses an explicit native controller:

```text
NspMftRouteControl.exe watch ABSOLUTE_PATH_TO_nsp-mft.ini
NspMftRouteControl.exe stop  ABSOLUTE_PATH_TO_nsp-mft.ini
```

The config, route DLL, MFT DLL and decoder DLL must be staged together. This prototype expects that exact MFT to also be globally registered: local registration uses MFTRegisterLocalByCLSID. A configured controller watches new target process generations and loads the route through ordinary Win32 LoadLibraryW. It only accepts the installed Apple WindowsApps AMPLibraryAgent and Microsoft ZuneVideo Video.UI image paths, verifies process creation time and unprotected status, and rejects other targets. Failed attachments retry at most every ten seconds. It does not enable SeDebugPrivilege, allocate executable payloads, change process mitigation policies, patch on-disk application files or install a scheduled task/service/startup entry.

FSA invokes `ensure` through Zig for an enabled profile. It prepares Films & TV and Apple Music, enables Apple-only enumeration routing and presentation-clock binding, and reuses the native watcher. On disable or normal FSA exit, the `disable` command stops the managed watcher and unregisters its staged MFT. The scheduler writes `%LOCALAPPDATA%/NativeSpatialPipe/mft-status.json`. A `watch-ready` state describes controller readiness. Per-player attachment results are recorded in the staged `route-watch.jsonl`. The verified Apple Music → standard spatial → NSp → ROAR → notebook chain and scope are recorded in [the deployment evidence](../../docs/research/lsac-apple-music-persistent-20261102.md).

Configuration:

```ini
[routing]
enabled=1
appleMusic=1
filmsTV=1
hookEnumeration=1
hookAppleMusic=1
hookFilmsTV=0
bindSessionClock=1
```

Films & TV only needs process-local registration in the measured topology. Apple additionally needs MFTEnumEx interception to fill the name on its own activation and put that candidate first; the name retains the NSP identity and contains the substring matched by the tested Apple CoreMedia version. Other candidates stay present. Original filter/approval flags are preserved; LOCALMFT is added for matching audio queries, not APPROVED_ONLY removed. If the original enumeration excludes NSP, the hook does not invent an approved result. Only MFTEnumEx is intercepted; MFTEnum2 has not been implemented as a fallback.

`hookEnumeration=0` disables all enumeration interception, while retaining local registration. Per-app flags further restrict it. Setting enabled=0 makes the native worker unregister and disable its interception, and the controller exits. This is configuration-controlled persistence while the controller is running, not Windows-startup persistence. If the controller dies while enabled remains set, an already-loaded route continues watching that configuration in its target process; disabling the same file still stops it. Disabling an individual application flag also unregisters that application's route.

The route is conservatively retained in process memory after stop; it is not force-unloaded while a watchdog/callback/decoder could still refer to it. Existing transform instances are not invalidated by unregistering. Finish/switch the source before deleting a staged deployment. A process may retain COM class-factory caching until exit, so deploying a new build into a different directory does not guarantee that an already-running process uses the new DLL. Restart/reopen verification is a separate production gate.

Films & TV rejected loading the route from the tested ProgramData location but accepted its own package LocalState, consistent with prior experiments. The current test runner uses a unique LocalState staging folder and does not change its ACL. A final installer must select a stable application-readable deployment path and verify both applications; a universal ProgramData deployment is not claimed.

## PCM ownership and metadata

The IMF buffer is borrowed under Lock for one-buffer input; ConvertToContiguousBuffer is used only for multi-buffer samples. Rust owns decoded PCM and moves each decoded frame into a bounded queue. The C ABI front view borrows PCM, labels and metadata until pop/reset/destroy; the owned-frame take API retains that same allocation across decoder reset/destruction until explicit release; there is no PCM copy or JSON serialization at that boundary. A pending output applies input backpressure. Diagnostic preview converts directly into its final IMF output buffer. Upstream codec-internal buffering still exists; this is not a claim of end-to-end zero-copy.

Size remains in internal C ABI events and submitter poses; the standard Windows adapter omits it at submission. Object IDs and channel bindings are separate, per decoder instance. Flush resets decode/queue/time origin; ordinary pause does not imply flushing. TrueHD startup frames can lack a complete channel declaration, in which case missing labels are explicitly Unknown; the submitter uses a subsequent complete declaration from the same queued segment. The backend rejects unsupported rate changes rather than silently resampling.

## Standard spatial submission

For a controlled host, bind the actual IMFPresentationClock through INspSpatialControl. For the tested player topology, opt into routing.bindSessionClock=1 and spatial.enabled=1, set spatial.endpoint explicitly, and retain spatial.asynchronous=1. The route observes ordinary MFCreateMediaSession and session Start, then binds only the NSP transform found in that session's resolved topology. It never chooses a clock just because it is the latest in the process. Rebinding the same clock/endpoint/offset is idempotent, preserving queued data on resume. Protected-session creation is not intercepted.

Apple's targeted enumeration adapter also includes asynchronous candidates when this mode is enabled. Original approval/filter flags remain intact. Films & TV does not require the enumeration adapter in the measured synchronous selection experiment; spatial mode additionally needs the targeted session-clock adapter. Configured routing and spatial output are disabled by default.

The audio worker requests MMCSS Pro Audio, uses event-driven mono Float32/48 kHz buffers, converts ADM right/front/up to Windows x/up/back, and applies object gain ramps per sample. Position updates are quantized to the provider buffer period. Decoded PCM ownership moves from Rust into a queue capped at 96000 samples per signal; this is a two-second capacity ceiling, not a two-second playback wait. Conversion writes directly from the held i32 PCM to provider object buffers, with no intervening PCM array or JSON. Unsupported static beds use positioned dynamic objects. Insufficient object capacity or unsupported properties fail explicitly.

The queue uses fixed SPSC rings with 8192 ownership credits shared across queued, active and retired frames. A separate reclaimer releases Rust allocations and returns credits; the steady audio mixer holds no queue mutex and does not release decoded frames. Channel routes are cached per declaration, metadata events split processing spans, and positions are evaluated at the last contributing sample before each affected pose changes or the block ends. Statistics publish once per block. An armed capacity event replaces the asynchronous MFT's one-millisecond polling; its worker starts on stream start or clock binding, rather than type probing. Clock pause detection still uses a five-millisecond check.

Run tools/mat-capture/TestStandardSpatialSubmission.py with a fresh T: session directory and codec eac3 or truehd. The event-driven controlled host verifies preroll, pause, a new 60-second timeline, silent carrier, shutdown and COM unloading. Sessions 094/095 confirmed source-decoded JOC/TrueHD arriving at NSP under DTS:X HT: 16 slots, 480-frame blocks, game-properties-32 and nonzero PCM. This does not measure end-to-end audible latency.

Player session 096 additionally verified Apple Music steady submission (~20 seconds), pause/resume, and Films & TV JOC/TrueHD playback plus seek to 15 seconds, with both sources arriving at NSP through standard properties. The read-only AnalyzeStandardSpatialSubmission.py checks stream continuity and control transitions against the captured snapshots; 13 checks passed. Routing still requires an explicitly configured controller and presentation-clock adapter.

Session 102 repeats player coverage with the optimized DLLs in fresh processes: Apple Music pause/resume and an actual UI seek to approximately 99 seconds, Films & TV JOC pause/resume plus UI seek to approximately 64 seconds, and TrueHD seek while paused to approximately 56 seconds followed by resume. System clock starts were 98.000, 62.030 and 56.540 seconds; each path continued nonzero object PCM at NSP. All 23 checks passed, including the three staged DLL paths, registration removal, controller shutdown and observer restoration. `TestGlobalNspMft.py --films-stage --route --standard-spatial --exercise-controls --interactive-seeks` exposes bounded, externally acknowledged UI phases; only acknowledge an action after actually performing it. The analyzer checks elapsed-time-adjusted position changes, matching clock starts and continued nonzero NSP data. See [the optimization report](../../docs/research/mft-hot-path-optimization-20261101.md).

Remaining gates include exact output-bound trimming, incomplete-dependent-presentation drain/EOS, complete object retirement/EOS, full bed-gain/DRC policy, broad whole-player seek/pause/exit coverage, multi-rate support, malformed-input stress and deployment lifecycle. Current drain tests use complete fixtures and do not establish that buffered incomplete codec data can be flushed out. See docs/research/source-mft-spatial-submission-20261030.md for live player results and limitations.

See LICENSING.md and decoder/vendor/ORIGINS.json for source versions and license boundaries. No complete release distribution or automatic system installation is produced here.
