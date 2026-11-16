# Extract module sources and licenses

Source: 0.2.7-preview.14 snapshot of the original `SpatialASIO/` and DEV16 modular changes. Moving the directory does not change the original license; SPDX and third-party original claims in the code remain unchanged.

- LIRA, own public support and most testing: keep the file's original Apache-2.0 statement, full license [UPSTREAM-LICENSE-APACHE-2.0](UPSTREAM-LICENSE-APACHE-2.0).
- ASIO access, device configuration, layout adaptation, and device C ABI: GPL-3.0-only statement retained, full text [UPSTREAM-LICENSE](UPSTREAM-LICENSE).
- `third_party/asio`: retain the original statement of the SDK and the GPLv3 branch adopted this time; `third_party/libsamplerate` retains its own LICENSE/ORIGIN.
- The distribution boundary of the combination containing the above GPL parts follows the previous decision, and it cannot be marked as pure Apache just because the module is migrated. The issuance target and material compilation are still pending and will not be released this time.

`UPSTREAM-LICENSING.md`, `UPSTREAM-NOTICE`, and `UPSTREAM-THIRD-PARTY-NOTICES.md` are source snapshots of the original complete project. The GUI/font/installer paths do not mean that these contents are still in this module. The current source code set is based on the actual files in this directory and `source-manifest.json`.
