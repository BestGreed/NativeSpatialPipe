# FreeSurround residual audition

Decoder and channel maps vendored from https://github.com/kodi-adsp/adsp.freesurround at 03ec0899e1d45ff40c4184d17429b8a1bab65ccb (GPL-3.0-or-later); KissFFT carries its own BSD license in vendor/kissfft/COPYING. This adapter is GPL-3.0-or-later. No redistribution of the combined NSp binary is performed by this local experiment.

Local upstream changes: replace Kodi-only channel identifiers with unique local bit flags; remove Kodi DSP wrapper include; correct the float output buffer shift/clear byte counts (upstream used sizeof(double)), use memmove for overlapping buffers; Flush also clears input and spectrum history to isolate discontinuities.

1024-sample stereo blocks, standard 7.1 geometry, center image 0.7, bass redirection disabled. No synthetic height channels. Streaming adapter contributes 1024 FIFO samples plus the decoder's 512-sample delay, total 1536 samples/32 ms. Original stereo, protected vocal, legacy comparison and neutral recombination all receive the same delay. Separation's 80 ms buffer is additional. Controls remain per-source audition controls; no final window-following object architecture is claimed.

## Independent low-latency experiment

`Build.ps1 -TargetDirectory build/freesurround-half512 -Window 512 -HalfHop`
builds a separate candidate; the default remains the 1024/full-hop decoder.
`DecodeHalf` advances exactly one half-window through the same spectral analysis
and overlap-add. The adapter reads the finalized half at offset N/2 rather than
retaining an extra half-window. With a 512-point window this yields a measured
512-frame (10.667 ms) content delay. `Test-FreeSurroundHalfHop.py` checks exact
equivalence against the full-hop **512-point** reference after a 256-frame shift,
impulses at seven block phases, and reset isolation. This does not establish
perceptual equivalence to the live 1024-point configuration.

The half-hop build additionally exports experimental `nsp_fs_mature_open`,
`nsp_fs_mature_push`, `nsp_fs_mature_pull`, `nsp_fs_mature_reset` and `close`.
Push accepts 1–480 planar stereo frames; pull returns one finalized H-frame,
12-channel planar block and its original source start frame. For H=256, after
consuming exclusive residual end E the block is `[E-512,E-256)`. Negative warmup
blocks are not published. Pull never depends on another input call. A preallocated
16-block queue rejects a full push before consuming any samples. Reset discards
queued data; the caller must own and validate stream epochs.

`Test-FreeSurroundMature.py` verifies exact legacy-half512 source parity for
120/128/480 input blocks, EOF coverage, reset isolation and atomic backpressure.
The NPU joint probe supports `Build-DeadlineProbe.ps1 -MatureFs` and verifies
combined protected-vocal plus FS output against a simultaneous legacy FS reference
when invoked with the final argument `verify`. Its independent240-frame consumer
is a **pre-DSP simulation**, not an opened WASAPI device.

The build also accepts non-power-of-two windows480 and240 with half-hops240
and120. The mature packet contract is generally `[E-N,E-N/2)` after consuming
exclusive residual end E. `Test-FreeSurroundMature.py <build-directory>` checks
the same-window reference for each. These are separate candidates, not changes
to live playback. The480/240 replay removed20-ms misses;240/120 was faster but
changed low-frequency output substantially. Neither is sample-equivalent to512.
See `docs/research/nsp-fs-window-alignment-20261104.md` for deadlines and quality
results, and `tools/lsac/Compare-FreeSurroundWindows.py` for comparisons.

Window510/step255 is also supported and passes same-window exact parity.
It is closer to512 in the tested energy distributions, but breaks alignment
with model128: the60-second20-ms sweep misses2815/11800 requests (P9921.9089ms).
See `docs/research/nsp-fs510-evaluation-20261104.md`; it is not a low-latency
replacement for480/240.
