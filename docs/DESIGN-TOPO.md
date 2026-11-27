# AES67 PTP topology design (NSP sender)

This document is the design reference that the AES67 module comments cite as
"DESIGN-TOPO.md". It describes the PTP topology modes of the Windows AES67
sender, why they exist, and what is deliberately different from AES67 /
IEEE 1588-2008. The user-facing settings table is in
[docs/aes67-output.md](aes67-output.md).

## 0. Scope and background

The sender runs on a Windows workstation that may sit in three very different
networks: a studio LAN with a PTP grandmaster (the validated deployment), an
isolated lab, or a machine that has to provide the clock itself. AES67 section
5 assumes a shared PTP time base, but a Windows host has no integrated PHC, so
"being the GM" there means announcing a software-grade clock. The four
`aes67.ptp.mode` values exist so the operator states the intent instead of the
sender guessing:

| mode | intent |
| --- | --- |
| slave | follow an external GM; this is the pre-existing strict behavior |
| auto | listen, follow an external GM when present, otherwise become GM (when capable) or fall back to the local clock |
| gm | become GM, but only after listening for an existing GM first |
| local | never speak PTP; use the local monotonic clock and `a=ts-refclk:local` |

## 1. Configuration mapping

`aes67.ptp.mode` (slave/auto/gm/local) → ABI `ptp_mode` (0/1/2/3).
`announceTimeoutMs` is the auto listening window and the floor for the
follower's foreign-master timeout. `priority1`/`priority2` are announced in
gm/auto modes. `gmCapable` allows auto mode to become GM instead of local.
`gmUtcOffset` selects the GM's currentUtcOffset: absent means ARB
(`ptpTimescale=0`, `timeTraceable=0`), a configured value means
`ptpTimescale=1` and a UTC-based claiming rule.

## 2. Role state machine

The follower and the role machine are independent: the follower always listens
so its Announce/Sync observations can drive the decisions, while the GM engine
only runs in the GM and Passive roles.

- **slave**: Listening until an eligible Announce arrives, then Slave forever.
  Startup lock timeout and loss keep the pre-existing session behavior
  (holdover, then LOCK_TIMEOUT in explicit slave mode).
- **auto**: Listening for `announceTimeoutMs`. An external Announce wins
  (Slave). On timeout: GM when capable and identity is readable, otherwise
  Local. After loss: holdover, then fall back to GM/local per the same rule,
  and never reclaim GM while a recent foreign Announce is still present.
- **gm**: Listening for `announceTimeoutMs` first. An already announcing GM
  wins immediately (Passive), so an explicit gm start cannot win the
  clockIdentity tie-break against a typical linuxptp GM and flap every receiver
  for about ten seconds. With no GM the sender becomes GM; a strictly better
  foreign dataset later steps it aside (Passive), and it reclaims after the
  foreign Announce times out. A Passive role that never ran the GM engine
  reclaims by Announce silence on its own.
- **local**: fixed Local role, no PTP sockets.

## 3. Clock identity and dataset

The clockIdentity is the IEEE 1588-2008 8.2.1 EUI-64 derived from the
configured interface MAC (EUI-64: MAC with `ff:fe` inserted and the U/L bit
flipped). It is never invented: gm mode refuses to start when the MAC cannot
be read (ABI -10). The announced dataset uses priority1/priority2 (default 128
per AES67 Annex A), clockClass 248, clockAccuracy 0xFE and
offsetScaledLogVariance 0xFFFF, matching the validated board GM profile; these
values are implementation choices, not AES67 numeric requirements. The GM time
base is a monotonic anchor at activation plus the optional UTC offset, because
this sender does not integrate a PHC.

The follower's foreign-master timeout is
`max(announceTimeoutMs, 5 × accepted announce interval)` with the interval
clamped to 125 ms–8 s. A malicious `logMessageInterval` cannot stretch the
timeout into days. A strictly better foreign dataset (IEEE 1588-2008 9.3.4
comparison order) replaces an already accepted master instead of being
rejected as `wrong_master`.

## 4. Deliberate deviations and extensions

- **ARB acceptance**: `ptpTimescale=0` Announce messages are accepted for
  frequency-only locking and report `timeTraceable=false`; AES67 section 5's
  shared TAI epoch is then not met (DEVIATION).
- **Local reference**: `a=ts-refclk:local` with `a=mediaclk:direct=0` is a
  mode-C EXTENSION used when no PTP clock exists; `time_traceable` stays false.
- **Dual-GM watchdog**: in gm/auto modes the engine yields after seeing a
  worse-but-live foreign GM for two Announce intervals rather than run two GMs
  (EXTENSION).
- **Software timestamping**: hardware timestamping is refused honestly
  (`TIMESTAMP_UNAVAILABLE`); the GM therefore announces a software-grade
  clock.

## 5. SDP clock reference lines

The announced SDP follows the current reference:

- locked slave / passive winner / active GM:
  `a=clock-domain:PTPv2 <domain>`, `a=ts-refclk:ptp=IEEE1588-2008:<GMID>:<domain>`,
  `a=mediaclk:direct=0` (AES67 section 8.2/8.3).
- local: `a=ts-refclk:local`, `a=mediaclk:direct=0`.
- no clock yet: no clock lines.

`ts-refclk:localmac` and `mediaclk:sender` are never emitted.

## 6. Status reporting

`nsp_aes67_ptp_status` reports mode, role, source, domain, time_traceable,
ptp_timescale, gm_identity, a stable reason tag and the transition counter. The
Windows scheduler writes those snapshots plus `nsp_aes67_counters` to
`<output-dir>/aes67-diagnostics.json` once a second, and one SDP per flow to
`<output-dir>/aes67-sdp.txt`. `PTP_LOST` means holdover started; RTP keeps
flowing until the holdover deadline.

## 7. Verification notes

The topology work was verified against a real LAN grandmaster
(`AE-6F-0D-FF-FE-8E-86-FC`, domain 0, two-step E2E): the DLL slave locked in
2.3–3.7 s and the SDP advertised the GM identity. Software timestamping means
the measured fit is limited by QPC and NIC interrupt behavior, not by a PHC.

### 7.5 Exact SDP lines by role

```
# slave locked / passive / GM active
a=clock-domain:PTPv2 0
a=ts-refclk:ptp=IEEE1588-2008:AE-6F-0D-FF-FE-8E-86-FC:0
a=mediaclk:direct=0

# local mode or auto fallback to the local clock
a=ts-refclk:local
a=mediaclk:direct=0
```
