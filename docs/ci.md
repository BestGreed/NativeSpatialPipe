# Windows CI build

The `Windows source build` GitHub Actions workflow builds public `main` on a fresh `windows-2022` runner. It also accepts manual runs and pull requests targeting `main`.

The bootstrap downloads hash-checked LLVM-MinGW and Zig archives, installs the pinned Rust toolchain, checks out pinned MinHook/ROAR revisions and fetches Cargo.lock dependencies. The normal source build then uses locked/offline Cargo builds. No local development checkout, private capture, audio driver or credential is required as a build dependency.

Coverage includes the capture/transport DLLs, Rust receiver and ROAR bridge with its patch stack, LIRA, ASIO, Zig scheduler/Web Host, native FSA, native enumeration and the existing Framework preflight probe. Source-level and owned-process checks use synthetic input. Logs and an expected-artifact hash list are retained as Actions artifacts for seven days.

The `windows-development-bundle` artifact also retains compiled binaries in their repository-relative locations, matching public source, pinned dependency sources and runtime notices for seven days. `CI-MANIFEST.json` records the workflow commit, run ID and SHA-256 of every included file. Download and extract it into a separate directory for local validation; it is a development bundle, not an installer or release. Python is still needed for preparation and telemetry tools. Audio drivers are not bundled.

This checks buildability and isolated behavior. It does not open an ASIO driver, inject AudioSrv/audiodg, write EDID, restart display/audio services or establish real-device playback, acoustic latency or long-run acceptance. No binary release is published by this workflow.

CI automation is maintained directly in the public repository. The provenance manifest continues to describe exported runtime source; public CI-only files do not claim a private source origin.
