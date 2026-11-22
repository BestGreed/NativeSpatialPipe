# FSA and playback configuration

Start FSA from the build output described in the [build guide](build.md).
Use its tray entry to manage spatial modes and NSP sessions. The Zig
scheduler handles session control. The web panel edits playback settings,
which default to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`.

Use the [playback schema](../modules/zig-scheduler/playback.schema.json)
and the [playback example](../modules/zig-scheduler/playback.example.json)
for field names and values. The
[WASAPI example](../modules/zig-scheduler/playback.wasapi.example.json)
illustrates the stereo output path. Apply backend changes by restarting the
corresponding session. Browser identity is optional and uses Node.js at
runtime. See the [project README](../README.md#optional-nodejs-runtime).


The web page is opened by clicking the FSA tray icon three times in quick
succession. FSA handles runtime control; saving web settings does not restart
a running session. Ordinary session changes apply on the next start. Restart
FSA for resident AoIP configuration/layout changes.

On a locked or unfinished session, use the same NSp tray entry to request stop
and verification. A successful cleanup allows the next click to start. The
online controller may also recover an old session during status checks. See
the [user guide](user-guide.md) for startup and recovery, and
[capture compatibility](capture-compatibility.md) for layout status/import.
