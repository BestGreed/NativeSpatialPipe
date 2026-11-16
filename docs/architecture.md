# Product architecture

FSA owns the user session and spatial-mode arbitration. The Zig scheduler
coordinates startup, capture, rendering and shutdown. The web management
panel edits playback JSON through the scheduler.

Native spatial capture forwards object PCM and metadata through the Rust
receiver. Optional MFT decoding provides an additional spatial source.
Optional PCM capture and upmix feed the product pipeline. Croksor coordinates
input handoff, rendering, DSP and output. ROAR 1.1 with the NSP patch provides
OLR. LIRA is a candidate renderer. Output paths include Inferno AoIP,
the integrated ASIO backend and stereo WASAPI for LSAC.

The Python telemetry service and Zig web host run separately from audio
processing. See the [runtime validation notes](runtime-details.md) and
[performance measurements](../README.md#performance-reference).
