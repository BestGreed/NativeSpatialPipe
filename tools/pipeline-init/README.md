# NSp front space pipeline initialization

The entrance is `pipeline.py`, which is called by FSA, NSp startup scripts and independent command lines. All commands output JSON, and return non-zero on failure. The control layer uses existing Windows Python; enumeration injection is performed by `build/enum-hook/nsp_enum_control.exe` and `nsp_enum_hook.dll`, shares a fixed version of MinHook with audio capture, and no longer loads Frida or JS injection agents. `inspect` still uses `work/OemEndpointProbe.exe`. Enumeration hooks and EDID writing require administrator privileges; the module does not trigger UAC on its own.

Run the `powershell -File tools/pipeline-init/Build-EnumHook.ps1` build; public builds can pass `-LlvmBin <LLVM-MinGW bin>`. A full FSA integration build also builds this module. The three AudioSrv interceptions are still independent of audiodg's PCM capture interception: the native injection and MinHook technology stacks are unified, and the life cycle or data of the two are not mixed. See [Native Enumeration Hook](../../docs/archive/core/native-enumeration-hook-20261024.md).

Explicit EDID `plan`/HT Support's preflight and `inspect` still require the old `work/OemEndpointProbe.exe`. It is a native .NET Framework tool for C#/Windows and can run the `powershell -File tools/endpoint-probe/Build-EndpointProbe.ps1` build; does not require .NET 10/WPF and is not part of the per-packet audio chain. The read-only parser is at `tools/edid-probe/edid_probe.py`, which does not use Frida. Don't mistakenly label this ready dependency as testing only.

## FSA access

Running `tools/fsa-integration/Build-Integration.ps1 -FsaOnly` generates `build/fsa-integration/bin/FinalSpatialAudio.exe`. Fill in `[NSP]PipelineInitScript` in the configuration script, and the default front-end mode is still Sonic.

The FSA main menu provides seven spatial modes; clicking will actually switch and read back the verification, and update the NSp's preformat at the same time. The four extension items of headphones/speakers are only displayed when the corresponding Enum is turned on and actually available; they are removed directly after the enumeration is turned off or the available state is lost. When running in other modes, NSp will wait for capture to stop and verify recovery before switching. **Advanced → NS Extreme** follows the material, font, DPI, positioning and interaction of FSA's self-drawn side expansion menu:

| Subkeys | Meaning | Configuration fields |
| --- | --- | --- |
| Expand Online | Keep enumeration persistent after exiting NSp/FSA | PersistEnumerationHook |
| Enum OLR | Speaker Mode Enumeration | HookSpeakers |
| Enum OBR | Headphone mode enumeration | HookHeadphones |
| Rec Hook | Turn off automatic persistence and revoke enumeration hook | Disable PersistEnumerationHook and recover |

### NSp Lock

**Advanced → NSp Lock** Immediately read the currently enabled spatial format in the working thread, save `LockedFrontend`, `LockEndpoint`, `LockFormatGuid`, `LockHookMask`, and then submit `NSpLock=1`. Locking is refused when spatial audio is off, the format is unknown, or the preferred endpoint does not match, instead of guessing from the old FrontendFormat.

The headphone format that relies on enumeration completes Expand Online and Enum OBR, and the speaker format completes Expand Online and Enum OLR; Sonic and home theater have no new enumeration requirements. If the corresponding enumeration is not enabled, there are no active hooks, and the service native declaration still supports the current format, zero new hook requirements will be recorded, and no additional injection will be done for the OEM/licensed mode that is already available natively. Retain other groups that the user has opened; healthy and scope-matching persistent residents are reused directly. During the lock period, these three dependency switches are maintained by snapshots, and Rec Hook can still be restored; starting again will reiterate the required dependencies. Canceling NSp Lock releases routing constraints, retains the current session and enumeration settings, and avoids revoking dependencies that are still in use.

The seven independent spatial modes of the main menu are combined into a single NSp entry:

