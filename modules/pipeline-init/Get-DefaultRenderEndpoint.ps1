$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IEndpointEnumerator {
 [PreserveSig] int EnumAudioEndpoints(int flow,uint states,out IntPtr devices);
 [PreserveSig] int GetDefaultAudioEndpoint(int flow,int role,out IEndpointDevice device);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IEndpointDevice {
 [PreserveSig] int Activate(ref Guid iid,uint context,IntPtr args,out IntPtr obj);
 [PreserveSig] int OpenPropertyStore(uint mode,out IntPtr props);
 [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
 [PreserveSig] int GetState(out uint state);
}
public static class DefaultRenderEndpoint {
 public static string Get(int role) {
  var e=(IEndpointEnumerator)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")));
  IEndpointDevice d;
  int hr=e.GetDefaultAudioEndpoint(0,role,out d);
  if(hr<0)Marshal.ThrowExceptionForHR(hr);
  string id;hr=d.GetId(out id);
  if(hr<0)Marshal.ThrowExceptionForHR(hr);
  return id;
 }
}
'@
[pscustomobject]@{utc=[DateTime]::UtcNow.ToString('o');console=[DefaultRenderEndpoint]::Get(0);multimedia=[DefaultRenderEndpoint]::Get(1);communications=[DefaultRenderEndpoint]::Get(2)}|ConvertTo-Json -Compress
