# Native Spatial Pipe product build

The product build stages the FSA session manager, Zig scheduler, web frontend,
native capture and receiver, and Croksor runtime. Croksor links the patched ROAR
1.1 renderer and Inferno AoIP. The build also produces LIRA and ASIO backends.

Run from the source root with an explicit Windows x64 Python executable:

```powershell
./tools/Build-Product.ps1 -Python (Get-Command python.exe).Source -MediaFoundation -PcmCapture -BrowserIdentity -Wasapi -WasapiUpmix
```

The build expects the pinned MinHook and ROAR checkouts, LLVM-MinGW, Zig,
the locked Rust toolchain and Cargo dependencies, and the OpenVINO CPU SDK
for the upmix option. See the build scripts for the expected dependency paths.
The scheduler, receiver, Croksor, and web assets are staged under
`build/nsp-product`. FSA is staged under `build/fsa-integration`.

Windows spatial audio is a platform prerequisite. The native object capture
controller also requires a supported Windows audio-engine build. The presence
of a spatial audio setting alone does not establish capture compatibility.

## Native spatial capture compatibility

The native capture controller requires an exact SHA-256 match for the
`audiodg.exe` profile compiled into the controller. Each profile assumes
a specific Windows audio engine layout.

| Profile | Capture layout |
| --- | --- |
| `Legacy` | Original validated build with primary and secondary vtables. |
| `Lnl20261102` | Validated LNL build with the primary vtable only. |

The build script defaults to `Auto`. It selects `Lnl20261102` when the local
system binary matches that profile's hash and selects `Legacy` otherwise.
At runtime, the controller independently checks the target binary against the
selected profile and rejects mismatches. A successful build therefore does
not establish that native capture will run on the target machine.

## Optional Node.js runtime

Browser identity uses a user-installed Node.js runtime. Its deployment script
resolves node.exe from PATH by default. Use -NodeExe to specify an executable
explicitly. The script records the resolved absolute path in playback settings.
The Windows product build and the default audio session require no Node.js.

See the [documentation index](docs/README.md) for dependency paths, runtime details and license scope.
