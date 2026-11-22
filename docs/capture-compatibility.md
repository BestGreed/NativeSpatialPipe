# Native capture compatibility

Windows updates can move capture entry points, vtables and object fields.
Current capture uses an explicit layout contract instead of admitting a target
only because its binary matches a compiled-in digest.

The shipped table is
`modules/native-capture/profiles/capture-profiles.json`; the product build
copies it to `build/nsp-product/profiles`. The selector under
`tools/capture-profile` identifies the target image, resolves table members and
can use instruction fingerprints or public PDB symbols when applicable.
Cached layout, status and refresh logs live in the NSP user configuration home.

The Hook validates the layout structure and entry-point prologue, then locates
an allowed block/vtable before copying audio. A known image identity and an
available Windows spatial mode do not independently prove that all object
shapes or playback paths work on that Windows version.

FSA provides layout status, explicit update and offline import actions. Import
matching profile/PDB material for the target audio engine; do not edit addresses
by guessing. Errors such as missing/invalid profile, anchor mismatch and block
not found describe distinct failures. Preserve their status and counters when
reporting a compatibility problem.

The table includes multiple Windows builds and captures symbol/anchor evidence.
It is not a claim that every listed build has passed real-device audio
acceptance. Live primary-path evidence and unobserved secondary object paths
remain separate. Test the selected build, source application and output backend
for the support level being released.
