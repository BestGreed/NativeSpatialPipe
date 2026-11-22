# Public CI scope

The Windows workflow validates the cleaned public checkout using the current
`modules/` ownership and `tools/Build-Product.ps1` entry point. It is independent
of downstream development builds. It runs for public `main`/`alpha-dev`, pull
requests and manual dispatch, with read-only repository permissions.

## Jobs and inputs

The workflow pins its actions by commit and prepares the recorded LLVM-MinGW,
Zig and Rust toolchains, MinHook and patched ROAR 1.1 sources. Cargo fetch and
build use lockfiles and the same registry configuration. Builds then use their
prepared offline cache.

The job builds the core product, MFT, PCM, browser backend and WASAPI, then
FreeSurround 480/240. It runs the source/boundary and Markdown checks, FSA
build-time tests, Zig units, scheduler and web contracts, endpoint-preparation
and telemetry tests, synthetic profile extraction and frontend contracts.
Results and logs are retained; no runnable release is automatically published.

Joint CPU/OpenVINO and inference models are outside this hosted job. The local
candidate full build additionally covered `-WasapiUpmix` using a prepared SDK.
Model availability, inference acceptance and device playback remain distinct.

## Verification status

The refreshed candidate completed the full local product build with MFT, PCM,
browser identity, WASAPI and CPU upmix. Local checks passed: 22 isolated
scheduler tests, 17 web-host contracts, 51 endpoint-preparation tests, 36
telemetry tests, synthetic CAB/MSU extraction and the selected frontend tests.
Zig units passed with one explicitly skipped fake-ASIO-ABI case. Two test-only
DLL paths were aligned with the actual product output; production algorithms
were not changed for those tests.

The subsequent shared-backend/frontend refresh passed the language and
configuration regressions and 18 web-host contracts for each of the default,
Chinese and unknown-language fallback cases. The public ORT CPU library built
with explicit development dependency paths. The shared Rust initialization
fallback fixture passed against the private implementation with identical
imported source. These checks leave the hosted inference scope unchanged.

This records local reproduction of the workflow's build/test commands with
prepared dependencies. The refreshed GitHub-hosted cold bootstrap has not run
yet, and is not reported as green. It must be observed on the public candidate
before treating hosted CI as validated. Source checks, local contracts, real
device playback and release-package acceptance each keep their own evidence.
