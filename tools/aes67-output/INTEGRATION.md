# AES67 sink — minimal NSP integration design (not compiled)

> **Status: design draft only.** It was **not compiled or run inside NSP**: this
> machine has no Zig 0.13 / Rust / pinned LLVM-MinGW toolchain, and the branch
> deliberately does not attempt a full NSP build. The standalone transmitter
> itself is built and tested (`Test-Aes67.py`, 28/28 PASS).

## 1. Where it plugs in

Current audio path (`tools/zig-scheduler/`):

```
main.zig:37            host subcommand
host.zig:304-326       if --asio-driver / playback.driver -> playback.Session.create
host.zig:322             output_playback.Session.create  (open/start ASIO worker)
host.zig:323             pipelines[source].bindOutput(audio.stream)
native.zig:132-140     Pipeline.bindOutput -> output_stream.Stream (32-slot lease ring)
output_stream.zig:28     slots = 32, frames = 480, channels = 12
output_stream.zig:166-183  processPool() called by the Rust writer (in-place DSP)
output_stream.zig:185-204  publishPool() pushes a lease {pcm,frames,token,release_fn}
asio_control.zig:169-174 owner pops <=4 leases/pass, nsp_asio_submit(...), releaseLease(...)
```

The AES67 sink replaces **only the device side**: a second "backend" that
consumes the same `output_stream.Stream` leases. The Rust pool, the Zig common
DSP and the lease ABI are unchanged.

## 2. playback.json additions

`tools/zig-scheduler/playback.schema.json` currently describes `renderer`,
`asioDll`, `driver`, `outputProcessing` etc. Add one root enum plus one object:

```json
{
  "outputBackend": "asio",
  "network": {
    "destIp": "239.69.1.1",
    "port": 5004,
    "framesPerPacket": 48,
    "ttl": 32,
    "payloadType": 96,
    "ssrc": 1314082870,
    "srcInterfaceIp": "",
    "ptpGmid": "00-11-22-FF-FE-33-44-55",
    "ptpDomain": 0
  }
}
```

Rules to enforce in `desktop_playback.zig` (same "strict parse, unknown field
rejected, validate before side effects" model used today, see
`desktop_playback.zig:31-55,124-136`):

* `outputBackend` defaults to `"asio"`; `"aes67"` requires the `network` block.
* `network.framesPerPacket` must be one of 48/96/192/480; `destIp` must be a
  dotted IPv4 (multicast 224.0.0.0/4 allowed); `ttl` 1..255.
* With `"aes67"`: `asioDll`/`driver` are ignored (mutually exclusive output
  backends). `channels` is derived from `outputLayout` — it must be <= 16, i.e.
  all seven presets fit (9.1.6 = 16, `contracts/output-layouts.json:12`).
* SDP file: derive next to the session output directory
  (`<out>/aes67.sdp`) using `nsp_aes67_build_sdp` and the layout labels from
  `contracts/output-layouts.json` (see `tools/aes67-output/aes67_layouts.h`).

## 3. host.zig diff sketch

```zig
// host.zig, next to the existing ASIO branch (host.zig:304-326)
const use_aes67 = if (desktop) |s| std.mem.eql(u8, s.value.outputBackend, "aes67") else false;
if (use_aes67) {
    var config = try aes67_control.Backend.Config.initFromPlayback(settings.value);
    // channels/processing must match the layout preset and the Stream:
    var audio = try aes67_playback.Session.create(a, config); // opens nsp_aes67_* handle
    pipelines[source].bindOutput(audio.stream) catch |err| { audio.finish(); audio.destroy(); return err; };
    ...
}
```

Two small new Zig files mirror the ASIO pair, because the generic owner worker
already exists:

1. `aes67_control.zig` — backend methods consumed by
   `output_control.zig`'s generic `Worker(Backend)`:
   `open(config)` → load the DLL (`native_library.zig`) and resolve
   `nsp_aes67_create/start/submit/stop/destroy/get_stats`; `start()` →
   `nsp_aes67_start`; `pump()` → `get_stats` + pop leases (see below);
   `stop/close` → `nsp_aes67_stop/destroy`; `errorDetail()` → the ABI error text.
