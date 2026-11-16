> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Web configuration and status Host

2026-10-23. Implemented by user confirmation: No Web → FSA control is added in this round. The independent Web API Host is started with FSA, providing status reading, existing JSON configuration reading and saving, and reusing the original Web front end to add configuration pages. FSA still has start and stop, spatial mode, Hook, endpoint mutual verification, HT Support and emergency stop.

## Modules and life cycle

- `nsp-scheduler.exe web-host` is an independent process and independent Zig module; it does not create an audio host, PCM queue or ASIO device. Reusing an executable file does not mean co-processing with the audio thread.
- FSA starts the Web Host when it starts; when FSA is run as administrator, it creates a normal permissions Host from the Explorer user token and does not hand over the administrator token to the HTTP service. Failure to start will only be logged and will not affect FSA management and sound.
- The listening address is fixed at `127.0.0.1:8767`. FSA does not automatically pop up the browser when it is started; the tray icon is released three times in a row with the left button, and it is completed one after another within the interval between double-clicks on the system, and the home page is opened with the default browser. Click/double-click and wait until the judgment window ends to display the original menu. Right-click to display the menu immediately and clear the click count.
- Web Host binds the FSA process handle that starts it, and FSA exits on its own after exiting; closing the browser does not stop FSA or the sound. After the Host exits unexpectedly, it will try to start again after the next three clicks.
- The FSA worker thread writes existing state into a fixed, pointerless, read-only shared memory observation structure; there is no new endpoint query thread. The web request reads the snapshot and queries the existing backend; if the backend is not online, it returns offline and does not call `ensure`. Snapshots will appear expired when the worker thread is blocked.
- The original `app.js`, drawing, data source, WebSocket telemetry and playback logic remain unchanged; only the configuration link and corresponding link style are added to the home page. The original telemetry service still runs according to the audio session life cycle and is not merged with the configuration host.

## Configure contract

| Configuration ID | File | Verification and Validation |
|---|---|---|
| `playback` | The default is `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` from 10/24; the customized `PlaybackConfig` will continue to be valid, the default files in the old installation directory will be retained and the copy will be migrated | Reuse `desktop_playback.parse`; the next startup session will be read; the default save will not modify the installation INI |
| `backend` | Existing backend home `config.json` | Existing `version`, `spatialFormat`, `fullTiming`; the backend service is loaded after restarting, and FSA startup may explicitly overwrite the space format |

Reading missing configurations only provides candidate default values, does not create files, and does not register playback configurations. Register `PlaybackConfig` after successfully saving and playing JSON for the first time. The candidate DLL path uses the current build location of this workspace, and the driver must be selected or filled in; the verification does not open the driver, and does not guarantee that the module file, device sampling rate, or buffer is actually available.

The graphical interface covers rendering backend, source streaming, device driver and sampling/buffering, clock matching, pre-buffering, drain timeout, module path, master volume, DSP bypass, 12-channel trim/delay, limiter/lookahead, ROAR radius mapping, naming order/custom routing. ROAR's current mode is OLR; special mapping for LIRA is left in the backend and does not accept ROAR's radius option. The deprecated limited wait/underrun times field is only compatible with the full JSON and does not provide interface control again.

Wiring display correction: calibration and routing partitions; the complete channel arrangement and the "Output Slot → Channel → ASIO Device Output" table are directly displayed in the routing, and the preview is synchronized when switching between standard/DVS/custom arrangements. The interface device channel starts from 1, which is consistent with the common label of the device; serialization still uses the zero-based index of the original JSON and does not change the actual mapping contract. Switching the arrangement preserves the physical device channel configuration of each slot, and the calibration always follows the standard channel.

Save write-only configuration, no automatic application, no restart, no starting of NSP, no opening of ASIO. The page shows the saved revision and the effective time; there is currently no reliable "Run Configuration File Revision" record, and it clearly shows that it is unknown. No new revision of the file is used to fake the current effective revision. The backend status returns its actual memory configuration and session data for verification. Damaged JSON will be returned in its original form for repair, and will not be overwritten with default values without authorization.

## HTTP interface

| Methods and paths | Function |
|---|---|
| `GET /api/web/bootstrap` | The writing nonce, read-only/configuration capabilities of this Host |
| `GET /api/web/status` | FSA management snapshot, existing backend status; unknown/expired clear return |
| `GET /api/web/drivers` | Only reads the local ASIO registration items and does not load the driver |
| `GET /api/web/config/{playback,backend}` | Path, existence, SHA-256 revision, JSON, verification result, effective time |
| `PUT /api/web/config/{playback,backend}` | `{revision, config}`, share verification first, then check old revisions, atomic save |

The interface does not provide file path parameters, directory browsing, arbitrary configuration file manipulation, run control, or control agents. Write requirements `application/json`, `X-NSP-Token` for this Host; request strict inspection of Host, Origin and browser cross-site flags, and do not provide CORS. The request header is 8 KiB, the request body is 64 KiB, and the single connection has a bounded timeout. The read-only nonce is protected by the browser's same-origin policy; the service is only for the current local user environment and has not been extended to multi-user authorization services.

Writes use temporary files in the same directory, synchronization and atomic replacement; Web write operations are serial and hold private locks, and old revision conflicts return 409. External editors that do not respect this lock may still race between checks and replaces, and cannot claim to implement cross-file transactions for any external tool. When playback JSON has been written and INI registration fails, the interface clearly fails and the file is retained for retry after repair.

## Validation and scope

- Isolated HTTP regression: `tools/zig-scheduler/Test-WebHost.py`, no real audio capture/ASIO opening.
- Configuration model and simulated DOM form regression: `test-configuration.js`, `test-configuration-page.js`, without using desktop or browser automation.
- Native click count/snapshot ABI: `web_host_test.c`; original NSp UI/exit and return to continue checking the start and stop behavior.
- The actual appearance of the browser, default browser association, and physical three-clicks are verified by user operations; automated verification has passed without pretending to be manual interaction.

Native deployment has been verified: Administrator FSA starts normal user Host, owner is consistent, management snapshot and configuration page are readable. For limited evidence, see the verification record (development reference is not provided with the open source code). The original playback JSON and INI playback pointers of this machine have not been changed.

This round does not introduce new Python/.NET product services, does not modify audio real-time paths, does not add FSA control APIs, and does not handle release installers, remote access, multi-user authorization, or configuration hot updates.
