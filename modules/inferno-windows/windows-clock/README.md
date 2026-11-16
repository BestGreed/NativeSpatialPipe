# Minimal Windows clock prototype

This dependency-free crate now provides the in-process Windows clock backend
for `inferno_aoip`, alongside its diagnostic CLI. It belongs to the root workspace;
build it with `cargo build -p inferno-windows-clock`. Linux retains usrvclock.

## Implemented

- QPC/QPF local monotonic time, using integer nanosecond conversion.
- PTPv1 two-step Sync/Follow_Up/Delay_Resp decoding and Delay_Req encoding.
- Bounded pairing by master identity, requesting identity, sequence and age.
- A 32-observation affine time fit with delay rejection, residual/rate gates,
  minimum acquisition span, discontinuity reset and expiry.
- In-process atomic clock snapshots: one writer, bounded-retry readers; no
  allocation or mutex in a clock read. A per-consumer reader rejects backwards
  time. Exact affine inversion supports future TX deadline calculations.
- Opt-in IPv4 unicast-delay follower with an explicitly selected interface,
master IP and local MAC identity. It never sends Sync or joins master election.
- Windows SO_EXCLUSIVEADDRUSE before binding PTP ports. An existing DVS/PTP
  process is not stopped or displaced.

## Validation modes

From this folder, `cargo test --locked --offline` runs deterministic clock and
packet tests plus a UDP/QPC mock-master test on ephemeral loopback ports.

`cargo run --release -- --probe` checks local counter monotonicity and reports
read timings. This is not a network synchronization benchmark.

`cargo run --release -- --simulate` exercises synthetic offset and +80 ppm
frequency error. Synthetic error numbers do not indicate real PTP accuracy.

NSP's `tools/aoip/Build-ClockPrototype.ps1` selects its portable Windows Rust and
LLVM tools and runs these checks. Some execution sandboxes prohibit loopback
networking; that produces socket timeouts even when unit tests pass.

## Explicit experimental network mode

`inferno-windows-clock --follow-unicast INTERFACE_IPV4 MASTER_IPV4 LOCAL_MAC_12HEX SECONDS`

The duration must be 1..120 seconds. This binds UDP 319/320 exclusively, joins
224.0.1.129 on the chosen interface, accepts messages only from the specified
master IP on the expected destination socket, pins the first Sync source identity, and sends
unicast Delay_Req to that master. Verify that the master supports this exchange.
The caller must ensure that the supplied MAC identifies the selected interface.
Port conflicts terminate the program; do not stop DVS just to run this command
without arranging the intended test state.

`--discover INTERFACE_IPV4 SECONDS` receives PTP packets without sending PTP
messages. `--follow-multicast` takes the same arguments as `--follow-unicast`
but sends Delay_Req to the default subdomain multicast group, TTL 1, on an
explicitly bound interface. The sender uses a separate ephemeral source port.
PTP source UDP ports need not be 319/320: actual hardware used 1031/1032.

After the operator allowed the executable through the local firewall, the Rust
follower completed real hardware exchanges and repeatedly acquired lock. The
initial sleep-based polling run also lost lock. The integrated host subsequently
acquired lock with socket-readiness waiting; sustained audio delivery remains
unverified until an external return route is connected.
The parent-dataset request encoder is experimental and its initial clock
quality fields are not calibrated measurements.

This mode uses user-space
software timestamps, Windows socket-readiness waiting, only `_DFLT` subdomain/zero epoch,
  two-step PTPv1 and unicast/multicast delay requests. An empty master field follows an active
  two-step source on the selected Windows interface and rediscovers after loss of lock.
  It does not implement PTPv2, BMCA,
one-step Sync, redundant networks, path-asymmetry calibration, hardware
timestamping or automatic master replacement. Packet layout references were
checked against Wireshark's public `packet-ptp.c` field definitions; this crate
does not copy its implementation or DVS code.

The default acquisition gates (8 accepted samples spanning 2 seconds, 100 us
fit RMS, +/-500 ppm, 2-second expiry) are experimental settings, not promised
synchronization accuracy. Stable path asymmetry can bias an apparently clean
fit; passing gates does not establish absolute accuracy.

## Live directional estimator

The live follower now uses `EnvelopeServo`. For nonnegative transport delays,
each four-timestamp exchange constrains `remote(t2) >= t1` and
`remote(t3) <= t4`. A bounded history of 128 exchanges intersects these forward
and reverse constraints, constrains the proposed frequency to feasible slopes,
and keeps the previous phase inside the new interval where possible. A fixed
minimum-roundtrip rejection window is no longer used by the live follower.
The original regression servo remains available for synthetic diagnostics.

Acquisition requires eight samples spanning two seconds and either a directional
interval half-width of at most 200 us or midpoint-fit RMS of at most 100 us.
The RMS alternative allows a stable fixed-delay path to acquire; it does not
remove bias from fixed path asymmetry. Frequency is bounded to +/-500 ppm and
the original two-second expiry remains enforced. Unqualified samples do not
renew a clock mapping. Interval half-width and midpoint-fit RMS are separate
diagnostic quantities; neither is a calibrated absolute accuracy guarantee.
The affine-rate and nonnegative-delay assumptions can fail under clock changes
or timestamp defects; the implementation rejects inconsistent bounds and
requires reacquisition on a detected clock discontinuity.

`INFERNO_CLOCK_TRACE` optionally names a CSV file for local four-timestamp
diagnostics. It records no audio. Trace data from a physical network should
remain local unless explicitly selected for sharing.

## Audio integration

The Windows `MediaClock` and TX/RX consumers now use this follower in process,
including expiry propagation and rate inversion. See ../WINDOWS-TESTING.md for
the two-channel synthetic source / return host. NSP pool integration, source-rate
matching and sustained hardware audio validation are subsequent work.