2. `aes67_playback.zig` — lifecycle glue analogous to `output_playback.zig:48-70`
   (create stream, configure DSP, submit `.open` then `.start` to the worker,
   `finish()` = stream.finish + wait until drained/timeout, then retire).

## 4. Lease return timing (the important part)

`output_stream.zig:31-36` defines the lease as `{pcm, frames, token, release_fn}`;
ownership rules are in `output_stream.zig:185-204` and
`docs/core/output-pool-processing-20261022.md:45-55`. For AES67:

```zig
// on the owner thread, once per pump()
for (0..4) |_| {
    const lease = stream.popLease() orelse break;
    defer stream.releaseLease(lease);              // same as asio_control.zig:170-174
    const rc = nsp_aes67_submit(handle, lease.pcm, lease.frames);
    if (rc != 0) { stream.fail(error.Aes67SubmitFailed); return; }
}
```

`nsp_aes67_submit` **copies** into the sender's bounded ring inside the call and
never keeps the pointer (`aes67_tx.c` `nsp_aes67_submit`), so the lease is
returned immediately after the call — exactly like ASIO, where
`nsp_asio_submit` copies into `InterleavedPcmQueue` before returning. Do not
hold a lease across the network: the ring is the only hand-off buffer.

Frame sizes: the pool emits 480-frame blocks; `framesPerPacket` only controls
how the sender *drains its ring*, so any of 48/96/192/480 works without changing
the lease granularity.

## 5. Lifecycle and prebuffer mapping

| NSP concept | ASIO today | AES67 sink |
|---|---|---|
| session lifetime | resident session, silence when no input | resident sender thread, silence packets when ring empty (`aes67_tx.c` `send_one_packet`) |
| prebuffer | `stats.minimum_prime_frames + 480` (`asio_control.zig:184-188`) | `network.prebufferFrames` (default 480) frames queued before state leaves `waiting`; reuse `Stream` state machine (`output_stream.zig:3`) |
| drain | `nsp_asio_finish_input` + `drainTimeoutMs` (`asio_control.zig:213`) | `stream.finish()` → wait until `stats.frames_sent >= stats.frames_submitted` or `drainTimeoutMs` → `nsp_aes67_stop` |
| device error | driver `faulted`, session fails | `send_errors`/network failures are counted; a fatal socket error fails the session |
| clock | QPC servo + HQ sinc SRC inside ASIO module | network/PTP clock is external; **no SRC**, sender is 48 kHz only |

## 6. Shutdown ordering (mirrors host.zig:327-361,430-516)

1. pipeline finish joins the Rust writer (no more `publishPool`).
2. wait for `stream.ended()` and `frames_sent >= frames_submitted`.
3. `nsp_aes67_stop` then `destroy`; write `aes67-output.json` report
   (packets/frames/silent/dropped/sendErrors/layout/channel labels) next to the
   existing `asio-output.json` (`host.zig:337`).
4. Only then free the bridge DLL reference (`output_stream.zig:63-68`).

## 7. Remaining work to actually integrate

* Export the C ABI from a DLL: `gcc -shared -Wl,--export-all-symbols` (or a
  `.def`) plus `-DNSP_AES67_BUILD_DLL` with `__declspec(dllexport)` annotations.
* Wire `Build-Core.ps1` to build `nsp_aes67.dll` and the docs/CI assertion for
  the new artifact.
* Add `playback.schema.json` entries, Web configuration page fields, and an
  `effectiveOutputBackend` report field.
* Repeat the pool-lease acceptance (`docs/core/output-pool-processing-20261022.md:57-63`)
  with a fake network sink, then a real AES67 receiver (e.g. ffmpeg `-i file.sdp`,
  Wireshark RTP analysis) on an isolated switch.
* PTP: a real deployment needs a PTP slave (e.g. Linux `ptp4l` on the same host
  or a NIC with hardware timestamping); this branch only advertises
  `ts-refclk` in SDP and does not discipline the transmit clock.
