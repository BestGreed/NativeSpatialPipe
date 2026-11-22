# Windows endpoint preparation

`pipeline.py` provides JSON commands for Windows-native spatial-format queries,
selection and readback, plus explicit EDID planning/support tools. FSA calls
this module through the Zig-controlled session path. Python and PowerShell
remain required management dependencies; they do not process audio blocks.

The public source omits extra AudioSrv format enumeration. `manager.py` is a
compatibility stub reporting unsupported/inactive status and allowing harmless
stop. Requests for extension groups are rejected. Native Windows-supported
formats continue to use the ordinary query/select/readback path.

The product build does not build an enumeration Hook. The public FSA submenu
retains HT Support for explicit provisioning; it does not expose the omitted
extension controls. Normal NSP start does not automatically write EDID.

`inspect` optionally uses `work/OemEndpointProbe.exe`. Build that diagnostic
separately with `modules/pipeline-init/Build-EndpointProbe.ps1` when needed.
It is not a default product artifact. EDID mutation requires an explicit plan
and the relevant permissions; an available format still depends on the endpoint
and Windows licensing.

See the [user guide](../../docs/user-guide.md),
[developer contracts](../../docs/development.md) and
[capture compatibility](../../docs/capture-compatibility.md). Capture-layout
selection for audiodg is separate from the omitted AudioSrv extension.
