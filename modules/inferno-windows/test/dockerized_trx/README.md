Tests Inferno audio over IP transfers in different conditions:
* apps using Inferno library, directly or indirectly
* Linux distributions

Uses [WavDiff from diff_tools](https://github.com/aspt/diff_tools) for sample-perfect comparisons.

## How to use

Run `run_test.sh`, it handles everything including building Docker images.

It may spuriously fail due to realtime scheduling issues, a.k.a. xruns. In that case try again or [tune your system](../../README.md#audio-stability-tips).
