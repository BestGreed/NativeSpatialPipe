# Architecture and scope

The native hook snapshots the verified internal object-submission boundary. A C worker sends bounded packets through shared memory and an event. A Rust receiver borrows each slot until native renderer submission completes. The Rust renderer processes object state and PCM, then hands output to a separate buffered file writer.

The capture, receiver and renderer workers register with Windows MMCSS. The default Zig scheduler or explicitly selected legacy Python control host creates native resources, selects settings and monitors status outside the audio path. Python still runs the telemetry service. Audio processing does not depend on the control polling interval. The file writer uses ordinary scheduling. See [scheduler and lifecycle](scheduler.md).

The current descriptor/payload contract includes object identifiers, static types or dynamic positions, gain, frame counts, lifecycle state, sequence numbers and timing. Object identifiers represent the local observed lifecycle, not a game's internal sound-source identity. Unsupported frame counts, malformed descriptors and discontinuities fail closed according to the existing native validation.

## Technical limits

- The native hook depends on verified Windows binary hashes, offsets and layouts. Those checks are part of the implementation, not a stable public API.
- No custom Spatial Sound Provider registration is provided.
- The standard research entry is single-stream, 480 frames/48 kHz. The default managed path prewarms four internal renderer slots; those slots are not a Windows stream enumeration. Slot reclamation across arbitrary restarts remains incomplete.
- Core performance ends at rendering completion. File flush, device buffers and actual playback are separate boundaries.
- Full render recording and telemetry snapshots remain enabled for development. Storage grows continuously; D15 storage governance is deferred until D05 audio-interface playback works. See the root README for the current budget and planned boundary.
- Extended gain/distance/EOS semantics, continuous long-duration runs, abnormal recovery and cross-build support remain incomplete.
- System-level injection does not establish anti-cheat compatibility. A previous game replay test was followed by a reported disconnection; causation and general compatibility have not been established. Start with owned fixtures rather than protected multiplayer sessions.
- Shared-memory integration tests require an execution environment allowed to reopen the named mapping/event. AppContainer access denial was reproduced and diagnosed; ordinary non-AppContainer runs pass. This does not establish capture, playback or long-duration acceptance. See [startup validation](receiver-startup.md).

The public source repository is [BestGreed/NativeSpatialPipe](https://github.com/BestGreed/NativeSpatialPipe), with `main` as its default branch. It excludes the private experiment archive, raw captures, game assets, third-party SDKs and locally installed toolchains, and has independent root history. The research repository remains private. Future public updates should be reviewed source exports; do not merge private research history into this repository.
