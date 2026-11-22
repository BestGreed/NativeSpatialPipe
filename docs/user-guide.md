# User guide

[中文用户手册](user-guide.zh-CN.md)

NSP processes spatial audio and ordinary PCM for your selected speaker output.
FSA runs in the Windows notification area. The browser provides configuration,
status and visualization. FSA selects Chinese or English from the Windows display language.
The web host uses FSA's startup language, with English as the fallback.

## Installation

Run NSP Setup to generate the initial configuration and program paths.

1. Check the Windows default playback endpoint. To change it, open Sound
   settings, select the endpoint, return to Setup and select Refresh.
2. Select the spatial mode, layout and output path.
3. Configure your device:

| Output | Settings |
| --- | --- |
| ASIO | Install the manufacturer's driver, select it and map named speakers to physical outputs. Device channel numbers start at 1. |
| AoIP | Select the sending interface and PTP discovery or a fixed master. Subscribe the receiver to NSP channels in the network controller. |
| LSAC (experimental) | Select a separate stereo endpoint and matched filter for 7.1.4-to-2.0 output. Laptop filters serve the corresponding laptop setup. |

4. Keep ordinary PCM capture selected for mixed playback, or clear it for
   spatial-only input.
5. Open Final Spatial Audio (NSp) from the Start menu or desktop shortcut and accept
   the Windows permission prompt.

The completion page offers an optional ASIO channel test. Lower the volume,
start the test and check each named speaker. Complete AoIP receiver
subscriptions or select the LSAC filter for your playback device.

## Tray and browser

Find FSA in the taskbar notification area; expand the hidden-icons arrow when
needed. Right-click opens the menu. Three quick left clicks start the web host
and open your browser, following the Windows double-click timing.

- Home: `http://127.0.0.1:8767/`.
- Configuration: `http://127.0.0.1:8767/configuration.html`.
- Live data: `http://127.0.0.1:8767/#live`.

Start the host through the tray icon first, then use these addresses or
bookmarks. FSA controls audio sessions; browser controls operate configuration
and visualization.

## Start and stop

1. Select NSp in the tray menu and wait for startup.
2. Play audio using the selected Windows capture endpoint.
3. Open Configuration and check the current status, endpoint and configuration.
4. Select Data source > Connect with `ws://127.0.0.1:8766/v1/telemetry`, or open
   the live-data address. Inspect input, output and channel meters.
5. Listen at the intended speakers or receiver and check positions and volume.

Select NSp / Stop NSp to stop audio. Select Exit to close FSA and finish cleanup.
Pause, camera reset and data-source controls operate the picture and displayed
data.

Advanced > Start with Windows launches FSA at login; use NSp to begin a session.
Auto switch uses application rules, maintained through capture/edit/reload rules.
Stereo/5.1/7.1/Sonic/Atmos/DTS entries select Windows endpoint modes.
Advanced > View log opens FSA logs.

## Playback configuration

Open Configuration > Playback and common DSP. Keep prepared module paths and
adjust your devices and wiring.

| Node | Settings |
| --- | --- |
| Renderer/output | Driver or endpoint, layout and sample rate. Start with the ROAR OLR preset. |
| ASIO routing | Match speaker names to physical outputs, starting at 1, and check the routing preview. |
| AoIP | Sending-interface IPv4, device name, PTP master and receiver subscriptions. Start with the generated identity, TX latency and buffers. |
| LSAC | Physical endpoint, matched filter and shared/exclusive mode. Start with shared mode and 7.1.4-to-stereo output. |
| PCM | Unified spatial/PCM input follows the FSA session endpoint. |
| Joint upmix | Enable PCM capture, unified input and ROAR OLR 7.1.4. Keep model and library paths. Switch upmix off to reduce CPU load. |
| Media Foundation | Enable Apple Music / Films & TV adaptation, then open the corresponding player. |
| Common DSP | Master, linear trim, delay and limiter. Trim 1 is unity; 48 frames at 48 kHz equal 1 ms. Bypass skips this group. |
| Diagnostics | Enable recording, statistics or snapshots for troubleshooting and check disk space. |
| Backend defaults | Set startup defaults and restart the corresponding backend service. |

Select Validate and save, then apply changes:

1. Ordinary playback and DSP: stop and restart NSp.
2. Output backend or AoIP parameters: exit and reopen FSA, then start NSp.
3. Backend startup defaults: restart the corresponding backend service.

For a revision conflict, reload, reapply edits and save. Apply advanced JSON to
the form before saving. Use FSA for start/stop, endpoint modes and emergency stop.

## Recovery and updates

For silence, check the application output, capture endpoint, live meters,
volume and physical routing. For AoIP, check clock synchronization and receiver
subscriptions. For a browser connection failure, confirm FSA is running and
triple-click its icon. For high upmix CPU load, switch upmix off, save and restart.

For an unfinished session or fault lock, select NSp / Stop NSp for stop and
verification, wait for cleanup, then start again. Online status checks also
handle recoverable old sessions. Advanced > Emergency stop NSp
runs teardown and recovery. After Windows updates, use capture-layout update or
import the matching layout.

Exit FSA before upgrading. Setup preserves existing settings. Select Replace my
existing playback settings with these choices to apply new selections and back
up the old configuration. Uninstall retains user settings.

## Feedback

Settings and reports are under `%LOCALAPPDATA%/NativeSpatialPipe`; the web form
shows the configuration path. Open logs through Advanced > View log.

Include version, Windows build, output path, capture endpoint, spatial mode,
operation time, status code and meter activity. Add driver details or inference
selection records for network or inference issues. AGENTS.md provides diagnostic
agents with version and log locations.
