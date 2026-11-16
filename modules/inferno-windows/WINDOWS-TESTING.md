# Native Windows Dante loop test

This experimental port uses an in-process QPC/PTPv1 follower and the existing
Inferno discovery, control, TX and RX implementation. No DVS or ASIO API is used.
It does not elect a master or change the system clock.

Build only the Windows host (not the ALSA workspace package):

```
cargo build --release -p inferno_aoip --example windows_loopback
```

Run with an explicitly selected interface, master and interface MAC:

```
windows_loopback.exe INTERFACE_IPV4 MASTER_IPV4 LOCAL_MAC_12HEX SECONDS
```

Duration is 1..1800 seconds after audio startup. Clock acquisition has a separate
120-second limit. Ctrl+C stops the audio test. The PTP worker releases its sockets
when the host shuts down. DVS must not own PTP ports 319/320 for this test.

For a bounded packet-size interoperability experiment only, set the process
environment variable `INFERNO_TEST_TX_FPP=32`. It overrides the receiver-requested
unicast TX frames per packet and logs requested versus actual values. This is
not negotiated acceptance; the peer may reject or misinterpret the stream.
Unset it for normal receiver-selected behavior. No persistent setting is used.

The device is **Inferno Windows Loop Test**, 48 kHz, 2 TX / 2 RX.
The Windows test entry point sets TX_LATENCY_NS to4,000,000 (4 ms) and
RX_LATENCY_NS to250,000 (250 us). The receiver uses the larger of its local
floor and the peer's advertised minimum. Per-flow logs show the applied samples.
TX mDNS capability advertisements retain the TX value independently of RX.
TX channels contain periodic 375 Hz and 562.5 Hz sine waves at -30 dBFS. The operator routes
TX 1/2 through another computer and returns those channels to RX 1/2 in Dante
Controller. No automatic subscriptions are created by the test entry point.
Inferno's existing saved-subscription behavior still applies on later runs.

Allow this executable's LAN UDP traffic on the test adapter: PTP 319/320,
mDNS 5353, test control ports **18000..18003**, and negotiated media ports.
Alternate control ports use Inferno's existing ALT_PORT mechanism and avoid
Dante conmon's wildcard listeners at 8700/8800; PROCESS_ID=1 separates the test
device identity. Existing services need not be stopped.

The terminal reports clock validity, RX callback frames and nonzero samples.
Zero received frames means no return audio has been observed. Nonzero samples
alone do not prove correct channel mapping, frequencies, continuity or latency.
A steady periodic tone cannot uniquely determine end-to-end latency.

The Windows backend revokes expired mappings and forwards clock loss to TX/RX.
The live follower estimates separate forward/reverse delay bounds rather than
assuming each exchange has symmetric queues. Its 200 us interval half-width
budget is a different metric from midpoint-fit RMS and is not a promise of
absolute synchronization accuracy. See windows-clock/README.md.
TX flow keepalives use `Instant` and are serviced even when audio is paused for
an unavailable clock. The host logs submitted-packet counts, send errors,
received keepalives and maximum gaps between successful send calls. These are
host-side diagnostics, not proof of on-wire delivery or receiver acceptance.
TX resumes using a fresh flow timestamp after reacquisition. Software timestamp
accuracy, scheduling jitter, channel integrity and sustained real audio delivery
still require hardware-loop testing. Do not interpret servo residual as absolute
synchronization accuracy. The Linux usrvclock integration test remains Unix-only.

Windows dependency fix: vendored netdev 0.32.0 retains its MIT license, with a
single SendARP output-length guard documented in vendor/netdev/PATCH.md.
