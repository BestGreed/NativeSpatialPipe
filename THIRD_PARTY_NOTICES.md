# Third-party components

The root Apache-2.0 license covers original Native Spatial Pipe code and documentation. It does not relicense third-party code, license texts, or the upstream-derived ROAR patch.

| Component | Pinned source | Applicable notices |
|---|---|---|
| MinHook | [TsudaKageyu/minhook](https://github.com/TsudaKageyu/minhook), `c3fcafdc10146beb5919319d0683e44e3c30d537` | [Full upstream license](licenses/MinHook-LICENSE.txt), including its bundled disassembler notices |
| ROAR | [AOMediaCodec/roar](https://github.com/AOMediaCodec/roar), `b3051102efaab6a7c44399c9bc9f986a0f822a22` | [LICENSE](licenses/ROAR-LICENSE) and [PATENTS](licenses/ROAR-PATENTS) |

The current product build applies `modules/croksor/patches/roar-1.1-validated.patch` to the pinned ROAR 1.1 source. This patch contains the validated downstream rendering and workspace changes. Earlier individual patches remain as historical source material; the product patch list is maintained by `modules/croksor/Build.ps1`. The patch and modified upstream source remain subject to the ROAR license and patent terms; no upstream copyright notice is removed.

ROAR's copyright license expressly excludes a patent grant; its separate PATENTS file provides the AOM Patent License 1.0 subject to its own terms and scope. Apache-2.0 for this project's contributions does not replace those terms or grant third-party patent rights.

Cargo.lock files pin direct and transitive Rust dependencies. Their own licenses remain applicable. Before redistributing compiled binaries, collect and include the notices for the resolved Cargo dependency graph and the chosen LLVM/MinGW runtime, including libunwind.dll when bundled. This branch distributes source, lockfiles, patches and notices, including the explicitly bundled Inferno dependency sources described below, without compiled binaries.

Windows binaries, proprietary audio codecs, game audio and Wwise projects are not distributed in the public source branch. The alpha-dev source includes GPL-scoped ASIO SDK headers under modules/audio-backends/third_party/asio, with their original license.

## Bundled Inferno Windows integration

`modules/inferno-windows` contains the NSP-specific Windows source snapshot of Inferno, derived from lumifaza/inferno and BestGreed/inferno-windows. See its `NSP-INTEGRATION.md` and `NSP-SOURCE-MANIFEST.json` for origin revisions, local modifications and flattened dependency provenance. Inferno declares GPL-3.0-or-later OR AGPL-3.0-or-later; original license texts are retained under `inferno_aoip`. The root Apache license does not cover this source. Nested dependencies retain their own licenses, including MIT for the patched netdev crate. This source inclusion is not a claim that binary redistribution requirements have been completed.

## Final Spatial Audio integration

The FSA source under `modules/fsa-integration` derives from BestGreed/Final_Spatial_Audio revision `decba9b` and retains its own Unlicense in that directory. Its icon artwork is included with the project owner's authorization and retains its separate asset notice. See `modules/fsa-integration/README.md` and `modules/fsa-integration/native/assets/LICENSE.txt` before reusing artwork. The root Apache license does not override these notices or grant rights in third-party marks.

## Frontend listener model

The small listener model and derived wire geometry originate from Kenney Furniture Kit under CC0 1.0. See modules/visualization-frontend/assets/README.md for provenance and the original model hash. They retain their upstream dedication.

## Spatial ASIO / LIRA and ASIO device integration

Source by hikari-taritsu (rmj), preserved from 0.2.7-preview.14 and adapted into modules/audio-backends. See its LICENSING.md, source-manifest.json, upstream notices and third_party license files. LIRA retains Apache-2.0; ASIO device code/SDK retain their GPL-3.0-only scope. libsamplerate coefficients retain their own license and ORIGIN. The full desktop/installer project is excluded. Root Apache terms do not replace these licenses.
