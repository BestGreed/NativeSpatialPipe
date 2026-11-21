# Output layouts

`contracts/output-layouts.json` is the single source of truth for NSP output
layouts. Every consumer is generated from it:

- `tools/Generate-OutputLayouts.py`: Rust presets
  (`tools/roar-bridge/src/layouts.rs`), Zig
  (`tools/zig-scheduler/layout_presets.zig`), C++
  (`modules/audio-backends/src/layout_presets.h`), Python
  (`tools/telemetry/layout_presets.py`) and JavaScript
  (`tools/visualization-frontend/layout-presets.js`).
- `tools/aes67-output/gen-layouts.py`: the SDP `channel-order` /
  `channel-labels` table used by the standalone sender and the scheduler
  network backend.

Do not hand-edit the generated tables; change the catalog and regenerate.

## Presence layouts

A presence layout names a ROAR loudspeaker layout directly: `channels` is the
published label order and no `map`/`downmix` is used. Examples: 5.1.2, 5.1.4,
7.1.4 (code 714), 9.1.6. Presence output is copied through bit-exactly.

## Derived layouts

A derived layout is an **NSP extension** declared on top of a ROAR presence
layout. Its catalog row adds:

- `roar`: the ROAR layout that is rendered (for example `Layout512`);
- `channels`: the published channel ids, in publishing order;
- `map`: contract indices that select/reorder the rendered bed into
  `channels`;
- `downmix` (optional): folds rendered channels into published targets,
  `[{"from": <roar index>, "to": [<contract index>, ...], "gain": <float>}]`.

The renderer always runs the `roar` layout. The bridge composes the published
block by copying the `map` entries first and then applying each `downmix`
fold; only fold targets are clamped to [-1,1] (a correlated full-scale sum can
exceed 0 dBFS), and the native pipeline report exposes `downmixClipped` so a
limited stream is observable.

## 4.1.2 (code 412)

Seven channels, published as `L, R, Ls, Rs, Ltf, Rtf, LFE`, derived from ROAR
5.1.2:

- `map = [0,1,4,5,6,7,3]`: selects L, R, Ls, Rs, Ltf, Rtf from 5.1.2 and
  publishes LFE last;
- `downmix = [{"from":2,"to":[0,1],"gain":0.70710678}]`: folds the ROAR center
  into L/R at -3 dB per channel (equal-energy split, ITU-R BS.775 style)
  instead of dropping it;
- folded L/R are clamped to +/-1 and counted; LFE and every other channel pass
  through unchanged;
- SDP `channel-order` is `SMPTE2110.(41,2)`. SMPTE ST 2110-30 defines no 4.1.2
  layout, so the token is an informative spelling only; the authoritative order
  is `a=comment:channel-labels`.

Network transport, encoding and the scheduler backend are documented in
[AES67 output](aes67-output.md).

## Adding a layout

1. Add a row to `contracts/output-layouts.json`: `id`, `code`, `roar`,
   `channels`, and for a derived row a `map` plus optional `downmix`
   (`downmix.from` is a rendered/roar index, `to` entries are published
   indices).
2. Regenerate the tables with `python tools/Generate-OutputLayouts.py` and
   `python tools/aes67-output/gen-layouts.py`, then commit the generated
   files.
3. The generators validate `map`/`downmix` ranges and fail fast on an invalid
   declaration.
4. Extend the tests that enumerate layouts
   (`tools/roar-bridge/Test-OutputLayouts.py`,
   `tools/telemetry/test_output_layouts.py`,
   `tools/zig-scheduler/desktop_playback_tests.zig`) and document any special
   semantics on this page.

## Background

- Layout preset design: `docs/archive/core/output-layout-presets-20261023.md`
- Output pool hand-off: `docs/archive/core/output-pool-processing-20261022.md`
