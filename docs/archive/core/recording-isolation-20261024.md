> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Decoupling PCM recording and real-time playback

Follow-up update on the same day: [User Directory Default and Compatibility Migration] (playback-user-location-20261024.md) for playing JSON has been implemented; the "Directory Migration to Be Done" in this article only belongs to the current stage, and the capacity and retention strategy still need to be done.

2026-10-24. User authorization removes the write coupling in the normal playback path and provides independent switches for PCM side recording and telemetry snapshot archiving. Only handles output delivery and diagnostic archiving, does not change ASIO driver scheduling, clocking, SRC or callback algorithms.

## actual path

Previously: `renderer → Rust output pool → same writer writes file → in-pool DSP → pool block delivery → ASIO owner`. The writer, which is independent of the renderer, is still on the path required for playback, so disk blocking or write errors may delay or even interrupt delivery.

Now: `renderer → Rust output pool → output delivery thread in-place DSP → pool block delivery → ASIO owner`. This thread does not hold the recording file, nor does it perform file writing, flushing, or sync.

Only when side recording is enabled, the delivery thread obtains empty blocks from the pre-allocated recording pool, copies the PCM before DSP, and then submits it to the dedicated recording thread in a non-blocking manner; the original playback pool blocks immediately continue DSP and delivery. The recording pool is fixed at 32 blocks. When it is not turned on, no PCM recording pool, recording thread or audio file will be created. When the pool is full, the recording thread fails, or the file cannot be created, only the recording data is discarded and counted, and no playback failure is returned; disk operations do not hold playback leases.

The recording still retains pre-DSP semantics and does not contain the common DSP extra tail. When recording block loss occurs, the file is not a complete and continuous control recording. `pcmRecording.droppedBlocks` and `error` in the report should be checked and cannot be used as a gap-free baseline. There is still a bounded memory copy and system resource overhead when recording is turned on. It is not claimed that the performance of the two states of switching is absolutely the same.

At the end of the session, output delivery and DSP tailing are completed, followed by a non-real-time finishing phase of draining the recording queue, flush/sync, and collecting reports. A permanent non-responsive disk may still prolong the end of the diagnostic recording. "Playing without waiting for recording" cannot be interpreted as "any stop under any disk failure must return immediately."

## Configuration and front-end

Play JSON New:

```json
"recording": {
  "pcm": false,
  "telemetrySnapshots": false
}
```

Both are off by default, and are also off when old JSON omits the object or its fields. Configuration versions remain compatible; Web "Configuration → Diagnostic Recording" provides two independent options. After saving, the session will be read next time, no hot switching will be performed, and playback will not be automatically started. The two items are independent of each other and are not controlled by `outputProcessing.bypass`.

Direct host testing can explicitly pass `--record-pcm` or `--archive-telemetry`; when provided together with `--playback-config`, the ambiguity is rejected, and JSON is the complete configuration entry.

When telemetry snapshot archiving is turned off, the original Zig Recorder query/JSONL recording thread is not started. Real-time telemetry services, HTTP/WebSocket, and original visualizations continue to work; memory history is still queryable. Small session configuration, state, backchecks, and final summaries continue to be retained and are not part of the audio/snapshot large file archive.

## Stream selection is ABI compatible

Raw telemetry stream selection relies on recording file byte growth. Now the monotonic memory progress of the renderer is read by adding `nsp_pipeline_progress`, maintaining the original "latest active output stream", 100 ms polling and timeout rules, and not merging candidate streams. The routing report is clearly marked `progressSource: "renderer-memory-counter"` and no longer relies on the existence of the recording file.

The official host uses the new `nsp_pipeline_create_live`, which supports all connected layouts and two renderers. Audio files are not written by default. The existing `create` / `create_with_position_mapping` / `create_spatial` / `create_layout` ABI is reserved for offline testing tools, and explicit file output, reliable recording and old audio comparison semantics continue. If the old library lacks a new real-time entry, it will fail explicitly and the implicit write path will not be returned.

The final native report adds `pcmRecording`, single-column recording switch, pool capacity, commit/write/discard blocks, recording frame number, errors and flush/sync time. The old `written`, `writerQueueMs`, `bufferedWriteMs` and other fields are reserved for compatibility; on the real-time path, they describe output delivery and side-recording submission, and no longer represent disk completion, and the report scope has been marked.

## verify

- Block the recording thread until all 1,000 submissions are completed: the fixed pool receives 32 blocks, and the remaining 968 blocks are discarded, and the submitting end does not wait for the disk; it is also tested that the failure to write the recording will not turn into a playback error.
- Two renderers, 7.1.4/9.1.6, closed/on/file conflicts, a total of 12 sets of actual live ABI checks: Playback PCM does not change with the recording switch, pool rentals are returned; when turned on, recording is equal to DSP pre-PCM; when turned off, no file is created, real-time tap and progress are still available.
- Own 140-slot capture fixture covers four switch combinations, 250 packets per set; real-time object/output telemetry, memory progress routing, full exit pass-through. There is no `.f32` or snapshot JSONL when the two items are turned off, and the corresponding recording and audio comparison are retained when they are turned on.
- Seven layouts × two backends × bed/object 28 groups of renderings and old 7.1.4 byte compatibility regression passed; 11 JSON configuration items, 15 Web API items and front-end form switch verification passed.

Actual DTS:X HT/FSA/ROAR/ASIO startup, operation, and graceful shutdown passed after user restored DVS; 2,787 device callbacks during idle operation, no underruns, and no PCM/snapshot files. There is no game streaming this time, not listening or actual signal verification; the test will remain stopped/ready.

This proves that the removal of file IO on the necessary path for playback is not enough to conclude that all previous sporadic interruptions came from the disk, and does not constitute a new end-to-end latency stress test. See also the verification directory for actual machine status.

For evidence, see `experiments/recording-isolation-20261024` (development reference is not provided with the open source code). Large files generated previously are not deleted in this round.
