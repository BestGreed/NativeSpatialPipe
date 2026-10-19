# FSA / NSp control recovery update

The native Zig scheduler now bounds control-plane socket I/O, including idle
browser preconnects, partial HTTP requests and unresponsive control servers.
These changes do not add polling or blocking work to capture/render transport.

FSA has separate Windows Sonic endpoint and NSp feature selections. NSp stop,
emergency stop and exit share verified state handling, with deduplicated control
children and normal single-instance ownership release after exit completes.

An unreachable old session can be retired only after its manager, orchestrator,
capture hosts, telemetry and old Hook target have exited. A current audiodg must
pass a live baseline check. Explicit absence of an audio host is also a valid
idle state and is checked again before retirement; enumeration errors and
multiple hosts are not treated as absence. Recovery preserves the current
endpoint and records separate evidence rather than inventing a missing endpoint
restore report.

Validation includes 13 isolated scheduler regression tests, native orphan tests
covering live processes and audio-host transitions, 33 telemetry tests, 60 FSA
mode/lock/observation combinations and tray animation checks. Physical-device
acceptance evidence remains in the private development repository. This is not
a claim of a new game playback test for every recovery branch.

The source provenance manifest points to private source revision
`17ab177fc5194c198fd12cd9eb2b4bd8b3d55fa2`. Public portable build adaptations and
existing licensing remain in place. This update does not implement source-stream
slot retirement or change the C/Rust audio core.
