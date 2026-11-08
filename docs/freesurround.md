# Standalone FreeSurround decoder

This optional decoder accepts 48 kHz planar stereo and produces a 7.1 bed in the first eight positions of the 12-channel output buffer. The height positions are silent. Its half-hop interface exposes completed ranges with original source frame indices.

```powershell
./modules/freesurround/Build.ps1 -LlvmBin $env:NSP_LLVM_BIN -TargetDirectory build/freesurround-480 -Window 480 -HalfHop
```

The 480-frame window produces 240-frame mature ranges. Push takes 1–480 frames. Pull returns a completed range or indicates that more input is needed. A caller should drain the bounded result queue and reset it on a discontinuity.

The adapter and decoder are GPL-3.0-or-later. The decoder and channel maps come from [Kodi FreeSurround](https://github.com/kodi-adsp/adsp.freesurround/tree/03ec0899e1d45ff40c4184d17429b8a1bab65ccb), with changes for standalone channel identifiers, float buffer handling and reset isolation. See the [GPL terms](../modules/freesurround/vendor/LICENSE.md) and [KissFFT terms](../modules/freesurround/vendor/kissfft/COPYING).
