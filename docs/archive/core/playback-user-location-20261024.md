> **Retired documentation.** This page records an earlier implementation. See the [current documentation](../../README.md).

# Migrate playback configuration to user directory

2026-10-24: The default playback configuration is changed to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`, which is in the same user directory as the backend `config.json`; the configuration is no longer written to the FSA installation directory by default. Web configuration saving does not modify the installation directory INI, and the revision lock is also in the same directory as the target JSON.

## Selection and migration rules

- Explicit `PlaybackConfig` will continue to be used when pointing to other customized absolute paths, and will not be moved or overwritten.
- When this entry is not configured, or it happens to point to `playback.json` in the same directory as the old FSA INI, the user directory default file is used.
- If there are already files in the user directory, they will be used first and old files will never be overwritten.
- When there is no file in the user directory and the old file in the same directory exists, the Web Host starts copying the old bytes to a temporary file in the same directory. After completing the synchronization, the user copy is established by atomic movement without replacing the target. Old files are retained; repeated starts will not overwrite them. Malformed JSON is also migrated unchanged, and errors are displayed on the existing configuration page for repair.
- Without any configuration, the query just returns the default template and user target path, without creating files or starting the backend. The user configuration is created only after the first valid save.

Native FSA uses the same default rules as web. FSA can read-only fallback to the old file while the web migration is not complete; user copies can be selected after the migration is complete or after the first save. Keep capturing/telemetry without enabling ASIO when there is no file. To continue using an independent custom configuration, explicitly specify a location other than the old default; clearing the INI will not ignore an existing user default file.

Saved configurations still take effect the next time you start a session. PCM/snapshot archiving is turned off by default and migration does not change drivers, layout, routing, DSP or recording settings. The backend status and log directory remain unchanged. `--home` is an isolation test/explicit backend directory coverage, which defaults to `LOCALAPPDATA` of the startup user; ordinary Web Host and privilege-elevated FSA should be in the same Windows user configuration context.

## Limited verification and release

Web API regression covers the new default directory and does not write the installation INI; each of the six isolation migration scenarios is started twice, covering no configuration, old file/old default pointer, user file priority, custom path and malformed JSON. The native FSA test overrides the actual boot parameter selection for default/fallback/explicit paths and does not open the audio device.

The local default scheduler is updated synchronously with FSA, and the user directory copy is checked through the actual configuration API; see `experiments/playback-user-location-20261024` for a summary of testing and deployment. The old development configuration remains as a rollback copy and does not continue to be the default save target.

This round synchronizes the main warehouse `alpha-dev/main`; the public source code is cleaned and exported along the existing public history, and the main warehouse `open-source` and the public warehouse `alpha-dev/main` are updated. Public export does not carry user configurations, recordings, credentials or private experimental payloads; Apache original code, GPL ASIO/SDK and FSA independent terms maintain their original scope and will not be reauthorized due to this directory migration or branch synchronization.
