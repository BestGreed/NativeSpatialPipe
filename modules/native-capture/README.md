# Native capture module

This directory contains the production C implementations and build entry points for Native-Hook, Native-Hook-Control, Native-Capture, and Native-Send. The Zig scheduler uses Native-Hook.h from this directory. The enumeration hook shares the controller source.

Legacy audiodg support is selected at runtime from the verified build table in `contracts/audiodg-builds.json` (generated into `Audiodg-Builds.h`); the hook DLL receives the target RVA, capture vtables and prologue through `HookConfig`. The fixture layout and the separately compiled LNL profile keep their fixed offsets. See [docs/audiodg-build-profiles.md](../../docs/audiodg-build-profiles.md).

The compatibility Python entry points, analysis utilities, and owned-process test fixtures remain under endpoint-probe. Build scripts reference the fixtures by explicit paths. Output directories, DLL names, the ABI, and default build arguments retain their existing values. Building does not inject code into system processes.
