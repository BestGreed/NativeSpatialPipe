# Source provenance

The current source-refresh candidate takes its implementation from source
revision `e136b6e`. It advances the existing public
history; it does not import the development repository's Git history. Its
starting public revision is `cf9badb`.

The [source manifest](core-source-manifest.json) records normalized file hashes,
source hashes for implementation files, explicit public adaptations and the
public baseline for independently maintained files. CRLF is normalized to LF;
other bytes are preserved. The recorded source revision is provenance, not a
promise that the corresponding development commit is available here.

Public endpoint preparation uses Windows-native support. Extra AudioSrv format
enumeration is omitted from source, build and public menu controls. The native
capture controller retains its audiodg path. These adaptations do not change
the shared capture, recovery, rendering or output algorithms.

The earlier unified import was public `5a3cb85`; public `cf9badb` maintains the
English documentation independently. This refresh preserves that structure and
updates it against the cleaned candidate rather than copying internal reports.

See [development](development.md), [CI scope](ci.md), [release preparation](release.md)
and [license scope](../LICENSING.md). The candidate's verification status must
be recorded separately from historical performance observations.
