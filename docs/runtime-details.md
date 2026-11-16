# Runtime architecture

FSA manages the session through the Zig scheduler. The scheduler coordinates capture, the Rust receiver, Croksor and the web frontend. Croksor handles input handoff, rendering and output delivery. The build includes ROAR 1.1 with the project patch, LIRA, Inferno AoIP and ASIO.

Product build switches add Media Foundation decoding, PCM capture, browser identity, WASAPI output and the CPU upmix components. Building these components makes their artifacts available. Playback configuration determines which path runs for a session.

The full build passed locally with prepared dependencies and FSA build-time checks. Live capture and device playback require separate validation. Native spatial capture requires the exact audio-engine profile documented in the [README](../README.md).
