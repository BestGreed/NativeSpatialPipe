> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Common Output Pool DSP and Lease Handover

2026-10-24 Update: The "writer writes to disk first and then processes/deliveries" described in this article has been replaced by [Independent optional recording] (recording-isolation-20261024.md). Currently real-time delivery does not perform file IO; PCM side recording and telemetry archiving are turned off by default, and the stream selection is changed to read memory progress. The reconstruction boundary at that time is retained below as a historical illustration.

Subsequent running configuration: A copy of the FSA set has been created according to user requirements and started with administrator privileges. Only the management entrance is ready, and Hook or playback is not automatically enabled. See the FSA output link record (development reference is not provided with the open source code). The isolation acceptance conclusion below does not extend to the actual DVS playback verification.

2026-10-22, DEV16. Users are explicitly authorized to optimize the common output stage path this round; the scope is only DSP forwarding, configurable skipping, and removal of two PCM copies between the Rust pool and the ASIO commit boundary. Do not restore previously terminated ASIO driver schedules, clocks, SRCs, callback rings, or latency optimizations, and do not restore deprecated temporary WPF entry points.

## actual path

Old: `renderer → Rust interleaved pool → Zig PCM queue (replication) → scratch (replication, DSP) → ASIO submit`

New: `renderer → Rust interleaved pool block → in-pool DSP/bypass → bounded pool block descriptor → ASIO owner commits pool address directly → returns pool block`

ROAR/OLR and LIRA share this path. Zig no longer allocates 32 blocks of PCM queue or scratch, only retains 32 pointer/frame number/return function descriptors. A total of 760,320 bytes of the original two staging storage of standard 480 frame × 12 channel Float32 have been removed; this is not a measured difference in the entire machine memory, nor is it a full-link zero-copy statement.

The ASIO owner commits the borrowed address synchronously via the existing C ABI, and the ASIO module reads Rust pool memory directly. **ASIO internally still copies to its own PCM queue, followed by device clock/SRC/ring/format processing. ** These algorithms are not changed in this round, and hardware callbacks are not allowed to operate the Rust pool or wait for the renderer. It is not that a new driver thread actively calls Rust's pull interface; it reaches the device submission boundary and directly borrows pool blocks.

## DSP location and configuration

Following the Zig `output_processing.zig` algorithm, executed via in-process C ABI callbacks on a writable pool block exclusive to the Rust output writer. The algorithm was not ported to a Rust implementation, and no new processing threads were added. The computation occurs before the pool block is released; the output device thread only submits and does not process the DSP once.

Set in `outputProcessing` for full JSON playback:

```json
{
  "outputProcessing": {
    "bypass": true
  }
}
```

- `bypass: true`: Skip all public DSP, including root field `master`, channel-by-channel trim, calibration delay and limiter/look ahead; the saved parameters are not lost and will take effect again after turning off bypass next time.
- `bypass: false` (default): processed according to configuration; if master/trim is all 1, delay is all 0 and limiter is turned off, sample processing will be automatically skipped.
- When skipping, the PCM is not scanned/rewritten, delay, lookahead, and peak history are not allocated, and DSP tail frames are not generated. Block layout, ordering, lifecycle checks, and non-limited sample checks for ASIO submissions are preserved.
- No hot-switching: Still modifying JSON and starting new sessions after stopping. Invalid configurations will be rejected even if bypass is enabled.
- Channel semantics, 48 kHz source frame units, and DSP order before physical routing remain unchanged; no new acoustic algorithms have been added.

`effectiveOutputProcessing` for `validate-playback` will display `bypass`, `effectiveBypass`, `placement` and valid lookahead; the actual output report provides the same bypass/position status, as well as pool block receive/return count and DSP tail frame count for `outputHandoff`.

**History (10/22, superseded):** At that time, the writer's behavior of writing files first and then processing/delivering them was only retained as migration evidence. **Current (10/24):** The renderer PCM optional side-recording still sits in front of the common DSP, but writes to a separate recording pool and thread; the playback delivery issues the lease descriptor after the DSP is executed in the output pool and does not wait for disk. Telemetry remains independent bypass. See [Decoupling of side-recording] (recording-isolation-20261024.md), the disk IO cannot be reconnected to the playback thread due to the old name writer.

## Ownership and closing actions

Rust creates bounded pools and stable block tokens on initialization and does not allocate lease descriptors on a per-packet basis. Added `nsp_pipeline_set_pool_output` entry, the configuration is completed before the first input is submitted, and is mutually exclusive with the old synchronization sink; the old sink export continues to be reserved for existing callers, and the new host does not silently fall back to the old replication path.

1. Rust exclusive block, publishes read-only PCM address and token after processing in place.
2. If the release is successful, the device side holds the lease; if the release fails, the ownership still belongs to Rust, and the caller cannot return the token.
3. owner takes out the block from the descriptor, calls the existing synchronization `nsp_asio_submit`, and returns it once; even if submit reports an error, the current block is returned.
4. The failure immediately returns all blocks still in the descriptor queue. The blocks that have been taken out and are being submitted are returned by the owner to prevent repeated release or early reuse.
5. There may still be blocks to be played after the pipeline is destroyed; Stream holds the bridge DLL loading reference alone, and the life cycle of the return function and block token does not depend on the Pipeline instance. This reference is unloaded after the worker exits and all blocks are returned.

LIRA's original 512-frame renderer tail is processed first, and then the common DSP drains its own calibration/lookahead tail in a pool block. `receivedFrames` keeps the renderer frame number, DSP extra last frames are counted separately, `submittedFrames` includes both. At the end, existing free blocks can be reused to generate a tail frame without creating a scratch.

Even if the real-time block queue is full, it will still clearly fail and old blocks will not be discarded for continuation. Only non-real-time EOF tail frames are allowed to wait for descriptor vacancies; the existing `drainTimeoutMs` constrains the release wait for the entire tail frame, exits on failure/timeout, and returns resources. No waiting aggregation targets for normal blocks are added.

## Validation and candidate builds

Reappearance entrance: `python tools/desktop/Test-OutputPool.py`. Build candidate set at `build/output-pool-direct-20261022/`, using own capture fixture and native emulated ASIO DLL, without opening real endpoints, WPF interface or DVS.

Passed this round: 6 pool block/timeout checks, 8 DSP checks, 8 configuration checks; 8 callback PCM comparisons of actual host software paths (two renderers, DSP, default neutral and explicit bypass, up to 48,000 frame calibration + 4,800 frame lookahead tail); 12 groups of playback/rebuffering/timeout/device failure regression, all checking that pool block reception and return counts are consistent; 9 Class error configuration pre-launch reject; native FSA JSON forwarding passes. The bypass forwarding and session ownership checks of the non-interface C# ClientProbe also pass, and do not constitute a temporary desktop for restoration and maintenance.

Evidence: Complete isolation acceptance (development reference is not provided with the open source code), host PCM comparison (development reference is not provided with the open source code), client forwarding regression (development reference is not provided with the open source code). The original ASIO emulation DLL and LIRA DLL hashes remain unchanged.

The new host and the bridge with the new pool ABI should be used as a set. The candidate bridge is at `build/output-pool-direct-20261022/bridge/release/nsp_roar_bridge.dll`, and `bridgeDll` playing JSON should point to that suite of builds; the old bridge fails explicitly with missing exports. Currently running FSAs, hosts or modules are not replaced; this round of results only demonstrates the isolation of software paths, real device playback/sustained stress and performance measurements still require subsequent bounded verification.
