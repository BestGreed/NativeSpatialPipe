# Sonic NSp / FSA Integration Candidate

2026-11-08. Use `Build-Integration.ps1 -FsaOnly -MediaFoundation` when constructing FSA with optional MFT product. During runtime, the same playback JSON `mediaFoundation.enabled` is read, and MFT registration and player routing managed by FSA are started and stopped via Zig. Output is optional LSAC/WASAPI, endpoints and hooks are still managed by FSA. See [Module Boundaries](../../docs/runtime-details.md) and [MFT Build Instructions](../media-foundation/README.md).

2026-10-27 Native AoIP candidate: Play JSON using `outputBackend="aoip"` and `aoip` network configurations. The standby service is established when FSA starts, and the capture is mounted in the same resident process. Stopping Hook does not close AoIP; there is no PCM continuous mute, short-term underload buffering and counting. Real 10-second/60-second streaming and repeated stopping have been passed, but long-term SRC has not been implemented. For evidence of this stage, see Historical Verification Record (reference outside this source tree).

2026-10-24 playback location: `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` is now the default. The old installation-adjacent default is copied without replacing existing user data; explicit custom paths remain supported. See [migration](../../docs/archive/core/playback-user-location-20261024.md).

## Operating instructions (updated 2026-10-27)

HT manual provisioning, split of endpoint capabilities and PCM snapshot responsibilities, normal stop/emergency stop feedback, ASIO session persistence and independent Web API Host are turned on. The native FSA is responsible for modes, hooks, endpoint mutual verification, and start and stop; the Web only checks the status and verifies/saves the configuration. Click the tray three times in succession to open `http://127.0.0.1:8767/`.

HT Support is a manual EDID entry; normal startup/enumeration is not automatically injected, and the HT mode that lacks capabilities is hidden according to availability. The filtering and HT check status are handled by FSA enumeration. The preparation module is called only when the conditions are met and the capability is lacking. It is reloaded/rechecked after writing; the installation entry has not yet been implemented. See EDID menu (reference outside this source tree).

Playback JSON has support for seven layouts, DSP, wiring and two `recording` switches. The current Zig host's PCM/snapshots are not saved to the disk by default; if `PlaybackConfig` is not filled in, the default file in the user directory will be parsed first, and it will be compatible with the default file in the old installation directory. If there is still no configuration after parsing, only capture/telemetry will be taken. A blank INI field cannot be used to infer that ASIO is not enabled. Recording contracts for legacy Python controls and offline tools remain separate. For details, see [Playback Configuration](../../docs/archive/core/playback-json-configuration-20261022.md) and [Side Recording Isolation](../../docs/archive/core/recording-isolation-20261024.md). For the current AoIP network parameters, flow selection and validation methods, see User Configuration (reference outside this source tree). The date-marked paragraph below is a historical delivery description and cannot cover [active progress](../../README.md#goals-support-and-progress).

Current technology boundaries: FSA is C/Win32, default dispatch is Zig; Python is used for both endpoint readiness control and standalone telemetry. Extra enumerations have been migrated to C/MinHook and no longer call Frida. PowerShell/WinRT is still a preparation/selection/recovery aid, don't mistake it for packet-by-packet audio processing. See [Current Architecture](../../docs/archive/core/current-architecture.md).