| Pre-formatting | Locked entry |
| --- | --- |
| Windows Sonic | Sonic NSp |
| Dolby Headphones | Dolby Atmos B NSp |
| Dolby Speakers | Dolby Atmos L NSp |
| Dolby Home Theater | Dolby Atmos HT NSp |
| DTS Headphones | DTS:X B NSp |
| DTS:X Ultra | DTS:X L NSp |
| DTS Home Theater | DTS:X HT NSp |

Check Lock first; start NSp when clicking the only entrance or automatic rule hitting space mode. All spatial format rules under lockdown (including spatial format targets that were originally unavailable) are routed to snapshot format + NSp, with priority still determined by the original rule parser. Stereo/5.1/7.1 does not redirect. When a hit is made, first verify that the capture is stopped, and then apply PCM; after that, if the space rule is hit again, it will automatically return to the same front-end pipeline. Automatic mode is still available during locked NSp operation. Clarify that manual stopping will pause automatic rules, preventing immediate automatic restarts.

Lock configurations are retained across FSA restarts, but capture is not automatically initiated. When the hardware endpoint changes, the snapshot is retained and the NSP entry is disabled. It needs to be unlocked and re-locked at the new endpoint. The lock itself does not replace the actual active GUID, hook heartbeat, endpoint identity, and recovery checks.

After the first complete verification of NSp is successful, the tray icon corresponding to the preformatted format rotates in a circle (about 720ms) with the cached frame, and the timer is stopped after the end; subsequent status polling will not be played repeatedly. Follows the Windows client animation switch and does not play a success prompt when it fails or only receives a startup request. NS Extreme's expansion arrows are drawn at all menu levels.

The switch operation in the self-drawn menu keeps the menu expanded and checks Update immediately. The removed mode slides out, fades out, and collapses row heights laterally in about 160 milliseconds; during the collapse, the rows are no longer clickable or keyboard selectable. Re-enabling during removal will cancel the removal, and closing the menu will clear the animation timer. Removed immediately when closing Windows menu/client animation; high contrast and screen readers still use the system menu without self-drawn animation.

For example:

```ini
[NSP]
FrontendFormat=DTSXUltra
PersistEnumerationHook=1
HookHeadphones=1
HookSpeakers=1
```

Seven kinds of `FrontendFormat`: `WindowsSonic`, `DolbyAtmosForHeadphones`, `DolbyAtmosForSpeakers`, `DolbyAtmosForHomeTheater`, `DTSHeadphoneX`, `DTSXUltra`, `DTSXForHomeTheater`.

FSA first checks the capture startup conditions, prepares for pre-mode, and then starts the NSp. Format parameters are used throughout the native Zig scheduler, compatible Python scheduler, and `Run-GameTelemetry.ps1`. When the settings change, the corresponding enumeration hook is rebuilt; the NSP with the same settings is started again and reused, and is not uninstalled repeatedly. Keep the initialization startup entry after checking the group; this only allows initialization to be initiated. To actually support verification bypass, you must wait until the matching memory survival mark appears.

## Independent parameter call

In PowerShell at the root of the repository, `$endpoint` uses the traditional ID of the MMDevice, such as `{0.0.0.00000000}.{...}`:

```powershell
$endpoint = '{0.0.0.00000000}.{7209e4d4-431d-479b-bdba-8dc78c174b60}'
python tools/pipeline-init/pipeline.py inspect --endpoint $endpoint
python tools/pipeline-init/pipeline.py ensure --endpoint $endpoint --persist --groups headphones speakers
python tools/pipeline-init/pipeline.py prepare --endpoint $endpoint --format DTSXUltra --persist --groups headphones speakers
python tools/pipeline-init/pipeline.py status
python tools/pipeline-init/pipeline.py verify-hook --endpoint $endpoint --format DTSXUltra
python tools/pipeline-init/pipeline.py recover
```

`ensure` only maintains enumeration and does not switch modes; `prepare` switches and checks the actual active/default GUID. `headphones` is compatible with Dolby Headphones and DTS Headphone:X; `speakers` is compatible with Dolby Speakers and DTS:X Ultra. Home Theater is used via the native declaration/EDID path and is not included in these two hook groups.

