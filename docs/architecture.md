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


Session startup first initializes a temporary shared audio client for the target
endpoint, then checks its audio host. Capture layout refresh and native Hook
validation precede activation. The existing HT format-stability wait and active
format readback remain part of startup.

Recovery verifies the old session under the current controller's owner lease.
A replacement manager is not treated as an old process that must exit. An
orphan may be retired without claiming that the original endpoint snapshot was
restored. Locked tray interaction requests stop/verification and allows a later
start after cleanup. See the [user guide](user-guide.md) and
[developer contracts](development.md).
