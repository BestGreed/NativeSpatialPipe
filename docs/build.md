# Build the Windows product

Run the [product build command](../README.md) from the repository root on Windows x64. Supply an explicit Python executable.

| Dependency | Expected location |
| --- | --- |
| MinHook at `c3fcafdc10146beb5919319d0683e44e3c30d537` | `work/minhook` |
| ROAR 1.1 at `b3051102efaab6a7c44399c9bc9f986a0f822a22` | `work/roar-product-1.1` |
| LLVM-MinGW 20261006 UCRT x86-64 | `work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64` |
| Zig 0.13.0 x86-64 | `work/zig-bootstrap/zig-windows-x86_64-0.13.0` |
| Rust 1.97.1 x86_64-pc-windows-gnullvm and Cargo registry cache | `work/rust/rustup` and `work/rust/cargo` |
| OpenVINO CPU development and runtime files for `-WasapiUpmix` | `work/joint-cpu-deps/openvino` |

Croksor validates and applies the in-tree ROAR patch. Populate the Cargo cache before running the offline builds. The local full build used preinstalled toolchains and a populated cache. The earlier Windows CI bootstrap targets the previous tree.

The build stages the FSA executable in `build/fsa-integration/bin` and the scheduler, receiver, Croksor and web assets in `build/nsp-product`. Browser identity requires Node.js at runtime when enabled. See the [README](../README.md) for capture compatibility and Node.js configuration.
## Refresh an installed application tree

`Build-Product.ps1` produces `build/nsp-product`, but an installed application tree also loads Python/JSON/Web assets from its own `tools/` and `contracts/` directories. Those files are not build outputs, so a binary-only upgrade leaves them stale. Refresh them after every source update:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/Sync-AppRuntime.ps1 `
  -Target <installed app> -Source <source tree>
```

The script backs up every replaced file to `<Target>/_runtime-backup/<timestamp>/`, clears stale `tools/telemetry/__pycache__`, and verifies the installed telemetry stack with the target's own Python: every output layout in the installed catalog is pushed through snapshot/history and validated as `layout`, `capabilities`, `snapshot` and `history`, so a catalog/contract pair that disagrees with the source fails the sync instead of silently killing the service. An app whose `contracts/output-layouts.json` and `tools/visualization-contract/contract.py` disagree makes the telemetry service raise `ValueError` on the first snapshot flush and exit, leaving the 8765 page without live data.
