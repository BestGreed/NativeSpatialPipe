# Inferno Windows: Dedicated integration built into NSP

Starting from 2026-10-28, NSP will directly include the source code of this directory. Cargo uses the path in the warehouse and does not pull variable branches or modify external forks during build.

## Source and License

- Original Inferno: <https://gitlab.com/lumifaza/inferno>; GitHub mirror <https://github.com/teodly/inferno>.
- Self-maintained Windows fork: <https://github.com/BestGreed/inferno-windows>.
- This import comes from the NSP local Windows integration working tree, HEAD `0e770093b85c6f8b1fa6b1db0c4b7c7756ed9a8e`, which contains subsequent uncommitted adaptations; it is not an intact copy of a commit in the public Windows branch.
- `NSP-SOURCE-MANIFEST.json` saves the submission, status, source of each warehouse and the original SHA-256 of 190 files during import; it is an import snapshot list, and subsequent maintenance is based on the NSP Git history.
- The atomic modules searchfire, usrvclock-rs, and alsa-sys-all have been expanded into ordinary source codes. Please see the list for submission records. No nested Git metadata is included, and submodule initialization is no longer required.
- Inferno and Windows clock package declare `GPL-3.0-or-later OR AGPL-3.0-or-later`; retain the original text of `inferno_aoip/LICENSE`, `LICENSE-GPL`, `LICENSE-AGPL`. The root project Apache-2.0 does not cover this directory.
- searchfire and usrvclock-rs each retain the MIT/Apache statement, alsa-sys-all and other packages are subject to their respective Cargo and license files; vendor/netdev retains the MIT license and `PATCH.md`. Do not rewrite third-party authorizations uniformly.

## dedicated adaptation boundary

The built-in version retains Windows QPC/PTPv1, multicast/control, keepalive and TX scheduling, as well as `PacketSource` required for NSP, commit after successful send, multi-stream cursor, frames per packet configuration and existing statistics. NSP's pool block, DSP, flow selection, FSA and user configuration are still in NSP's own module and will not be moved back to this directory.

This directory retains the original workspace and Linux-related source code to keep the source intact; NSP build only selects the required packages. The Windows test host remains the experimental entry point; 250 µs TX, long-term clock drift compensation and full Dante telemetry are not yet complete.

## Build and maintain

Product entrance: `tools/aoip/Build-NativeOutput.ps1`. Clock and independent transceiver diagnostic scripts also reference this directory. The build no longer applies `native-packet-source.patch`, the patch remains for historical reference only.

When upgrading the general Windows fork, explicitly compare the source commit with the NSP-specific differences, and then update after completing the build and regression; this directory will not be automatically overwritten. The original `work/inferno-aoip` is temporarily reserved as a historical working tree, and this directory does not depend on it. ROAR and other external dependencies such as the compilation tool chain are not within the scope of this migration.
