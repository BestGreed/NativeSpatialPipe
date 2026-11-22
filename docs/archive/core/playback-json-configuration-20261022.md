> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# DEV16 merge function and playback JSON configuration

2026-10-24 playback location: `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` is now the default. The old installation-adjacent default is copied without replacing existing user data; explicit custom paths remain supported. See [migration](playback-user-location-20261024.md).

2026-10-24: Two independent switches `recording.pcm` and `recording.telemetrySnapshots` have been added, both are turned off by default, and the Web configuration page can be edited; for details, see [Decoupling of recording and playback] (recording-isolation-20261024.md).

2026-10-23: Added seven optional output layouts and ROAR 6DoF custom reserved slots, see [Output Layout Configuration] (output-layout-presets-20261023.md) for details. The original fixed 12-channel example on this page corresponds to the default 7.1.4; the calibration and routing arrays of other layouts are configured according to the selected number of channels.

Update 2026-10-23: A web graphics configuration page has been added to cover the existing fields in this article. FSA starts the independent Web API Host, triple-clicks the tray icon to open the original homepage, and then clicks "Configure". The page verifies and saves JSON without controlling the session; it will be read the next time it is started. See [Web Configuration and Status Host](../decisions/web-configuration-host-20261023.md). In the early part of this article, "no new interface controls" refers only to the current round of 2026-10-22.

In this round, the already implemented control items will be connected to the same playback configuration, ASIO scheduling/delay optimization will not be expanded, and no new interface controls will be added. The configuration is uniformly parsed and verified by `desktop_playback.zig`, and then handed over to the actual host. The old configuration retains the original default behavior when new fields are omitted.

Follow-up update on the same day: The public DSP has been moved to the Rust output pool block, `outputProcessing.bypass` has been added, and the Zig PCM queue and scratch have been changed to pool block leasing and handover. See [Output Pool Handling and Ownership] (output-pool-processing-20261022.md) for details; ASIO internal buffering/clocking algorithm remains unchanged.

## Entry and effective scope

- FSA: Set `[NSP] PlaybackConfig=<absolute path to play JSON>` in your INI; use the Zig scheduler to read and submit the session on startup. Endpoint preparation, locking and real-time mutual verification continue to be managed by FSA.
- Control Client: The `playback` object in the `start-session` request uses the same set of fields. C# `PlaybackSettings` is retained as a reference for existing client implementation and testing; the temporary WPF desktop has been deprecated, and advanced setting controls such as DSP will no longer be added to it. Public JSON and control protocols continue to be used.
- Direct host/start: `--playback-config <absolute path>`.
- JSON is an independent playback configuration, not a global state file for the control backend; do not edit the `session.json` or telemetry files to modify settings.

Fields take effect when a new session is started. Stop the current session before modification and restart after modification; running DSP or devices will not be hot updated. The session saves the parsed configuration and default values, and the output report saves the actual processing and routing settings.

Candidate check command (will not start endpoints, hooks or ASIO):

```powershell
& './build/json-feature-controls-20261022/nsp-scheduler.exe' validate-playback --playback-config 'D:/path/to/playback.json'
```

This command checks field, range, array length, and combination constraints and prints default values and valid channel order; there is no guarantee that the DLL exists, that the export is complete, or that the driver supports a certain sample rate/buffer size. The runtime continues to check for these conditions. The file uses UTF-8 and supports UTF-8 BOM; UTF-16 is not supported. Unknown field error.

Example: [Neutral ROAR/OLR configuration](../../../modules/zig-scheduler/playback.example.json). First replace the DLL path and driver CLSID. The editor can associate [JSON Schema](../../../modules/zig-scheduler/playback.schema.json); do not insert `$schema` fields into the playback file, and unknown fields are strictly rejected at runtime. Finally, it is subject to runtime verification.

## Correspondence between functions and fields

| Merged Capabilities | JSON Fields | Default Values/Constraints |
|---|---|---|
| Rendering backends and modes | `renderer`, `mode` | `roar` / `olr`; LIRA uses `lira` / `null`. Old `renderer: "olr"` Compatible with abbreviation only |
| Public Rust bridge library | `bridgeDll` | Required absolute path |
| LIRA independent rendering library | `rendererDll` | LIRA required absolute path; ROAR not used |
| ASIO output library and driver | `asioDll`, `driver` | The absolute path is required; the driver is `asio:<CLSID>` |
| Capture stream selection | `sourceStream` | `0`, 0–3 allowed; is the capture stream number, not the device channel |
| Device sampling rate and buffering | `sampleRate`, `bufferFrames` | `48000` / `0`; sampling rate 8000–384000, buffering 0–65536; 0 follows the driver selection and is ultimately confirmed by the device |
| Public output master volume | `master` | `1`, linear multiple 0–16; retain the original root field and do not import nested objects |
| Public DSP explicitly skipped | `outputProcessing.bypass` | `false`; true skips master/trim/delay/limiter, retains the configuration but does not apply to this session; neutral configuration automatically skips |
| Per-channel trim | `outputProcessing.trim` | 12 linear multiples, default all 1, each item 0–16 |
| Speaker calibration delay | `outputProcessing.delayFrames` | 12 integers, default all 0, each item 0–48000, based on source 48 kHz frames |
| Optional peak limiter | `outputProcessing.limiter.enabled` | `false` |
| limiter peak upper limit | `outputProcessing.limiter.ceiling` | `1`, range (0, 1], linear full scale |
| limiter release time | `outputProcessing.limiter.releaseMs` | `50`, 0–10000 ms |
| Optional lookahead | `outputProcessing.limiter.lookaheadFrames` | `0`, 0–4800; only takes effect when limiter is turned on, adding frames / 48000 seconds delay |
| ROAR/OLR radius mapping | `positionMapping.mode`, `positionMapping.reference` | `scaled` / `1`; reference is limited and greater than 0. Compatible with `direction` requires reference=1. LIRA disable this object |
| Naming channel order | `channelOrder` | `olr`; also `dvs-speakers` or `custom` |
| Custom source channel order | `routing.sourceChannelIndices` | Settable and required only for `custom`, 12 0–11 indices; allows explicit duplication of source channels |
| Physical output selection | `routing.physicalChannels` | Default 0–11; 12 non-repeating 0–255 indexes, and the actual channel number must be met after the device is turned on |
| Device clock matching | `clockSync` | `true`; performed by the device output module, the renderer no longer compensates for the same error |
| Bounded start prebuffering | `prebufferFrames` | `0` Automatic; explicit value 0–48000, in source frames and not less than the device's minimum Prime threshold |
| Startup timeout | `startupTimeoutMs` | `5000`, positive integer |
| Rebuffering timeout | `rebufferTimeoutMs` | `1000`, positive integer |
| Maximum number of rebuffers | `maxRebuffers` | `3`, non-negative integer; 0 does not allow resume playback |
| Final drain timeout | `drainTimeoutMs` | `5000`, positive integer |

