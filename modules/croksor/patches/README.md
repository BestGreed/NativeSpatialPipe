# ROAR patch for NSP

Added the seventh item `olr-direct-single.patch`: Single object safe direct writing to the caller planar output, removing the internal PCM workspace/clear/copy; dual-object and larger configurations retain a workspace allocated when added, reused object by object, and maintain the original overlay sequence. Implementation, regression and first packet allocation results (reference outside this source tree).

The standard is built in a temporary isolation warehouse to verify the entire patch chain in reverse order to avoid misjudgments caused by subsequent patches modifying early context and not resetting the real source code. Fixed HEAD New application complete chain and duplicate builds verified.

Fixed upstream commit: `60c96e566c1dd2838468006b63ada716549f82b2`. `../Build.ps1` Verify/apply in order, applied patches will not be re-applied; conflicts will report an error immediately.

1. `olr-block-mix.patch`: Existing single-object output copy optimization retains multi-object accumulation order.
2. `olr-zero-gain.patch`: Added on 2026-10-22; Fixed/Interp Exact zero gain skip, does not change non-zero calculation expressions, interpolation or accumulation order, does not use near-zero thresholds.
3. `olr-silent-block.patch`: Skip multiply-add only if the actual overlapping input range is all exact zero; still advance object metadata, time and queue, retain size error checking.
4. `olr-preserve-interp.patch`: Combine double-endpoint interpolation traversal, maintain the addition order of all start inputs first, and then all end inputs sample by sample, and do not merge floating point expressions.
5. `olr-gain-reuse.patch`: Explicit opt-in of immutable gain calculator; 4-item exact-coordinate gain pool per interpreter, 64-query decay scoring, up to one elimination per cycle on hits below 12.5%. No coordinate quantization, dynamic expansion, cross-thread locks or background GC.
6. `olr-context-geometry.patch`: Object elements of the same ROAR context share an immutable geometry; NSP is explicitly prepared before the capture starts, and is isolated according to the existing block→pipeline binding. The four contexts are prepared four times, and package processing and object replacement are not rebuilt. Object gain pools, position, interpolation history and workspace remain independent.

Zero gain patches are used for OLR paths of NSP verified limited PCM. NSP rejects NaN/Inf before core calls; this patch makes no commitment to preserve propagation behavior for arbitrary non-finite inputs. Any preset negative zero accumulator may retain the zero sign, and the test allows this numerical equivalence; the complete output of the NSP normal initialization requires byte-by-byte consistency, and 800 packets in both direction/full radius modes are passed.

Verification tool: `../Test-ZeroGain.py`. Covers real processing block code, various frame numbers, channel numbers, overlapping boundaries, missing interpolation endpoints, positive and negative zeros, non-zero minimum gain, and output size errors.

`../Test-GainReuse.py` additionally validates exact keys, contiguous transpositions, 4-item capacity, single eviction, errors not cached, calculator opt-in, instance isolation, and metadata continuity during quiescing. The pool is 552 bytes/object (current x64 build), ~69 KiB for 128 entries; this is a fixed space gain cache, not an object identity or slot GC.

This round of candidate verification (reference outside this source tree) includes original material, all-zero, silent switching and zero-hit displacement conditions. Diagnostic counts only exist in isolated builds, production patches do not introduce corresponding statistics modules or ABIs.

Building, lifecycle and audio validation of shared geometry by block (reference outside this source tree). This does not mean that dynamic layout/6DOF has been implemented; the current fixed layout is inherited while the context is alive and is not re-constructed per package.

Single-round Sonic 140 tank field pressure test (reference outside this source tree). The patch source file retains the upstream BSD 3-Clause Clear/Patent License Statement and does not change its source license.
