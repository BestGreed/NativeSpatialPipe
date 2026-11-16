> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Output layout presets and ROAR 6DoF slots

2026-10-23: Connected to the current `alpha-dev` workspace. Layout selection is a render output configuration and is independent of the 12-channel bed and number of dynamic objects captured at the endpoint.

## Entry and effective scope

The web front-end's "Configuration → Playback and Public DSP → Speaker Layout" provides the following seven items, and you can also directly modify the `outputLayout` that plays JSON. The omitted field is still `7.1.4`, and the configuration version remains compatible. The save is not automatically applied and will be read the next time the NSp session is started.

| Abbreviation | JSON value | Number of output channels |
|---|---|---:|
| 512 | `5.1.2` | 8 |
| 514 | `5.1.4` | 10 |
| 712 | `7.1.2` | 10 |
| 714 | `7.1.4` | 12 |
| 716 | `7.1.6` | 14 |
| 914 | `9.1.4` | 14 |
| 916 | `9.1.6` | 16 |

All seven items are applicable to `renderer: "roar", mode: "olr"` and `renderer: "lira", mode: null`. The selection goes through the actual renderer, Rust output pool, public DSP, output device routing, and telemetry; it doesn't just modify the interface label. The device output channels must be sufficient, and the existing 12-channel DVS network subscription will not be automatically increased to 14/16 channels.

## Channel sequence and wiring

The only configuration table is [`contracts/output-layouts.json`](../../../contracts/output-layouts.json), and the table for each language is generated through `tools/Generate-OutputLayouts.py`. Both backends use the same output order; algorithms and room behavior remain separate.

| Layout | Renderer stdout order |
|---|---|
| 5.1.2 | L R C LFE Ls Rs Ltf Rtf |
| 5.1.4 | L R C LFE Ls Rs Ltf Rtf Ltr Rtr |
| 7.1.2 | L R C LFE Ls Rs Lrs Rrs Ltf Rtf |
| 7.1.4 | L R C LFE Ls Rs Lrs Rrs Ltf Rtf Ltr Rtr |
| 7.1.6 | L R C LFE Ls Rs Lrs Rrs Ltf Rtf Ltr Rtr Ltm Rtm |
| 9.1.4 | Lw Rw C LFE Lrs Rrs L R Ls Rs Ltf Rtf Ltr Rtr |
| 9.1.6 | Lw Rw C LFE Lrs Rrs L R Ls Rs Ltf Rtf Ltr Rtr Ltm Rtm |

9.x follows the ROAR native 9.1.6 order. The two top channels are preset to use ROAR's front top channel pair and will not be changed to mid-top channels. The physical wiring is separate from the above table and is specified by `routing.physicalChannels`; the interface display starts from 1 and the JSON index starts from 0.

The length of `outputProcessing.trim`, `delayFrames`, `routing.physicalChannels`, and custom source arrangements must be consistent with the selected layout; neutral values are used when the calibration array is omitted. DSP is always processed in renderer standard channel order before device output is mapped.

When switching layouts in the form, keep the calibration and existing device wiring by channel name, use neutral calibration for new channels and assign unoccupied outputs; the arrangement returns to the standard order after switching. Directly editing the complete JSON will not be secretly rearranged, and saving will be refused if the length does not match. The old `dvs-speakers` preset only works in 7.1.4, other layouts use standard or custom routing.

## ROAR expansion and reserved slots

The pinned ROAR version does not have 7.1.6 or 9.1.4 natively. This time the actual target speaker geometry is added via a separate `olr-output-layouts.patch`; the object is rendered directly to this geometry. NSP fixed 7.1.4 bed mapping to new layout by speaker with the same name, without first rendering 9.1.6 and then discarding the channel. The extension only guarantees NSP current bed and object inputs and does not claim to cover all IAMF/HOA input combinations upstream.

Additional reservation `roar-custom-6dof`, and the following ROAR exclusive configuration metadata:

```json
"roarCustomLayout": {
  "version": 1,
  "measurementFile": null
}
```

`measurementFile` must be an absolute path if it is not empty. Currently, only slot metadata is saved, measurements are not read, geometry is not applied, and real-time tracking is not started. This layout is not selectable in the interface and is actively refused to be selected at runtime. Subsequently, the measurement contract, coordinate system, calibration and layout update boundaries need to be defined separately, and the reserved slots cannot be regarded as having achieved 6DoF.

## Validation and delivery boundaries

- ROAR/LIRA × seven layouts × bed/object, a total of 28 sets of offline actual rendering passes, covering output size, valid audio, telemetry packets and output pool return; there are also four sets of old 7.1.4 audio byte-by-byte compatibility control passes.
- 10 items of Zig configuration and 7 items of output pool handover passed; the snapshot, history and protocol verification of seven layout telemetry passed. Regression of 33 items of original telemetry and 15 items of protocol passed.
- The configuration page test covers seven layout switching, migration by channel name, physical routing anti-collision and reserved slots; 14 Web API checks passed, including seven layout save readback and reserved layout rejection.
- The current local default scheduler, ROAR bridge library and LIRA library have been replaced, and FSA has been restarted; the user's current layout, driver or network subscription has not been changed, and playback has not been automatically started.

This round did not conduct actual playback of the seven layouts of speakers, hearing verification or new delay stress testing; it is not claimed that the 14/16-channel physical network is connected based on this. The original diagnostic file name `roar-714.f32` is temporarily reserved for compatibility with old tools; when it is not 7.1.4, it must be interpreted according to the reported `outputLayout`/`outputChannels`, and the size cannot be inferred based on the file name.

For verification records, see `experiments/output-layouts-20261023` (development reference is not provided with the open source code).
