# Architecture and scope

The native hook snapshots the verified internal object-submission boundary. A C worker sends bounded packets through shared memory and an event. A Rust receiver borrows each slot until native renderer submission completes. The Rust renderer processes object state and PCM, then hands output to a separate buffered file writer.

The capture, receiver and renderer workers register with Windows MMCSS. Python selects the settings, creates native resources and polls status every 100 ms. Audio processing does not depend on that polling interval. The file writer uses ordinary scheduling.

The current descriptor/payload contract includes object identifiers, static types or dynamic positions, gain, frame counts, lifecycle state, sequence numbers and timing. Object identifiers represent the local observed lifecycle, not a game's internal sound-source identity. Unsupported frame counts, malformed descriptors and discontinuities fail closed according to the existing native validation.

## Technical limits

- The native hook depends on verified Windows binary hashes, offsets and layouts. Those checks are part of the implementation, not a stable public API.
- No custom Spatial Sound Provider registration is provided.
- The default pipeline is single-stream, 480 frames/48 kHz, with a prewarmed native renderer.
- Core performance ends at rendering completion. File flush, device buffers and actual playback are separate boundaries.
- Extended gain/distance/EOS semantics, continuous long-duration runs, abnormal recovery and cross-build support remain incomplete.
- System-level injection does not establish anti-cheat compatibility. A previous game replay test was followed by a reported disconnection; causation and general compatibility have not been established. Start with owned fixtures rather than protected multiplayer sessions.
- Shared-memory integration tests require an execution environment allowed to reopen the named mapping/event. AppContainer access denial was reproduced and diagnosed; ordinary non-AppContainer runs pass. This does not establish capture, playback or long-duration acceptance. See [startup validation](receiver-startup.md).

The public source repository is [BestGreed/NativeSpatialPipe](https://github.com/BestGreed/NativeSpatialPipe), with `main` as its default branch. It excludes the private experiment archive, raw captures, game assets, third-party SDKs and locally installed toolchains, and has independent root history. The research repository remains private. Future public updates should be reviewed source exports; do not merge private research history into this repository.