The target must be the current preferred multimedia rendering endpoint. Resident is detached from FSA/NSp and continues running after the caller exits. The old resident is restored and exited when the preferred endpoint changes; FSA can be re-established according to user preferences after updating the endpoint. FSA checks for liveness every eight seconds, and when the AudioSrv instance changes, the resident rechecks the identity and version before attempting to attach. It does not restart the audio service and does not manage NSp capture hooks. "Persistence" here means that the process life cycle is independent; no self-starting tasks are installed.

Non-persistent tests can be called directly:

```powershell
python tools/pipeline-init/manager.py start --endpoint $endpoint --groups headphones --owner-pid $PID
```

This persistence is restored when the specified owner process exits. `prepare` also accepts `--owner-pid`; non-persistent and required when specifying hook grouping. The FSA non-persistent configuration takes itself as the owner and actively recovers after the NSp stops acknowledging. Repeated launches of the same endpoint, grouping and persistence settings are idempotent; different configurations require recovery first, a step already handled by the FSA menu.

## EDID initialization and recovery

The product trigger boundary is based on the 2026-10-23 decision (development reference is not provided with the open source code): EDID writing is limited to subsequent installation processes and explicit manual entries, resident check/`prepare` is not automatically written; successful overwriting will not be withdrawn with FSA/NSP exit.

FSA has been connected to the NS Extreme → HT Support manual portal (the development reference is not provided with the open source code). Local filtering and existing HT enumeration control menu status, no external process is created for status query. `pipeline.py edid-enable --endpoint <id> --output <new directory>` is executed only when the user clicks on an applicable endpoint that does not yet have HT capabilities: Bind the only active monitor through the ContainerId, check the parent device of the graphics card, reload the graphics card after writing and verify with a new process. This command does the actual writing and reloading and is not used for read-only diagnostics; failure results and recovery transactions are retained in the output directory.

2026-10-23 For the regression progress, please see OG27QH EDID preparing to return (development reference is not provided with the open source code). New transaction and driver reload orchestration tests, actual write readback, driver reload, new process capabilities and active/default query are respectively accepted; devices that have been declared cannot serve as a positive and negative comparison of "from unsupported to supported".

Call `inspect` first when using it for the first time. It returns GPU audio endpoint determinations, support queries for two home theater modes, `recommendEdidInitialization` and active display candidates. FSA or other frontends can prompt for initialization based on this. The sound endpoint and display must be paired explicitly by the caller and cannot be inferred just by "device number".

```powershell
python tools/pipeline-init/pipeline.py edid-plan --endpoint $endpoint --monitor-instance 'HKC27A7\5&26f91d2b&0&UID4357' --profile both --output experiments/my-edid-plan
python tools/pipeline-init/pipeline.py edid-apply --plan experiments/my-edid-plan/edid-plan.json
python tools/pipeline-init/pipeline.py edid-reload --plan experiments/my-edid-plan/edid-plan.json --adapter-instance 'PCI\actual graphics card instance ID'
python tools/pipeline-init/pipeline.py query --endpoint $endpoint --format DTSXForHomeTheater
python tools/pipeline-init/pipeline.py edid-restore --plan experiments/my-edid-plan/edid-plan.json
```

`--profile dtsx` supplements LPCM, DTS, DTS-HD; `atmos` supplements LPCM and MAT with object capability flag; `both` includes both. Preserve existing CRU metadata, non-audio blocks, and display timing. The plan saves the before and after EDID, all existing coverage values, and the original value of the target block; records the transaction intention before writing, and checks the device EDID hash, current block, and read back after writing. Stops when there is insufficient space, unknown layout, bad checksum, or someone else modified the block.

The app only writes the specified CTA overlay block. Display driver reloading is an independent explicit operation, and the screen may go black briefly; after restoring the EDID, it also needs to be reloaded to allow the driver to re-enumerate. `edid-restore` restores the blocks touched by this transaction without deleting other CRU settings. `recover` only restores the running enumeration hook and does not revoke the applied EDID. Neither EDID assertion nor active success proves that the physical device is capable of decoding or playback.

