# Alpha integration and public source scope

Updated 2026-10-24. N03-N12 integration remains complete within its recorded scope. Current additions include manual HT preparation and FSA state fixes, independent read/save Web configuration, shared seven-layout output, ASIO session residency and module-local runtime loading, isolated optional recording and user-directory playback configuration.

ROAR is a renderer backend; OLR is its currently integrated speaker mode. LIRA is a separate backend retaining room projection, clipping, smoothing and 512-frame inherent delay. Common input preserves raw metadata. Device clock matching and internal ASIO buffering remain device responsibilities.

Both PCM recording and telemetry snapshot archival default off. The playback handoff does not perform recording IO; telemetry routing uses memory progress. Remaining storage work covers quotas, retention and cleanup. The 6DoF layout slot is inactive; 14/16-channel physical playback, long-term performance and native network output are not completed. Web runtime control is intentionally absent; FSA owns it.

## Provenance

The export combines NSP core development, hikari-taritsu (rmj)'s Spatial ASIO preview.13/preview.14 contributions, common conversion/output/control integration and subsequent ROAR optimization. Original author, license and upstream notices remain. The source repository is https://github.com/hikari-taritsu/spatial-asio . Private contribution commits are retained in a separate development contribution branch; they are not imported as public Git history.

The public branch advances its own existing history. `core-source-manifest.json` records the private source revision and per-file normalized source/export hashes. Public build-layout adaptations and omitted private evidence links are identified; a source revision is provenance, not a promise that the private commit is available in this repository.

## Export boundaries

Included: capture/transport sources, Rust receiver and ROAR adapter/patch stack, extracted LIRA/ASIO modules, Zig coordination and DSP, FSA integration, telemetry/browser configuration and visualization sources, license materials and source-level test fixtures.

Excluded: raw PCM/capture/profiler streams, device-specific live configuration, credentials, private research payloads, Wwise projects, complete downstream engine, retired WPF/C# frontend and private-history comparison tests. Missing development evidence links are rendered as explanatory text rather than fabricated public artifacts.

The root README is English. Markdown is retained as UTF-8 and checked for lossy encoding. This is a development source update, not a binary release or a claim that all dependencies, devices and packaging have been certified. License scope remains Apache-2.0 originals plus GPL-3.0-only ASIO/SDK scope, with separate FSA/artwork terms.
