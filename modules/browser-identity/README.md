# Ordinary Edge identity bridge

This module associates ordinary WASAPI PCM queues with real Edge tab and document identities. It carries metadata alongside the existing shared PCM ring; it does not capture tab audio, change DSP, inject code into the audio service, or use ETW.

## Runtime

Enable the top-level `browserIdentity.enabled` in playback JSON alongside `pcmCapture.enabled`. The observer uses `pcmCapture.inputEndpointId` and shares queue discovery with PCM capture. The legacy nested field is accepted on read. See the [playback settings](../../docs/core/playback-json-configuration-20261022.md#可选模块开关).

The MV3 extension reports tab/window IDs, target IDs, active document IDs and internal renderer IDs to the loopback agent on port 8784. The agent reads audio-controller metadata in its own `edge://media-internals` target. A version-pinned native observer verifies the controller-to-WASAPI-object references and compares the Windows Event object with the audiodg queue event. The capture and ingress DLLs attach the verified identity to ring version 2 descriptors.

The scheduler owns the agent, which owns the native observer. Browser navigation invalidates old identities. Queue/process recreation forces revalidation. Identity records expire after 500 ms without native validation. `sourceTabId: 0` attaches available identities without selecting a particular tab; a nonzero value restricts capture to that tab.

Windows can transiently refuse replacement of a metadata file. Such a sharing violation leaves the last complete file intact and the next publication retries; the retained record's timestamp still expires normally. Run `node --test modules/browser-identity/atomic-file.test.mjs` for the publication recovery test. The status file refreshes every second, including the current validated records, so a historical `bound` snapshot must not be treated as a current connection after its timestamp stops advancing.

For the ordinary default Edge profile, enable **Allow remote debugging for this browser instance** in `edge://inspect`. The agent discovers the current WebSocket address from `DevToolsActivePort`, then uses one connection with flattened target sessions. Edge may require approval for the connection. Do not add a command-line remote-debugging port to the ordinary default profile. Enabling the server and authorizing a connection are separate steps.

The unpacked **NSp Browser Identity** extension also requires developer mode on `edge://extensions` and its extension toggle enabled. An installed extension can still be disabled: Chromium disable reason `16777216` (`1 << 24`) denotes an unsupported developer extension with developer mode off. In that case the bridge reaches `awaiting-extension` after browser authorization, rather than reporting a connection failure. Enable it through Edge's UI; do not edit protected profile preferences.

`Deploy.ps1` writes the top-level browser settings, preserves renderer, DSP, gain and output settings, and saves the previous playback configuration and FSA configuration under `build/browser-identity-v1/deployment-backup`. The deployed scheduler starts the bridge with NSp. Live state is in the current session's `observed/browser-identity.json`; inspect that state rather than assuming an open port means identities are bound.

## Validation and limits

The 2026-11-04 isolated integration test used the real extension and new capture/ingress DLLs: two same-titled tabs, two source changes, two reloads with document replacement, AudioService recreation, and identity lease expiry all passed. Evidence: `build/browser-binding/ordinary-identity-20261104/events.json`, `capture.jsonl`, `capture-exit.txt` and `complete.json`. This is separate from the ordinary user-profile deployment check.

The native observer selects the layout by the exact SHA-256 of `msedge.dll`. Supported Edge builds are 156.0.4314.58 and 157.0.4341.42. Other images fail closed. Multiple tabs sharing a renderer remain unbound when ownership is ambiguous. The identity registry can describe several queues, but the current playback pipeline still selects one unambiguous ordinary PCM stream; this module does not implement multi-source mixing or sample-rate conversion. Lifecycle verification is leased metadata, not frame-exact attribution. Native-only benchmark CPU numbers do not represent the entire extension/agent/diagnostic-page cost.
