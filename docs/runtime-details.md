# Runtime architecture

FSA manages the session through the Zig scheduler. The scheduler coordinates capture, the Rust receiver, Croksor and the web frontend. Croksor handles input handoff, rendering and output delivery. The build includes ROAR 1.1 with the project patch, LIRA, Inferno AoIP and ASIO.

Product build switches add Media Foundation decoding, PCM capture, browser identity, WASAPI output and the CPU upmix components. Building these components makes their artifacts available. Playback configuration determines which path runs for a session.

The full build passed locally with prepared dependencies and FSA build-time checks. Live capture and device playback require separate validation. Native spatial capture requires a usable runtime layout as described in [capture compatibility](capture-compatibility.md).


## Startup failure and recovery

A Hook-before-activation failure now releases initialized pipelines, stops and
joins receiver/bridge readers, and only then frees their referenced resources.
The originating implementation was checked with repeated injected file-write
failures and normal sessions. Candidate build and regression results must be
recorded against this public tree as well.

The online controller can verify and retire an orphan during status handling.
The normal locked tray toggle sends stop; after successful cleanup, another
click starts NSP. Recovery does not require the replacement manager to exit.
This preserves the existing user decision path rather than adding a separate
automatic restart mechanism. FSA polls immediately, then schedules the next
check about two seconds after the current control operation completes. The
startup format wait remains a separate call-chain step.
