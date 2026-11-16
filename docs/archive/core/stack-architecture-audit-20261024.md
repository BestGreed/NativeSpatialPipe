> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Technology stack, operation portal and active document review

2026-10-24, alpha-dev. The scope is the correspondence between local product entry, build list and active description. No new operating framework is added, and equipment/acoustic performance acceptance is not expanded. The only entry for the current architecture is [Current Architecture] (current-architecture.md).

## Check results

| Check objects | Actual entrance/evidence | Conclusion |
|---|---|---|
| FSA default schedule | `Build-Integration.ps1`, `native/nsp_playback.h`, Zig `backend.zig` | Default Zig; Python schedule is only explicitly compatible with comparison, playback configuration cannot be silently handed over to old Python |
| Endpoint preparation and enumeration | `pipeline.py` → `manager.py` → `native_enum.py` → Native controller/DLL | Python control still exists; AudioSrv enumeration is gone Frida import/agent.js, and capture are unified MinHook |
| Default session startup assist | `backend.zig` → `Run-GameTelemetry.ps1`, `host.zig` → `standalone.py` | PowerShell/WinRT and Python telemetry are still product assist dependencies and must not be mislabeled as testing use only |
| C++ module | `modules/audio-backends`, LIRA/ASIO build and this round of DLL import | Native, not loading .NET 10/WPF; the runtime library is deployed with the module, it cannot be said that there is no runtime library at all |
| PCM / DSP | `host.zig`, Rust `recording.rs`, `output_stream.zig` / `output_playback.zig` | DSP in the pool, descriptor rental; side recording is independent and turned off by default, does not restore disk transfer or Zig PCM scratch |
| Web / Telemetry | Zig `web_host.zig` and Python `service.py`, front-end package.json | Two independent service responsibilities; browser JS, Node only development and testing; no web session control |
| Windows Hosting Assistance | System.Runtime.WindowsRuntime / Add-Type for PowerShell | Existing system interoperability, not a .NET 10 desktop dependency; the "all code is native" description does not hold |
| Explicit EDID preflight | `edid.plan` / `inspect` → `work/OemEndpointProbe.exe` | With C# / Windows .NET Framework assistance, not only testing; complete source code and independent build entry, not migrated to native |
| Old C# client | `tools/control-client`, history ClientProbe / WwiseProbe | Optional fixture, marked history; does not participate in default product dependencies, does not export as new front end |
| Networking & WDM | Planning, Research and History Fixtures | Native AoIP/Inferno not plugged in; WDM full flow completion not in core, not mixed in with dependencies or current processes |

There are no new languages, package managers, web frameworks or resident services added this round. Keep the technology stack as C/Rust/Zig, C++ for abstracting modules, Python for preparation and telemetry, system PowerShell/WinRT, browser JS. Existing auxiliary dependencies cannot be masked as "no new technology overflows"; they are specified individually.

The public export re-examination found that `tools/edid-probe/edid_probe.py` was leaked, causing the EDID test import to fail. Read-only parser, pure synthetic tests, existing C# preflight source code and independent build instructions have been added; no machine-specific OG27 coverage scripts are exported, no automatic building/execution of probes or writing of EDIDs is done. This is a fix for existing dependency closures, not the addition of a new product technology stack. The legacy preflight nativeization is still a follow-up work, and it cannot be claimed that the preparation layer has been fully nativeized.

## Document correction and historical boundaries

- The current architecture page is rewritten as the current chain; the original disk map and early delayed data are moved to the history page. Historical measurements are not carried over to current round performance conclusions.
- Fixed reverse connection of enum descriptions: Enum OLR → Speakers/HookSpeakers, Enum OBR → Headphones/HookHeadphones. The FSA menu and test have been implemented in this way, and the menu code will not be changed in this round.
- Updated FSA and dependency notes: Python is used for both provisioning and telemetry; playback configuration is parsed from the user directory by default, and a blank INI field alone cannot be used to assert that ASIO is not enabled.
- Early FSA Sonic-only/strong locking suspends all rules, merge period writer/PCM staging, in-process telemetry, WebView2 shell and WPF routes, respectively marked as historical or deprecated; not restored to pending tasks because they are still available for review.
- Web control, automatic EDID, old limited ASIO playback timeout are not the current route. Implemented are read-only state and configuration saving, explicit HT Support, in-session resident output.
- In the future, ROAR OBR, 6DoF, native network output, capacity/retention policy, formal installation and release will remain as unfinished items and will not be claimed as completed by document cleanup.

## Validation and release scope

54 regressions in the preparation module for enumeration migration, AudioSrv real machine start/stop/recovery and read-only byte audit have been passed. The original build mode of the shared capture controller also compiles and passes. This review also completed public native enumeration DLL/controller builds, 54 public preparation module and 15 regressions in EDID parsing, legacy Framework preflight probe compilation, and document encoding/linking and clean export hash checks. Preflight probes are only compiled, not executed. There is no restarting FSA, turning on Hooks, writing EDID, or redoing audio quality/latency acceptance for documentation updates.

The core development branch is actually named `alpha-dev` (called dev by users), and the public repository is also `alpha-dev`; no additional synonym `dev` is created. The sync targets are core `alpha-dev`/`main`/`open-source` and public `alpha-dev`/`main`. Open source maintains an independent commit history and source manifest; no private experiments, machine configurations, original PCMs, or historical research workloads are pushed.
