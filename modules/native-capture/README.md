# Native capture module

This directory contains the production C implementations and build entry points for Native-Hook, Native-Hook-Control, Native-Capture, and Native-Send. The Zig scheduler uses Native-Hook.h from this directory. The enumeration hook shares the controller source.

The compatibility Python entry points, analysis utilities, and owned-process test fixtures remain under endpoint-probe. Build scripts reference the fixtures by explicit paths. Output directories, DLL names, the ABI, and default build arguments retain their existing values. Building does not inject code into system processes.
