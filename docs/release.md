# Installer release preparation

The user-facing binary delivery is an installer. A ZIP staging directory is an
internal build input, and source/dependency archives are separate developer
artifacts. They are not installed alongside the application.

The installed runtime contains the application, required DLLs, compact Python
runtime, required execution scripts, profiles, web assets, licenses, version
manifest and one diagnostic `AGENTS.md`. Development reports, test fixtures,
agent instruction collections and source archives are excluded.

## First-use setup

The installer discovers the current Windows default capture endpoint and its
available spatial modes. It collects the output backend and actual device or
network choices, layout and channel routing, plus LSAC filter selection where
required. Mixed spatial/PCM input and spatial-only input are explicit choices.

ASIO setup checks installed-driver capabilities, channel bounds and 48 kHz
support. AoIP setup chooses the sending interface and clock discovery/fixed
master; the receiver subscription remains a network-controller operation.
WASAPI/LSAC setup requires a distinct physical output and a complete supported
adaptation filter. Do not finish setup using placeholder device IDs or paths.

Setup generates the selected user's configuration with installed library
paths, validates it through the product scheduler, and prepares the running
Windows capture layout. Upgrade preserves existing settings unless replacement
is explicitly selected, in which case it keeps a backup. Uninstall retains
user settings. A user-triggered ASIO channel test can verify audible routing;
it is not played automatically.

## Version and source correspondence

Record the private implementation revision, public core correspondence,
installer tooling hashes, runtime file hashes, feature scope and matching
source sidecar. Public source may omit private endpoint extensions or presets.
Provide applicable license texts and matching component source delivery
separately; do not turn a consumer installation into a developer checkout.

Installer packaging is maintained separately from the public core build.
Verify the actual setup program in an isolated install directory: first
installation, upgrade preservation/replacement, start-menu entry, configuration
paths, runtime dependencies and uninstall behavior. Also record actual output
checks for the hardware paths being claimed. A configuration parser result or
no-device fixture is not audible-output evidence.

The public CI validates the cleaned source candidate; it does not publish a
formal installer automatically. See the [user guide](user-guide.md) for the
installed workflow and [CI scope](ci.md) for source validation.
