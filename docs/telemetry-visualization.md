# Telemetry and web visualization

The Zig control service listens on port 8765 by default. The Python telemetry
service uses port 8766. The Zig web host serves the visualization and
configuration UI on port 8767. The frontend displays object positions,
channel activity and session status.

Build the frontend together with the product. The staged assets are under
`build/nsp-product/web`. Launch the session through FSA and open its web
interface. See the [scheduler guide](scheduler.md) for playback settings
and the [build guide](build.md) for dependencies.

## Service diagnostics

The scheduler spawns the Python telemetry service with its standard output and
standard error redirected to `<session out>/telemetry-stderr.txt` instead of
NUL, and writes `<session out>/telemetry-exit.json` (`pid`, `exitCode`) when
the service is reaped. An unexpected service exit therefore leaves a Python
traceback next to the session's `telemetry-service.json` marker. The scheduler
does not restart the service automatically; a non-zero `exitCode` or a
traceback means the 8766 endpoint will stay down until the session restarts.
