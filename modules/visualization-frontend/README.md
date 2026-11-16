# NSp sound navigation / sound spectrum front end

2026-10-24 playback location: `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` is now the default. The old installation-adjacent default is copied without replacing existing user data; explicit custom paths remain supported. See [migration](../../docs/archive/core/playback-user-location-20261024.md).

## Current entry and configuration

The playback configuration area of 2026-11-08 shows the core layout and the two-channel device configuration of LSAC respectively. The signal chain panel shows rendering and device layout, MFT status, PCM capture, browser identity, Joint backend and fallback, and WASAPI writes. For fields and constraints of optional modules, see [Playback Configuration](../../docs/archive/core/playback-json-configuration-20261022.md#radius-recovery-and-new-switches).

The independent Zig Web API Host that comes with FSA uses `http://127.0.0.1:8767/` by default, and the tray is opened by the default browser with three consecutive clicks. The "Configuration" on the homepage supports playback/backend JSON forms, renderer, seven layouts, DSP/bypass, device wiring displayed by channel name, and two diagnostic recording switches. PCM side-recording and telemetry snapshots are turned off by default. Turning off archiving does not affect real-time telemetry, memory history, and visualization. Device channels start at 1 in the interface and 0 in JSON.

The configuration is read by `/api/web/`, verified and saved according to revision. The browser does not save the master copy; the playback settings take effect the next time the session is started, and the backend default value requires a backend restart. Web does not control FSA, Hook, EDID or playback. Layout switching migrates calibration/wiring by channel name, ROAR 6DoF slots are not selectable. See [Web Contract](../../docs/archive/decisions/web-configuration-host-20261023.md), [Layout](../../docs/archive/core/output-layout-presets-20261023.md), [Sidenote](../../docs/archive/core/recording-isolation-20261024.md).

`8765` of `serve.py` below is an independent preview entrance, which does not start configuration API, capture or ASIO; do not let the preview service occupy `8767` when FSA Web Host is running. The original simulation, playback and sonar/sound spectrum entries are maintained.

2026-10-18. Supports simulation, Wwise one-minute playback and independent real-time telemetry service, contract 0.2 capability negotiation, both subscription, decoded-input/render-ready. Wwise run-05 has observed the sound navigation/sound spectrum and 140 object details in the browser; all final effects, independent frame rates and long-term resources are yet to be tested. For the latest fix, see Joint Debugging Overview (reference outside this source tree).

Start the warehouse root directory:

```powershell
python modules/visualization-frontend/serve.py
```

Browser access console prints `http://127.0.0.1:8765/`; when the port is occupied, add `--port 8767`. The server only monitors the local machine, provides front-end and read-only contract files, does not list directories, and does not expose other project files. The page requires HTTP access, and HTML cannot be double-clicked directly. No npm installation, external CDN or web fonts required.

## Wwise one minute recording playback

There is real source data playback adaptation, see Verification Record (reference outside this source tree). First run `python modules/visualization-frontend/prepare-replay.py`, which reads the original one-minute source capture retained, measures all 6000 packets, and re-renders 12 channels using the existing ROAR DLL. Generate 600 10 Hz snapshots and 60-second history summary, and write them to `build/wwise-visualization-replay`; if the manifest already exists, repeated generation will be refused.

After starting the above local service, visit `/?replay=wwise-minute`, or click "Data Source → Load Wwise One Minute Record". Supports pause playback, playback from the beginning and progress bar, caching up to 3 second-by-second data blocks. The page is clearly marked for recording and playback, and does not take over the audio, does not rerun Wwise, and does not play PCM. The historical window uses a complete 60-second record: 10% coverage in 10 minutes and 1.67% coverage in 1 hour.

## Realized

- Sound Navigation: Object direction shell, relative trajectory, short transient light spot, rotation/zoom/reset, mouse selection and keyboard object selection. Unknown positions remain in the object selector and are not crammed into the center; zero vectors do not fake directions.
- Sound spectrum: Ideal Layout714's smooth energy sphere, speaker marks, 12-channel full-band RMS meter; fixed −60~0 dBFS color scale, LFE displayed separately. The unknown layout still displays the channel table and does not apply the default space layout.
- Object/channel details, gap/stale/disconnected status, coverage, telemetry missing blocks and event overflows; clearly differentiate between simulated and measured sources.
- Simulation data 10 Hz, WebGL screen upper limit 30 fps; pause to freeze the screen while continuing to receive data; reduce dynamic shutdown of short trajectories and transients. Hiding the page stops drawing and simulation advancement.
- Real-time/10 minutes/1 hour switching. The demo history is an independent fixed fixture, and the page continues to prompt that it is not the cumulative result of the demo animation; the browser history collector is not implemented.
- The data source panel supports importing snapshot/layout/history JSON, or connecting the capabilities, layout, history HTTP interface and WebSocket snapshot of the same service. The event cache has a maximum of 256 items and a maximum of 10 points per object trajectory; objects are removed based on absence only in complete snapshots, and generation changes do not change trajectories.

Shortcut keys: Space to pause/continue, R to reset, Esc to cancel selection; buttons and object selectors can all be operated by the keyboard. The narrow screen changes to a top-bottom layout and a two-line channel table; channel data is still provided when WebGL2 is not supported.

## Access real services

Click "Data Source", enter `ws://127.0.0.1:<service port>/v1/telemetry` and connect. The front end first reads the same host `/v1/capabilities`, selects the first route and gets the corresponding layout, and then sends subscribe. By default, it applies for 10 Hz, full band and supported modes. The service must first return negotiated before sending the snapshot; the independent service has implemented these endpoints, and the connection must meet the service Origin policy. Multiple routing UI, automatic reconnection, authentication and complete error codes have not yet been implemented; users must manually reconnect after disconnection in this version.

The JSON structure is checked against the schema generated by the repository, and the browser additionally checks for key values, coverage, identity, location transformations, and event semantics. External text is only displayed with textContent. The upper limit of single message/file temporary protection is 2 MiB, which has nothing to do with the old 64 KiB original performance target. The browser structure validator only supports the schema vocabulary of this project, and the server should still use the complete contract verification.

The original 64 KiB/snapshot, 0.5 MB/s budget is not blocking front-end development. Keep the complete JSON according to user confirmation, verify the real resource overhead first, and then decide whether to optimize. Audio does not wait for the interface and bounded queue requirements remain.

## Authentication and restrictions

Run when Node.js is available (no dependencies required):

```powershell
node modules/visualization-frontend/test-data.js
```

This front-end JavaScript verification: 17 existing fixtures passed, 9 error messages were rejected, covering field order irrelevance; app script syntax passed. Use the available JavaScript runtime environment to execute, without system node command dependency. 13 groups passed the original Python contract test.

The browser has verified: simulated first screen, two-mode switching, 12 channels, LFE details, 10 minutes/1 hour fixed history, importing six-axis fixtures and selecting unknown positions, pause logo; 390px narrow screen check without horizontal overflow. This section is for early simulation acceptance; see the joint debugging overview for the subsequent connection between real WS and Wwise on-site browser. Band FFT, object long history, complete nebula/light cone, WebView2 shell, GPU context recovery and one-hour resource testing are yet to be followed.

Current display direction and energy diagrams are for observation and are not proof of actual sound pressure, sound beam, room simulation, or device playback. For the first real access, you should first check the channel table and coverage, and then verify the spatial effect.

## Sound Navigation V0.2 Prototype

See Review, Algorithm, and Input Limitations (reference outside this source tree). The controllable movement drop-down box on the page covers steering, panning, mixing sound sources, identity multiplexing and interruption. Real playback uses 100 ms snapshots and updates the visual state in about 50 ms; no translation is generated when there is no credible scale. Test: `node modules/visualization-frontend/test-navigation.js`.

## Weak continuous sound source screen

Acoustic Navigation's weak continuous sound source wire mesh grows by a path: each object first explores laterally and bends slightly inward. The number and angle of the main filaments change according to the object, and shorter side branches emerge from the middle of the filament bundle. Objects that are adjacent and of similar duration are gradually connected from both ends along a concave local curved path, and the connected section is brighter. The proximity relationship only selects connectable local object groups and does not directly draw straight lines between points; deep field mist is distributed along the tow. Special depth point series no longer draw straight lines between objects.

Some peripheral seeds grow another shorter, darker outward probe filament. The number of outward layers is less than that of the inward main filament. There is only a weak haze along the line to form a thickness without restoring a neat radial outline.

Mature peripheral filaments, connecting sections and membrane surfaces share a slowly changing spatial deformation field, resulting in bounded swimming; each path remains fixed at the real object point, and nearby filaments and the "reduce dynamic" mode do not swim. This animation only affects visual vertices and does not participate in object position, trajectory or heading calculations.

Connected local network segments are briefly highlighted in tile order. Nearby low-pitched objects still retain darker filaments and local connections; only adjacent paths judged peripheral relative to conversational scale will generate transparent diaphragms concave toward the listener side. When peripheral low-pitched objects disappear or fall to silence, the visual layer predicts up to 0.5 seconds along the last continuous displacement (the display displacement is capped), and then fades out and converges in 0.5 seconds; this afterimage is not included in the solution of active objects and heading. This distance is a relative visual scale without metric calibration.

Objects that actually display transient aperture will exit the screen and ordinary clouds within the validity period of the event and 0.2 seconds thereafter; if low-loud activity continues thereafter, they can only enter the network after meeting the continuous condition for about 0.65 seconds again. Normal clouds also require at least 0.35 seconds of continuous activity, so the short sound will not leave a cloud behind. The onset in the playback is a high-frequency candidate event. Only the strongest 6 apertures are displayed in each frame. All undisplayed candidates cannot be regarded as confirmed transients. The light blue depth point column markers are another spatial distribution cue and do not represent transient classification.

On-site objects may be long-lived, while server-side onset is only triggered when an identity is created or when the source flag appears. Between consecutive and complete measured snapshots, if the window RMS of the same dynamic object increases by at least 10 dB, the current RMS is not less than −42 dBFS, and the peak value is at least 0.05, a **visual transient candidate** with 450 ms cooling can be added to the front end; existing events on the server will be given priority. Candidates only enter the front-end aperture and short-term visual exclusive state, do not write back telemetry, motion estimation or game semantics, and do not infer broken frames and first frames. By default, the 6 strongest apertures are displayed; if there are no candidates near the center of the listening position, the strongest 1 in the area will be retained, with a limit of 7. The center area is judged by a session-fixed visual radial scale, without pretending to be metric distances; the exact origin also draws the aperture toward the camera. Reduce Dynamics turns off transient animation.

Normal clouds are driven by dynamic objects that last for at least 0.35 seconds and have an RMS higher than about −42 dBFS, with size, diffusion, and transparency adjusted based on their relative radial position in the frame. The clouds near the center of the picture tighten and become lighter, while the outer edges maintain their original intensity; this level is not a judgment of actual sound source distance or acoustic category.

The RMS intensity of the same object for the first 0.35 seconds is taken when the clouds and fog enter: the early stronger continuous sound enters with a brighter initial fog layer and a shorter fade-in time; the medium loudness slowly unfolds with medium brightness. After entering, the steady-state intensity of the clouds and fog still follows the current measured energy, and the early intensity does not permanently cover subsequent changes; the short sound and transient aperture period do not enter.

The original position on the system side of the one-minute playback is consistent with the first frame of export. The Windows interface stipulates that the coordinate unit is meters, and Wwise can convert from the game unit according to `fGameUnitsToMeters`; the acquisition does not include the game conversion coefficient or independent physical scale, so the front-end contract is still marked as `source-unverified`. The median length of the entire object position modulus is about 18.67 interface units, the maximum is 152; about 3,152/76,800 dynamic object snapshots are the precise origin. The reason for the large cloud in the center is that the original rules expand the continuously loud objects close to the origin into four large fogs, which are not large sound sources inferred from the distance data.

## Original audio track vocal candidate test

First install `onnxruntime` and `silero-vad==6.2.1` to `build/voice-vad-deps`, and then run `python modules/visualization-frontend/probe-voice.py`. The script reads `work/wwise-minute/sources/Slot*.wav` and generates an independent `voice-candidates.json` in the original playback directory without changing the WAV or measured playback chunks. Open `/?replay=wwise-minute&voice=probe` to see the arc surface corresponding to the candidate interval; remove `voice=probe` to return to the original playback. Do not append `navigation=legacy` unless interested in comparing to an older solver. This label is only a hypothesis of offline voice activity and cannot prove that it is a human voice or corresponding person, nor can it infer the content of the speech from VAD.

```powershell
python -m pip install --target build/voice-vad-deps onnxruntime silero-vad==6.2.1
python modules/visualization-frontend/probe-voice.py
```

## Sound Navigation V0.3 motion estimation

The current default is to use short-term stable reference filtering, weighted Procrustes, RANSAC/IRLS, and source-independent residual streams; Wwise playback estimates relative steering and common flow direction using internally consistent source coordinates, and the units are not calibrated to metric. See V0.3 Review and Validation (reference outside this source tree) for details and input boundaries. Add `?navigation=legacy` after the page address (add `&navigation=legacy` if there are parameters) to switch to the reserved V0.2 solver, and `navigation=off` to turn off automatic heading. Run `node modules/visualization-frontend/test-registration.js`, `test-navigation-core.js`, `test-navigation.js`, and `test-navigation-recording.js` verification after one minute of ready playback.

## Peripheral grid connection and inertial feedback

The curved connections between objects are colored according to the path position: the endpoints of the two objects are brighter, and the middle section is only faintly visible when stationary; the block flashing retains the gradient of the endpoints, and the middle section is briefly brightened where the flowing current passes. The fog of the connected segment is also attenuated according to the endpoint position. Brightness changes follow existing line segments and do not increase the WebGL draw batch or vertex count.

Peripheral tows and connecting paths produce small, stable visual lags for credible co-flow and relative rotation; original object coordinates and their connecting endpoints remain fixed. The controller has three translational and three rotational responses, but playback has no true listener 6DoF attitude and no reliable metric scale; missing or untrustworthy motion axes remain at zero input. Stabilizes when manually observing, low-confidence states, and reducing dynamic effects. This feedback is for vision only and does not write back pose estimates.

The native QuickJS synthesis test repeats 190,000 point calculations on a 19-point connection curve, and the single-point processing time after adding inertia increases from approximately 2.04 microseconds to 2.55 microseconds; this is an interpreter stress test, not a browser CPU/GPU frame time. Real browser performance and visual strength still need to be observed on the replay page.
## FSA configuration portal (2026-10-23)

After FSA automatically starts the independent Web API Host, visit `http://127.0.0.1:8767/`, or click the FSA tray icon three times in a row. A new "Configuration" link is added to the visual homepage to enter `configuration.html`, which provides graphic editing and complete JSON editing of existing playback, ASIO, DSP, routing, radius mapping and back-end JSON. Saving the configuration does not control the operation, and the original drawing and telemetry logic remain unchanged. When using the old read-only preview service alone, the configuration page will prompt that the API is unavailable. See [Web Host Contract](../../docs/archive/decisions/web-configuration-host-20261023.md) for details.

Pure model/DOM contract validation: `npm run test:configuration` and `npm run test:configuration-page`; no browser automation required.
