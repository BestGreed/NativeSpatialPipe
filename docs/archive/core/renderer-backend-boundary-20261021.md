> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# DEV16: Rendering backend and mode boundary correction

2026-10-21, retroactive revision of terminology, configuration, and actual distribution of N03–N11. Previously, OLR and LIRA were placed in the same renderer enumeration, confusing the backend and mode; this is not just changing the interface copy.

## unified contract

```text
NSP capture and transport → public raw input
  → ROAR backend → OLR mode and its input adaptation → PCM
  or LIRA backend → dedicated input adaptation and autonomous core → PCM
→ Common output processing → ASIO device output
```

ROAR also supports other modes; this development integration only connects OLR and does not declare that other modes have been connected to the new playback path of NSP. The first version of LIRA had no external sub-mode options, no OLR or native modes for it.

Public output responsibilities are independent of mode, but this handover layout is still limited to connected 48 kHz, 12-channel 7.1.4; this does not mean that other output layouts or headphone modes of ROAR have received the same interface adaptation and verification.

| Scope | Exact meaning of attribution and existing rounds |
|---|---|
| Capture raw attributes, PCM, full XYZ/gain fields | N03 public input contract without first OLR type/coordinate verification |
| `olr_input.rs`, type conversion, coordinate axis change, direction/scaled/reference, gain passthrough | ROAR's OLR adaptation; N09's default radius mapping change is limited to this path, and does not extend to other ROAR modes or LIRA |
| Identity correlation, parked, room projection, negative height clipping, quantization/smoothing, 512 frames front and rear | N10 LIRA dedicated adaptation and core, not a public rule or ROAR patch |
| Reliable writer/sink, master volume/trim/delay/limiter | Common handover and output processing after each backend; the first round of OLR verification of N06/N07 does not make them OLR modules |
| ASIO driver, device clock, Prime/underrun recovery, backend control worker | Public devices and coordination capabilities of N04–N08, independent of rendering backend and mode |
| FSA, temporary desktop | Session control entry; passes the backend and its mode, does not have the renderer's sound algorithm |

## Configuration, distribution and reporting

- New configurations use `renderer: "roar", mode: "olr"`, or `renderer: "lira", mode: null`. ROAR uses the current OLR default when mode is omitted. LIRA does not accept OLR mode; unconnected ROAR mode is explicitly rejected and cannot fall back silently.
- host uses `--renderer roar --renderer-mode olr` or `--renderer lira`. The old `--renderer olr`/JSON `renderer: "olr"` is only used as a compatible abbreviation for ROAR/OLR; the old `spatial` CLI alias still points to LIRA.
- The running report uniformly gives `renderer: "roar", rendererMode: "olr"` or `renderer: "lira", rendererMode: null`. Rust's internal distribution distinguishes `Backend::Roar(RoarMode::Olr(...))` from `Backend::Lira(...)` and no longer defines the third OLR backend.
- The interface displays the rendering backend and current mode respectively. `mode` in the protocol is the rendering mode, and `spatialFormat` is the Windows capture endpoint format. There is no corresponding binding relationship between the two.
- `channelOrder: "olr"` is still the name of the existing 12-channel arrangement that can be output by both backends; it is not a backend selection. DLL names, C ABI symbols, and existing `roar-714.f32` file names remain compatible. It cannot be inferred who rendered the data based on these names.

The existing evidence for N03/N07/N08/N09 only proves the ROAR/OLR path and its corresponding public interface; the original comparison of N10 only proves LIRA detachment. Historical reports and recordings will not be rewritten, and local successes will not be expanded into acceptance of the entire ROAR. Validation of hardware, endpoint switching and other combinations of N12 remains as planned.

## Correction and re-inspection

- Rust bridge, Zig host, WPF desktop and control probe independently built and passed.
- Three configuration tests passed: ROAR/OLR and LIRA, old olr abbreviation, error mode, and illegal configuration rejection.
- The isolation control chains of the two HTs pass: respectively verify the backend/mode forwarding of ROAR/OLR and LIRA, the original owner/stop boundary and the old FSA no-play field request. The test pairing of HT and renderer only covers the use cases, not the binding relationship. Evidence: `build/dev16-n11-desktop/runs/1792589836545868700/checks.json`; same directory `desktop.png` Visually inspected backend and schema layering.
- `tools/roar-bridge/Test-RendererBoundary.py`: ROAR/OLR direction, scaled The two groups are consistent byte by byte for the N09 old DLL, LIRA is consistent byte by byte for the N10 original control recording; the new report renderer/rendererMode is correct. Evidence: `build/dev16-renderer-boundary/1792589833697004600/checks.json`.

These are limited retests of layer corrections and do not replace the remaining N12 combination verification or acoustic/physical acceptance; there are no new rendering capabilities or deployments.
