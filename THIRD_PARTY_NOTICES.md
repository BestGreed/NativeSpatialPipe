# Third-party components

The root Apache-2.0 license covers original Native Spatial Pipe code and documentation. It does not relicense third-party code, license texts, or the upstream-derived ROAR patch.

| Component | Pinned source | Applicable notices |
|---|---|---|
| MinHook | [TsudaKageyu/minhook](https://github.com/TsudaKageyu/minhook), `c3fcafdc10146beb5919319d0683e44e3c30d537` | [Full upstream license](licenses/MinHook-LICENSE.txt), including its bundled disassembler notices |
| ROAR | [AOMediaCodec/roar](https://github.com/AOMediaCodec/roar), `60c96e566c1dd2838468006b63ada716549f82b2` | [LICENSE](licenses/ROAR-LICENSE) and [PATENTS](licenses/ROAR-PATENTS) |

The ROAR patch in `tools/roar-bridge/patches/olr-block-mix.patch` modifies upstream object mixing to avoid a redundant accumulation for a single object while retaining the multi-object accumulation order. It was prepared for this project in October 2026. The patch and modified upstream source remain subject to the ROAR license and patent terms; no upstream copyright notice is removed.

ROAR's copyright license expressly excludes a patent grant; its separate PATENTS file provides the AOM Patent License 1.0 subject to its own terms and scope. Apache-2.0 for this project's contributions does not replace those terms or grant third-party patent rights.

Cargo.lock files pin direct and transitive Rust dependencies. Their own licenses remain applicable. Before redistributing compiled binaries, collect and include the notices for the resolved Cargo dependency graph and the chosen LLVM/MinGW runtime, including libunwind.dll when bundled. This branch distributes source, lockfiles, patches and notices, without dependency sources or compiled binaries.

Windows binaries, proprietary audio codecs, game audio, Wwise projects and ASIO SDK materials are not distributed here.

## Frontend listener model

The small listener model and derived wire geometry originate from Kenney Furniture Kit under CC0 1.0. See tools/visualization-frontend/assets/README.md for provenance and the original model hash. They retain their upstream dedication.