2026-10-18. `decba9b` based on native `Final_Spatial_Audio-published`, sourced from
[BestGreed/Final_Spatial_Audio](https://github.com/BestGreed/Final_Spatial_Audio).
`native/` is an integrated copy of the submitted source code. For the original license, see `LICENSE`; the original repository or
`D:\Final Spatial Audio` file installed. The original upstream build instructions are still retained, and this page describes this new behavior.

## Build and run

DEV16 N12 adds optional `[NSP] PlaybackConfig=<absolute JSON path>`, using the same single-session playback contract as the desktop. `renderer=roar, mode=olr` or `renderer=lira, mode=null`, and explicitly specify the rendering bridge, ASIO module, driver, etc.; this early behavior of N12 has been replaced by user directory configuration parsing: when currently left blank, the user default file is still searched, and only capture/telemetry is performed only if there is no actual configuration, and recording is turned off by default. `Configure-Scheduler.ps1 -PlaybackConfig <path> -ControlExecutable <development host>` can be written to the specified INI; when the PlaybackConfig parameter is not provided, the existing value is retained and explicit null values are cleared. It is only passed during startup, stop/emergency stop does not depend on this file. This capability requires Zig and cannot be silently transferred to old Python scheduling. N12 builds and tests use isolated directories, and executing the old formal build/configuration commands on this page should still be scheduled according to deployment timing. For details, see N12 scope, evidence and remaining actual steps (reference outside this source tree).

This directory is the NSp-specific integrated source code and has not yet been returned to the FSA independent warehouse mainline. Only `[NSP] Scheduler=Zig` is supported from 2026-10-28, the old Python control scheduler and diagnostics have been archived; `Python` is still used for preparation/telemetry. When `ControlExecutable` is left blank, it will be parsed according to the formal build layout. If Zig is missing, there will be no fallback. Configuration migration clears old ControlScript and retains rules and snapshots without restarting the service. For deployment, startup sequence and verification, see [Zig Scheduling Instructions](../zig-scheduler/README.md).

The integrated version EXE is now embedded in the `requireAdministrator` startup list, and Windows will request administrator authorization when double-clicking it; there is no need to elevate the rights again when it is already started in the administrator context. The test EXE does not carry this privilege escalation list.

Only updating the FSA adds `-FsaOnly` to the build command and does not rebuild or overwrite the Hook/Telemetry DLL in use. The build directly generates and checks the official `build/fsa-integration/bin/FinalSpatialAudio.exe`. If the EXE is occupied, the build fails, the process is not closed, and a pending copy is not generated.

Execute in the NSp warehouse root directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File modules/fsa-integration/Build-Integration.ps1
```

The product is `build/fsa-integration/bin/FinalSpatialAudio.exe`. The build script prepares an independent running directory,
The first copy of the rules and snapshots of the installed FSA; each build writes the default Zig selection and EXE, telemetry Python, and old control script paths as `Configure-Scheduler.ps1`.
Additional rules and snapshots are retained for future builds. Compile Zig first for a complete build. `-FsaOnly` requires a Zig product. Audio DLL, Python, toolchain still use the native NSp repository,
This is not a release that can be installed out of the repository. FSA uses the original single-instance mutex and will not open another instance when the installation version is running.

Please use `modules/fsa-integration/Start Sonic NSp.cmd` to run, escalate privileges by Windows UAC and start Sonic NSp directly. You can also right-click and run the candidate EXE as administrator and select Sonic NSp.
The alternate command is `Start-SonicNSp.ps1 -Action Start|Stop|EmergencyStop`.

The menu displays **Sonic NSp** and the status icon uses the **FSA application's own icon**.
Uses the rotating stereo icon during initialization; switches to the FSA icon only after successful Sonic, endpoint, and hook verification. Animation frames are pre-generated according to the current DPI. The animation timer is turned off after success, failure or stop. The permanent ready state does not continuously refresh the icon.
After selection, normal rules are paused, target access permissions, protection levels, builds and dependencies are pre-checked, then Sonic is restored and verified, saved to a separate Sonic snapshot slot, and NSp is explicitly started. The audio will not be switched if the preflight fails.
Other supported audio modes remain selectable during runtime: stop the NSp after clicking, verify that hooks are disabled, original instructions restored, and backend wrap-up complete before applying the selected mode. The lock is retained on stop failure or unknown status. Automatic rule switching and intent capture remain suspended; Stop and E-Stop remain available.

## Persistence and state

The local user status is located at `%LOCALAPPDATA%\NativeSpatialPipe`:

| File | Function |
|---|---|
| `config.json` | Atomicly save audio configuration; restart to restore settings without automatically starting audio |
| `session.json` | Session ID, output directory, FSA owner and endpoint, status and errors |
| `owner.lock` | Control service process exclusive lock; different ports cannot repeatedly manage the same user status |
| `runtime.json` | Current back-end process, address and local command token; delete normally by exiting |
| `hook-status.json` / `.ini` | Actual query results; INI with request association ID and time, read back by the FSA disk module |
| `sessions/<id>/observed/hook-session.json` | Hook target PID and process creation time, used to prevent PID reuse and misoperation |

Configuration and status are separated: `idle → starting → running → stopping → stopped`;
The abnormality enters `faulted`. When the control service is restarted, it is found that the unfinished session enters `recovery-required`.
The reconnect page only restores the telemetry subscription and does not start the Hook. No data can be waiting/expired, and it cannot be hooked repeatedly based on this.

FSA only displays Sonic NSp if **Sonic selection, endpoint identity, and Hook job status all pass readback**.
Stop confirmation requires Hook to be disabled and the original command to be restored, or the original target process instance to have exited; the latter is explicitly recorded as
`targetExited`, restore readings without forging instructions. Locking continues while downstream closure and endpoint recovery have not yet completed.
New sessions do not inherit the last running state. Failure does not automatically re-hang; the user must explicitly stop and the recovery report is complete before it can be unlocked.

## Permanent, exit and emergency stop

FSA-managed sessions use `duration=0`, removing the fixed 20-minute cutoff and cumulative capture byte budget;
Original timed experiments are still available for a limited time. The audio side queue is still bounded. Residency does not imply long-term performance acceptance.

Hook's protection thread holds the FSA and native capture host process handles, and any exit will trigger disabling and draining.
Window/browser exit does not stop audio. Explicitly exit FSA by first requesting the audio to stop, waiting for the report, and emergency stop if necessary;
The final exit of the FSA process is still protected by the handle. The control service can be resident, and the audio session will not be automatically restored due to service restarts.

"Stop NSp" notifies the original orchestrator through a persistent stop file, and ends in the order of producer stop, downstream draining, and endpoint recovery.
"Emergency Stop NSp" dispatches the adapter independently from the interface, bypassing the waiting arbitration thread; first notifies the native Hook to stop,
Then query the disabled/original command to restore, stop the file and let the orchestrator continue to complete the downstream finishing. Stop timeout retention fault,
Will not kill `audiodg.exe` or force the destruction of receivers that are still in use.

Command line alternative entry:

```powershell
build/nsp-product/nsp-scheduler.exe status
build/nsp-product/nsp-scheduler.exe stop
build/nsp-product/nsp-scheduler.exe emergency-stop
```

The command should use the same native program that the current FSA INI's ControlExecutable points to; the path above is a current candidate example. Do not start the archive Python backend to diagnose the current session.
The launch endpoint and FSA PID are passed in explicitly by FSA, historical experimental endpoints are not used as the default for the FSA path.

## Validation and Boundaries

See Integration Acceptance Record (reference outside this source tree). Controlled acceptance and real Sonic switching, capture, normal stops, emergency stops and restarts all passed. The installation version has not been replaced yet, and long-term performance and cross-system compatibility acceptance are subject to separate review.

The new Hook is exported to `build/native-hook-managed-v2/nsp_managed_hook_v2.dll` separately to avoid overwriting the old build that already resides.
When there is an old `nsp_native_hook.dll` in the target, it must be verified by the original controller to stop, restore the original command, and release resources before reuse is allowed; mixed use is refused when the old Hook is active or cannot be verified. Different builds in the target are also rejecting calls at wrong export offsets.
In this case it remains in an unknown/faulty state and cannot be stopped by pretending to be complete by deleting the status file.

DEV16's `[NSP] PlaybackConfig` file can uniformly set public DSP, ROAR/OLR radius, physical routing and existing ASIO output strategies. The modification will take effect on the next startup, and endpoint mutual verification will continue to belong to FSA. For details, see [Playback JSON configuration and examples](../../docs/archive/core/playback-json-configuration-20261022.md). The old host does not recognize the new fields and should use the corresponding new build; the existing FSA instance is not replaced in this round.
## Web configuration portal (2026-10-23)

When FSA starts, it creates an independent Zig Web API Host with address `http://127.0.0.1:8767/` and does not automatically launch the browser. Left-click the tray three times in a row to open the original visual homepage with the default browser; a new "Configuration" entry is added to the homepage. Click/double-click to display the original menu after the multi-click window ends, and right-click to display the menu immediately. Web only queries status, verifies and saves existing JSON, and does not provide start/stop, Hook, EDID or other controls. The administrator FSA starts the HTTP Host using a normal user token; service failure does not affect the FSA. See [Implementation Boundaries and Usage Instructions](../../docs/archive/decisions/web-configuration-host-20261023.md) for details.
