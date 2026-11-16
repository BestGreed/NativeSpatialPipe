# Croksor

Croksor, which means Crossover, is responsible for NSp’s real-time audio processing and output handover. The Rust crate is named `nsp-croksor`, and the Windows product is `nsp_croksor.dll`.

| Source code location | Responsibilities |
| --- | --- |
| `src/input/` | Capture data interpretation, unified input alignment, deadlines and render wake-up |
| `src/render/` | Rendering backend dispatch, ROAR OLR input adaptation, LIRA DLL adaptation and layout definition |
| `src/dsp/` | Common output processing, limiting, optional upmixing and LSAC adaptation |
| `src/output/` | Pool block leasing and returning, output routing and AoIP handover |
| `src/observability/` | Telemetry tap and recording bypass |
| `src/packet.rs` | Real-time processing of assemblies, workers and existing C ABIs |

Build individually using [Build.ps1](Build.ps1). The fixed version and patches of ROAR 1.1 are verified by the build entry, and Inferno uses the built-in source code of the warehouse. `bridgeDll` in the product configuration should point to `nsp_croksor.dll` in this build. C ABI exports inherit existing names.
