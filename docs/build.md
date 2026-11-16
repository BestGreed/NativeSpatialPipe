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
