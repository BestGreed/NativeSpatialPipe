> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# ASIO module dependency deployment and startup repair

2026-10-23: Actual FSA launch after layout update exposed ASIO module deployment issues. Failed session `<development-session>` exited before hook injection, `host-stderr.txt` as `NSp Zig scheduler: FileNotFound`; the peripheral subsequently reported `Observer not ready` and unacknowledged normal stop. These are the consequences of host startup failure.

## Cause and fix

`NSP.AsioOutput.dll` exists, but its isolation directory is missing LLVM-MinGW's `libc++.dll` and `libunwind.dll`. The original module test was loaded by adding the compiler PATH to the child process and adding the DLL directory to Python, which did not cover the independent running environment of FSA. At the same time, Zig's default `DynLib.open` does not guarantee that its dependencies will be searched from the side of the target DLL, and simply patching the file still cannot be used as a reliable repair.

- Deploy the above two runtime libraries matching the build tool chain to the same directory as ASIO DLL.
- The ASIO adapter uses `native_library.zig`, converts the module path to an absolute path, and loads it as `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS`. On failure, the module path and Win32 error code are output, the global PATH is not expanded, and the ASIO real-time processing or buffering algorithm is not modified.
- The built product of `Test-DeviceModule.py` also carries the runtime library to avoid losing dependencies again when moving the product later. The open source build entry `Build-Alpha.ps1` already has corresponding copying steps.
- Added a new regression of isolated loading without opening the device: remove the compiler PATH, the working directory is different from the DLL directory; it will explicitly fail when dependencies are missing, the dependencies are complete but the old loading method still fails, and the new loading method can parse the real ASIO ABI.

## Actual measurement results

Isolated loading regression passed; six output control thread tests passed, and one test that required a fake driver DLL to be explicitly provided was skipped. After cleaning old failed sessions using safe exit and recovery evidence, replace the default scheduler and restart FSA, preserving the user's playback JSON, DTS:X HT, ROAR/OLR, 7.1.4 and DVS wiring.

New session `<development-session>` actually enters `running`, FSA is `nspCode=0`, `NSp frontend: verified`, no startup error; the session remains open waiting for audio. This result confirms that the actual device module and driver are successfully started, but does not mean that the listening experience of the current round speaker has been verified.

The previous round of seven-layout offline rendering and configuration testing did not open the actual ASIO device and cannot replace the above running environment check. The original failure log is retained, and there is no way to bypass exit verification by deleting the status file.

For verification materials, see `experiments/asio-startup-fix-20261023` (development reference is not provided with the open source code).
