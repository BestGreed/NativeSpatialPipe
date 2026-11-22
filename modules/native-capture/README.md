# Native capture module

This directory contains the production C implementations and build entry points for Native-Hook, Native-Hook-Control, Native-Capture, and Native-Send. The Zig scheduler uses Native-Hook.h from this directory. The public controller contains the audiodg capture branch; the extra AudioSrv enumeration implementation is omitted.

The compatibility Python entry points, analysis utilities, and owned-process test fixtures remain under endpoint-probe. Build scripts reference the fixtures by explicit paths. Output directories, DLL names, the ABI, and default build arguments retain their existing values. Building does not inject code into system processes.

Runtime layouts and compatibility checks are described in [capture compatibility](../../docs/capture-compatibility.md).
