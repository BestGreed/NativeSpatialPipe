# audiodg build profiles (multi-build hook support)

The native audiodg capture hook used to pin one build of
`%SystemRoot%\System32\audiodg.exe` at compile time: the target function RVA,
the two capture vtables and the 16-byte prologue were constants in the hook
DLL and controller. A Windows servicing update changed the whole-file SHA-256
and the layout, so the hook stopped working and the fix had to be compiled
again.

This subsystem replaces that with a **runtime build-profile table** shared by
every consumer:

| Consumer | Role |
|---|---|
| `contracts/audiodg-builds.json` | single source of truth, one record per verified audiodg build |
| `tools/Generate-AudiodgBuilds.py` | generates the C header, Zig module and Python module below |
| `tools/endpoint-probe/Audiodg-Builds.h`, `modules/native-capture/Audiodg-Builds.h` | table used by the native controller |
| `tools/zig-scheduler/audiodg_builds.zig`, `modules/zig-scheduler/audiodg_builds.zig` | table used by the Zig scheduler preflight |
| `tools/backend/audiodg_builds.py` | table used by Python preflight/launcher tools |

## Runtime flow

1. The scheduler (`nsp-scheduler preflight` / host setup), the native
   controller (`nsp_hook_control.exe`) and Python (`hook_state.preflight`,
   `Sync-Hook.py`) hash the full `audiodg.exe` file in `System32`.
2. The SHA-256 selects a profile from the generated table. No match is a
   hard fail: the message contains the actual digest and the full supported
   list, and no hook code runs.
3. The selected `target_rva`, `target_size`, `vt1_rva`, `vt2_rva` and
   16-byte `prologue` are written into `HookConfig` (fields appended to the
   existing struct) and passed to the hook DLL. The Legacy hook DLL no longer
   embeds any audiodg RVA/vtable constant; it checks the live prologue from
   the config before patching.
4. The controller re-reads the file digest itself and rejects a stale or
   mismatched `HookConfig` (SHA-256, RVA, size, vtables and prologue must all
   match the selected profile). This is the second, independent gate.
5. `baseline` verification reads the live prologue at the table RVA.

The fixture layout (`nsp_fixture.dll`) and the separately compiled LNL lab
profile (`-DNSP_AUDIODG_LNL_20261102`, public PDB verified RVA 0x31930 /
primary vtable 0x9f4f8) keep their existing behaviour: the fixture path still
uses `GetProcAddress(image, "boundary")` and fixed fixture offsets, and the
LNL hook DLL keeps its compiled constants and single-SHA gate. LNL is accepted
by the scheduler outside the runtime table but never receives table values.

## Table records

Each record in `contracts/audiodg-builds.json`:

| Field | Meaning |
|---|---|
| `id` | stable profile id, e.g. `win-26100-10068` |
| `label` | human readable build label (Windows version/servicing context) |
| `sha256` | full-file SHA-256 of the exact `audiodg.exe` |
| `targetRva`, `targetSize` | hooked function RVA and `.pdata` function size |
| `prologue` | first 16 function bytes checked before patching |
| `vt1Rva`, `vt2Rva` | primary and secondary capture vtable RVAs |
| `functionSha256` | function fingerprint recorded by the offline validator; it distinguishes builds that share RVAs and is **not** checked at runtime |

## Supported builds

| id | audiodg file version | target | vt1 / vt2 |
|---|---|---|---|
| `win-26100-9444` | 10.0.26100.9444 | 0x31930 | 0x9f4f8 / 0xa2d58 |
| `win-26100-9974` | 10.0.26100.9949 (OS build 26200.9974) | 0x32c40 | 0x9e4e8 / 0xa1be0 |
| `win-26100-10068` | 10.0.26100.10068 (25H2 2026-11-20 servicing build) | 0x32c40 | 0x9e4e8 / 0xa1c20 |

The 9974 and 10068 builds share the target RVA and primary vtable, but their
file layouts differ: the second vtable moved by 0x40. The 9974 value in this
table (`0xa1be0`) was confirmed by disassembling the object constructor that
installs the same interface slots in both builds:

```
9974 build   RVA 0x88981: leaq ... -> 0x1400a1be0 ; movq %rax,0x28(%rsi)
10068 build  RVA 0x88981: leaq ... -> 0x1400a1c20 ; movq %rax,0x28(%rsi)
```

The 9444 record uses the historical verified layout, and the 10068 record
keeps the layout validated by the previous retarget (target RVA 0x32c40 /
vt1 0x9e4e8 / vt2 0xa1c20). `baseline` proves the target RVA/prologue on a
live target; the second vtable is only exercised by a real capture session.

## Update policy

When a Windows servicing update changes `audiodg.exe`:

1. Verify the new build with the offline validators (unwind function hash /
   cross-build fingerprint) and record its `.pdata` RVA, size, prologue and
   both capture vtables.
2. Append a record to `contracts/audiodg-builds.json`. Keep older records
   while those Windows builds remain in service.
3. Run `python tools/Generate-AudiodgBuilds.py` and commit the regenerated
   files (`--check` verifies they are current).
4. Rebuild `nsp_hook_control.exe`, the hook DLL and the scheduler together.
   `HookConfig` grew in this change, so an already installed hook DLL from an
   older build fails the existing struct-size/build checks and must be
   stopped and replaced; do not mix builds.

## Verification

```powershell
python -m unittest discover -s tools/backend -p 'test_audiodg_builds.py' -v
python tools/Generate-AudiodgBuilds.py --check
./tools/endpoint-probe/Test-Audiodg-Builds.ps1
```

The native controller also exposes a read-only lookup that never opens a
process or loads a DLL:

```powershell
build/native-hook-managed-v2/nsp_hook_control.exe --audiodg-profile C:\\Windows\\System32\\audiodg.exe
```

It prints the selected profile as JSON, or exits non-zero with the actual
SHA-256 and the supported list.

## Known limits

* Runtime checks use the whole-file SHA-256, the target prologue and the
  controller's config consistency gate. The per-record `functionSha256` is
  an offline evidence field, not a runtime hash.
* The 9974 and 10068 entries were not exercised by a live capture session in
  this change (no privileged deployment was performed); they are supported by
  static PE/disassembly evidence and the shared table tests. A real session
  on each build is still the final acceptance step.
* The LNL lab build remains a separately compiled profile outside this table.
