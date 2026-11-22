# LSAC stereo post-processing adapter

This optional Rust library processes a planar, 12-channel 7.1.4 bus at 48 kHz into planar stereo. It contains a measured FIR downmix, an optional virtual bass stage, and a separate equal-loudness processor that a host can drive with hardware attenuation. Applications construct the adapter with their own filter data and choose their own listening-level profile.

## Build and test

From the public checkout root, run the crate tests with the Rust toolchain from the [build guide](build.md).

```powershell
cargo test --locked --manifest-path modules/lsac-adapter/Cargo.toml
```

Croksor links this crate for the product LSAC path; Zig configures the session and its WASAPI output. Standalone hosts must supply block ownership, frame alignment, an output sink and their limiter policy.

## FIR input

The adapter reads the `NSPNBF01` format. The header is eight ASCII bytes followed by four little-endian unsigned 32-bit words. The words contain the sample rate (48000), input channel count (12), tap count (32–4096), and reserved value (zero). The payload contains 24 arrays of little-endian float32 FIR taps in output-ear-major order. Each ear has one array per input channel in FL, FR, C, LFE, SL, SR, BL, BR, TFL, TFR, TBL, TBR order. Supply filters measured or designed for the intended device.
