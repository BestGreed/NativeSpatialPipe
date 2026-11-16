> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# ASIO output persists with NSp session

User confirmation: ASIO is the device output backend. After correct configuration, it runs with the NSp session and does not start or stop according to the audio source playback life cycle. Remove the "no first packet timeout exit, heavy buffering timeout exit, and accumulated underload times exceeding the limit" strategy inherited from the limited playback scenario.

## Behavior and implementation boundaries

- The driver only starts when the session starts, and always calls the output callback according to the device clock; there is no PCM, application pause/close, or continuous write silence when the input is intermittent.
- Consumption is resumed after the input reaches the original bounded pre-buffering threshold; the source PCM, packet sequence number or EOF is not forged while waiting, the unbounded cache is not increased, and the driver is not restarted.
- The C++ C ABI output backend already supports empty wait muting, persistent callbacks during hold, source clock freezing, and reset of drift estimates on recovery. It will be used in this round and no new PCM replication, scheduling optimization or new ASIO implementation will be added.
- Changes in the Zig life cycle policy: `.waiting` and `.rebuffering` can wait indefinitely; the number of underloads/recoveries continues to be used as a diagnostic indicator, and the session cannot be terminated based on this.
- Only perform finish, DSP tail and bounded drain before shutting down the device if the session is explicitly stopped/upstream explicitly ended. `drainTimeoutMs` is still in effect.
- Device failures, driver reset requests, discontinuous input, queue overflows, and capture/transmission failures still follow the original failures and safe endings; there is no addition of automatic device replacement or failure continuation.

## Configuration compatible

`startupTimeoutMs`, `rebufferTimeoutMs`, `maxRebuffers` and the corresponding Zig/CLI fields remain parsing compatible. The default and valid values are 0. Old non-zero values no longer trigger normal flow-free exit. They are not switches to configure "how long to wait before settling". `effectiveOutputLifecycle` for `validate-playback` and `playbackPolicy` for host explicitly report `lifetime=session`, `noInput=silence`.

`prebufferFrames`, device clock matching, channel routing, public DSP, pool block ownership unchanged. ASIO's driver open/close errors and explicit stop timeouts are not ignored due to persistence. The limited file end semantics used in the original test are only used to actively end the test and do not represent the absence of normal sources.

## Acceptance scope of this round

Uses existing ROAR bridge DLL and own virtual ASIO driver, does not rebuild/optimize renderer, does not operate real DVS, system default output or EDID. Verify that the initial idle wait exceeds the old timeout, repeated power outages exceed the old upper limit, the silent callback persists, subsequent PCM can recover, and device failure still fails to be reported. Non-clock synchronized paths perform an exact frame-by-frame comparison on the recovered PCM, and the pool blocks must be returned in full. Long-term persistence of the real device and cross-application switching still require subsequent return of the real device.

This round does not automatically add `PlaybackConfig` to the FSA or select the user's ASIO device; configuring whether to enable the playback backend and the session life of the output backend are two independent issues.

Results of this round: 13 software playback/fault scenarios and 21 unit tests passed, including recovery after muting without the first packet, recovery after repeated source interruption, normal stop when there is no input, and explicit emptying stall still reporting `OutputTimeout`. For evidence, see the software verification results (the development reference is not provided with the open source code). After the administrator query confirmed that the old session was stopped, the new `build/zig-scheduler/nsp-scheduler.exe` was deployed and the FSA was restarted normally; the real ASIO device was not opened for long-term actual testing.