## memory survival contract

The shared mapping is `Local\NSp-PipelineInit-v1`, 2048 bytes, and the layout corresponds to `model.py` and FSA's `native/pipeline_init.h`:

| offset | type | content |
| --- | --- | --- |
| 0 | uint32 | seqlock sequence number, odd numbers indicate writing |
| 4 | uint32 | magic `0x4950534e` |
| 8 | uint32 | version 1 |
| 12 | uint32 | Status: 0 stopped, 1 starting/retrying, 2 ready, 3 withdrawing |
| 16 / 20 | uint32 × 2 | Resident PID; flags bit0 online, bit1 persistent |
| 24 / 32 | uint64 × 2 | GetTickCount64 heartbeat; process creation FILETIME |
| 40 / 44 | uint32 × 2 | AudioSrv PID; bitmask in seven formats |
| 48 | WCHAR[512] | Binding traditional endpoint ID |

The FSA must also check for consistent sequence numbers, versions, readiness status, heartbeats no longer than three seconds, PID live and creation times, endpoints, and selected format bits. The INI checkbox or the `ready` text on the disk cannot authorize bypassing. Only active tags corresponding to the current format can relax support preflight for that format; actual setup results, active GUID, endpoint identity, and capture recovery checks must still pass.

## Recovery and version boundaries

The hook is currently strictly bound to AudioSrv.dll SHA-256 `487e51035a76323032acd5996511c221c449cd244051d3b0e057eec70538ca97` (native 10.0.26100.8875). After Windows updates, if the hashes do not match, attachment will be refused and the address needs to be reanalyzed. Only the license query results of the specified endpoint and specified format are modified, and the service file or kernel driver is not modified.

The resident renewal interval is about half a second; the native watchdog cancels itself after the supervision process exits or the lease is not renewed for five seconds. `recover` requests to exit and compare the raw bytes of the three functions one by one. When the host exits abnormally, the same AudioSrv process instance is checked through read-only `ReadProcessMemory`, the runtime is not re-injected, and the old memory snapshot is not written to the new PID. A small Hook DLL and a disabled trampoline persist after undoing to protect calls that have entered the original function. Failure is returned when recovery cannot be confirmed, and `%LOCALAPPDATA%/NativeSpatialPipe/pipeline-init/resident.json` evidence is retained.

## Verify records

`experiments/pipeline-init-20261019` Save local verification: All four headphone/speaker modes under the same resident PID are set successfully, the caller is still online after exiting, the FSA native C reader confirms survival; the resident exits after the preferred endpoint changes, and the original byte recovery verification of the three functions passes. `owner-exit-fixed/result.json` verified that non-persistent owner exits are properly revoked even if the test side still retains the process handle. After the test is completed, the original mode and default endpoint are restored, leaving no running enumeration hooks.

`fsa-seven-modes-fixed/result.json` records the real-machine verification of the seven switching entries of FSA itself: all seven active/default GUIDs match, the same resident PID is completed, and then the original audio status and configuration are restored. Fixed an issue that required the AudioSrv SYNCHRONIZE permission, causing some contexts to be unable to check the service identity; now only use PROCESS_QUERY_LIMITED_INFORMATION to query the creation time and exit code, and the exited owner of the retained handle can still be identified.

The Universal EDID scheme has performed a read-only check on the existing OG27QH overlay with the same results; the card was not reloaded again for this module verification. Unit tests cover EDID retention/grouping/checksum/reject conditions and stale, bad endpoint, bad process and torn memory tags. Also checked via scheduler regression and FSA build. The pop-up menu test covers seven modes and four sub-item commands, keyboard operations and sub-menu geometry; the mouse track use case is explicitly skipped when the input desktop is unavailable. Full NSp audio quality validation for long dwell times, AudioSrv forced crashes, and each of the seven front modes has not yet been done.
