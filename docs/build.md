# Build the Windows product

Run the [product build command](../README.md) from the repository root on Windows x64. Supply an explicit Python executable.

| Dependency | Expected location |
| --- | --- |
| MinHook at `c3fcafdc10146beb5919319d0683e44e3c30d537` | `work/minhook` |
| ROAR 1.1 at `b3051102efaab6a7c44399c9bc9f986a0f822a22` | Prepare `work/roar`; Croksor creates and validates `work/roar-product-1.1` |
| LLVM-MinGW 20261006 UCRT x86-64 | `work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64` |
| Zig 0.13.0 x86-64 | `work/zig-bootstrap/zig-windows-x86_64-0.13.0` |
| Rust 1.97.1 x86_64-pc-windows-gnullvm and Cargo registry cache | `work/rust/rustup` and `work/rust/cargo` |
| OpenVINO CPU development and runtime files for `-WasapiUpmix` | `work/joint-cpu-deps/openvino` |

Croksor validates and applies the in-tree ROAR patch. Populate the Cargo cache before running the offline builds. The local full build used preinstalled toolchains and a populated cache. The public CI bootstrap prepares the same module layout; see [CI scope](ci.md) for its selected features and validation status.

The build stages the FSA executable in `build/fsa-integration/bin` and the scheduler, receiver, Croksor and web assets in `build/nsp-product`. Browser identity requires Node.js at runtime when enabled. See the [README](../README.md) for capture compatibility and Node.js configuration.

The product entry point is `tools/Build-Product.ps1`. The retired `tools/Build-Alpha.ps1` is not a current product entry point. The build output still depends on the source
checkout and prepared dependency directories; it is not a standalone
installation package.


| Switch | Additional build inputs |
| --- | --- |
| `-MediaFoundation` | Source decoder and Windows MFT/router components |
| `-PcmCapture` | PCM capture and ingress, with profile generation and native checks |
| `-BrowserIdentity` | Implies PCM; browser backend and separately configured Node agent |
| `-Wasapi` | Stereo WASAPI output; does not itself imply PCM capture |
| `-WasapiUpmix` | Implies PCM/WASAPI; FreeSurround 480/240 and OpenVINO CPU Joint |

The CPU upmix SDK is separate from the base toolchain. Inference models and
LSAC measurement filters are not generated or bundled by the product build.
The public product omits extra AudioSrv enumeration and uses native Windows
format support. See [development](development.md) for ownership and tests,
and [release preparation](release.md) for independently deployable packages.