DSP trim, calibration delay and lookahead follow the source side **48 kHz / 12 channels** and do not change the units with the device `sampleRate`. Time/count uses u32; the array must have exactly 12 entries per channel, short arrays cannot be used to make up for the default. Nested objects can provide only some fields, and default values will be used for omitted items.

## Channels and Processing Order

The standard order in which two renderers are delivered to the common output layer is:

`L R C LFE Ls Rs Lrs Rrs Ltf Rtf Ltr Rtr`

`trim` and `delayFrames` are always filled in in this order. Public DSP is executed after rendering and before device routing; `master` is the output volume, not the object attribute gain. The limiter is a sample peak limit associated across channels and does not commit to a true-peak limit.

The same index in the routing array represents the same output slot: `sourceChannelIndices[i]` selects the processed standard source channel and sends it to `physicalChannels[i]`. Both indices start at **0**, with output 1 from the device panel corresponding to physical index 0. Swapping routes does not change the semantics of the DSP array.

`dvs-speakers` uses the actual wiring confirmed previously:

`L C R Rs Ls Rrs Lrs LFE Ltr Ltf Rtf Rtr`

Corresponding source index `[0,2,1,5,4,7,6,3,10,8,9,11]`. The physical output still defaults to 0-11; if you only change the physical socket, you can keep the naming order and set `physicalChannels` separately. Any source sequence uses `custom`. For example:

```json
{
  "channelOrder": "custom",
  "routing": {
    "sourceChannelIndices": [0,2,1,5,4,7,6,3,10,8,9,11],
    "physicalChannels": [0,1,2,3,4,5,6,7,8,9,10,11]
  }
}
```

This is a snippet that adds to the complete configuration. Confirm the speakers according to actual wiring before modifying the physical output.

## Radius, recovery, and new switches

`scaled` uses source coordinates/reference to enter OLR and retain distance information; the meaning of reference is the scale corresponding to OLR radius 1 in the source coordinates, and the room metric size is automatically derived without JSON. `direction` is only used as a comparison with the old mapping. When the configuration explicitly provides `positionMapping`, it must not be repeatedly overridden with positional command line parameters; LIRA maintains its original spatial behavior.

Output underload is still handled according to the existing bounded re-buffering policy; the device is disconnected, an error is reported, or a reset request is stopped safely, and the device is not automatically replaced. Capture transmission out of sequence/corruption is still a fatal error and cannot be changed to packet loss resumption through these output fields.

There are no fake configuration entries for the following: object gain extra multiplication, ROAR slot GC, LIRA room projection/negative height clipping switch, capture checkpoint recovery, endpoint polling service, Inferno network sending. LIRA's original behavior continues to be encapsulated in a dedicated backend. Capture initialization and FSA mutual validation boundaries remain as planned.

## Version and verification boundaries

The new host accepts the old playback JSON; the old host does not recognize the new fields of this round. The new client and host should be built as a set, and existing running test instances should not be replaced. The compatibility here is the compatibility of behavior when new fields are omitted, not the compatibility of sending new fields to old programs.

Verification entry: `tools/desktop/Test-PlaybackJson.py` and `tools/desktop/Test-Desktop.py`. The former uses its own capture fixture, real host, and original simulated ASIO driver to compare the callback PCM with the offline DSP and check native FSA forwarding; the latter checks C# field serialization and session ownership/stop behavior. Real driver hearing, GUI advanced controls, and continuous performance verification are not part of this configuration wiring acceptance.

Results of this round: 7 configuration checks and 7 public DSP checks passed; the three callback PCMs of old configuration, ROAR/DSP, LIRA/DSP/custom routing were consistent with offline processing byte by byte; 9 types of incorrect configurations were rejected before session creation; native FSA forwarding and C# client regression passed. The evidence is in the form of JSON wiring results (development reference not provided with the open source code) and desktop control regressions (development reference not provided with the open source code). They are all isolated fixture verifications and do not mean they have been deployed to existing running instances.
