# Licensing

This integration is distributed as GPL-3.0-or-later because its decoder links the GPL bridge API and adapter crates. It is not covered by the root Apache license as a whole.

The E-AC-3 library, TrueHD library and original Harletty adapter sources retain their upstream Apache-2.0 notices; bridge_api retains GPL-3.0-or-later. The adapter crate manifests describe the resulting linked artifacts as GPL-3.0-or-later. See each vendored LICENSE, Cargo.toml and vendor/ORIGINS.json. MinHook is built from the repository's pinned work/minhook and retains its BSD license.

The vendored codec implementations are unchanged. Local manifest path adaptations and the TrueHD early-frame label padding are in the NSP wrapper, not codec algorithms. The runtime does not load harletty_dolby_bridge.dll or a system Dolby decoder to perform decoding.
