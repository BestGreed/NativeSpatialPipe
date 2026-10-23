# Receiver startup diagnostics

> Historical, narrowly scoped startup evidence. The AppContainer diagnosis remains useful; old DLL paths and build status are not current deployment instructions. See [current architecture](core/current-architecture.md).

2026-10-17. This update changes startup diagnostics and owned-process regression tests. The packet-processing loop, shared-memory ACL and normal/MMCSS scheduling semantics are unchanged.

## Failure boundary

The reproduced initialization failure occurred when the Rust receiver reopened an existing named mapping, before its worker or renderer submission started. In AppContainer, direct calls to `OpenFileMappingW` and `OpenEventW` returned Win32 5 (access denied), although mapping creation had succeeded. Outside AppContainer, the same pre-existing DLLs and a fresh build passed. Both environments reported `TokenElevation=0`; administrator elevation was not necessary for this owned-process test.

The transport grants access to the creator user, SYSTEM and LocalService, without an AppContainer identity grant. Run these integration tests in a non-AppContainer environment; do not weaken the ACL. This finding is not an ASIO failure or evidence of an audio processing regression. The historical failure log lacked token details, so it cannot establish every property of the earlier execution environment.

## Diagnostics and compatibility

`nsp_receiver_start_error() -> uint64_t` returns the last startup diagnostic on the calling thread. Read it on the same thread immediately after a null startup result. The high 32 bits identify the stage; the low 32 bits carry an OS error when available. Each start resets the value to zero. Successful start does not imply later worker/MMCSS success; inspect the final receiver report as before.

| Stage | Meaning |
|---|---|
| 1 | Invalid arguments |
| 2 | OpenFileMappingW |
| 3 | OpenEventW |
| 4 | MapViewOfFile |
| 5 | Ring header mismatch |
| 6 | QPC frequency |
| 7 | Worker creation |
| 8 | Startup panic |

The Python wrapper reports the stage, OS error and loaded DLL path. Old DLLs remain compatible but cannot supply the new diagnostic. Rebuild to enable it. Use `--receiver-dll <path>` in the test suite to select an isolated build; `--mmcss` still controls the scheduling request independently.

## Validation scope

The source baseline was checked on Windows 11 build 26200, Python 3.12.10 x64 and Rust 1.97.1 gnullvm with locked offline dependencies:

- Rust contract unit tests: 4/4.
- Updated receiver suite: 10/10 in ordinary scheduling and 10/10 with MMCSS, outside AppContainer.
- Original DLLs: original five tests passed; the five new diagnostic tests require the new export and are skipped for old DLLs.
- Controlled shared-memory to actual ROAR file output: 32 packets per profile, ordinary/MMCSS output byte-identical to direct submission. This used one static object at 480 frames.

Tests cover held-slot ownership, malformed payloads, sequence/stream rejection, idle shutdown, argument errors, missing mapping/event, undersized mapping, invalid ring header, diagnostic reset and thread isolation. No game, live system Hook, audio device or service restart was used. These checks do not renew historical performance claims or establish long-duration, device playback, dynamic-object completeness or cross-build acceptance. Private logs and captures are not included in this public export.

The public worktree was also rebuilt independently with the same locked toolchain and a separate target directory: Rust tests passed 4/4, and receiver tests passed 10/10 with each scheduling setting. The MMCSS check explicitly selected the newly built DLL with `--receiver-dll build/native-receiver/release/nsp_native_receiver.dll --mmcss`. This validation reused the existing sender binary, whose source is unchanged and matches the source manifest; it did not rebuild the entire capture/render system.
