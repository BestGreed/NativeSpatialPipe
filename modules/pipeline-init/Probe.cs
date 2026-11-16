using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Web.Script.Serialization;

// x64 COM ABI checked against Microsoft's SpatialAudioClient.h.
// No registration, driver install, provider switch or game instrumentation.
[StructLayout(LayoutKind.Sequential)] struct Key { public Guid id; public uint pid; public Key(string g,uint p){id=new Guid(g);pid=p;} }
[StructLayout(LayoutKind.Explicit, Size=24)] struct PV {
 [FieldOffset(0)] public ushort vt;
 [FieldOffset(8)] public IntPtr text;
 [FieldOffset(8)] public uint bytes;
 [FieldOffset(16)] public IntPtr data;
}
[StructLayout(LayoutKind.Sequential)] struct Params {
 public IntPtr format; public uint mask,min,max; public int category; public IntPtr ev,notify;
}
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Enumerator {
 [PreserveSig] int EnumAudioEndpoints(int flow,uint states,out Collection devices);
 [PreserveSig] int GetDefaultAudioEndpoint(int flow,int role,out Device device);
}
[ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Collection {
 [PreserveSig] int GetCount(out uint count); [PreserveSig] int Item(uint i,out Device device);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Device {
 [PreserveSig] int Activate(ref Guid iid,uint context,IntPtr args,[MarshalAs(UnmanagedType.IUnknown)] out object obj);
 [PreserveSig] int OpenPropertyStore(uint mode,out Properties props);
 [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
 [PreserveSig] int GetState(out uint state);
}
[ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Properties {
 [PreserveSig] int GetCount(out uint count); [PreserveSig] int GetAt(uint i,out Key key);
 [PreserveSig] int GetValue(ref Key key,out PV value);
}
[ComImport, Guid("BBF8E066-AAAA-49BE-9A4D-FD2A858EA27F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Spatial {
 [PreserveSig] int GetStaticObjectPosition(uint type,out float x,out float y,out float z);
 [PreserveSig] int GetNativeStaticObjectTypeMask(out uint mask);
 [PreserveSig] int GetMaxDynamicObjectCount(out uint count);
 [PreserveSig] int GetSupportedAudioObjectFormatEnumerator(out Formats formats);
 [PreserveSig] int GetMaxFrameCount(IntPtr format,out uint frames);
 [PreserveSig] int IsAudioObjectFormatSupported(IntPtr format);
 [PreserveSig] int IsSpatialAudioStreamAvailable(ref Guid iid,IntPtr args);
 [PreserveSig] int ActivateSpatialAudioStream(ref PV args,ref Guid iid,[MarshalAs(UnmanagedType.IUnknown)] out object stream);
}
[ComImport, Guid("DCDAA858-895A-4A22-A5EB-67BDA506096D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Formats {
 [PreserveSig] int GetCount(out uint count); [PreserveSig] int GetFormat(uint i,out IntPtr format);
}
[ComImport, Guid("BAB5F473-B423-477B-85F5-B5A332A04153"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface Stream {
 [PreserveSig] int GetAvailableDynamicObjectCount(out uint n);
 [PreserveSig] int GetService(ref Guid iid,[MarshalAs(UnmanagedType.IUnknown)] out object service);
 [PreserveSig] int Start(); [PreserveSig] int Stop(); [PreserveSig] int Reset();
 [PreserveSig] int BeginUpdatingAudioObjects(out uint n,out uint frames);
 [PreserveSig] int EndUpdatingAudioObjects();
 [PreserveSig] int ActivateSpatialAudioObject(uint type,out AudioObject obj);
}
[ComImport, Guid("DDE28967-521B-46E5-8F00-BD6F2BC8AB1D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface AudioObject {
 [PreserveSig] int GetBuffer(out IntPtr p,out uint bytes);
 [PreserveSig] int SetEndOfStream(uint frames);
 [PreserveSig] int IsActive([MarshalAs(UnmanagedType.Bool)] out bool active);
 [PreserveSig] int GetAudioObjectType(out uint type);
 [PreserveSig] int SetPosition(float x,float y,float z);
 [PreserveSig] int SetVolume(float volume);
}
class Probe {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern bool GetModuleHandleEx(uint flags, IntPtr address, out IntPtr module);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern uint GetModuleFileName(IntPtr module, StringBuilder name, int size);
 // Read-only inspection of our own COM interfaces. Never patch a vtable or inspect another process.
 static object Boundary(object instance, Type type, string[] methods) {
  IntPtr unknown=IntPtr.Zero, pointer=IntPtr.Zero; var rows=new List<object>();
  try {
   unknown=Marshal.GetIUnknownForObject(instance); Guid iid=type.GUID;
   Check(Marshal.QueryInterface(unknown,ref iid,out pointer)); IntPtr table=Marshal.ReadIntPtr(pointer);
   for(int i=0;i<methods.Length;i++) {
    IntPtr address=Marshal.ReadIntPtr(table,(i+3)*IntPtr.Size), module; string path=null,version=null,rva=null;
    if(GetModuleHandleEx(6,address,out module)) {
     var name=new StringBuilder(32768); if(GetModuleFileName(module,name,name.Capacity)>0)path=name.ToString();
     rva="0x"+(address.ToInt64()-module.ToInt64()).ToString("X");
     if(path!=null)version=FileVersionInfo.GetVersionInfo(path).FileVersion;
    }
    rows.Add(new {method=methods[i],slot=i+3,module=path,fileVersion=version,rva=rva});
   }
   return new {iid=type.GUID,scope="own-process-client-interface-only",methods=rows};
  } finally {if(pointer!=IntPtr.Zero)Marshal.Release(pointer);if(unknown!=IntPtr.Zero)Marshal.Release(unknown);}
 }
 static object LoadedModules() {
  var modules=new List<object>();
  foreach(ProcessModule m in Process.GetCurrentProcess().Modules)modules.Add(new {name=m.ModuleName,path=m.FileName,version=m.FileVersionInfo.FileVersion});
  return modules;
 }
 [DllImport("ole32.dll")] static extern int PropVariantClear(ref PV p);
 static string Hex(int hr){return "0x"+unchecked((uint)hr).ToString("X8");}
 static void Check(int hr){if(hr<0)Marshal.ThrowExceptionForHR(hr);}
 static void Release(object o){if(o!=null && Marshal.IsComObject(o))Marshal.ReleaseComObject(o);}
 static Dictionary<string,object> Exercise(Spatial client, bool dynamic, string fixtureDirectory, bool privateBlocks=false, bool observerFixture=false, bool lifecycle=false, bool tail=false, bool keepalive=false) {
  var r=new Dictionary<string,object>(); Formats formats=null; object raw=null; Stream stream=null;
  AudioObject a=null,b=null,left=null,right=null; IntPtr memory=IntPtr.Zero; bool started=false;
  bool fixture=fixtureDirectory!=null; var events=new List<object>();
  var blockSnapshots=new List<object>();
  var recordings=new[]{new MemoryStream(),new MemoryStream(),new MemoryStream(),new MemoryStream()};
  using(var ev=new EventWaitHandle(false,EventResetMode.AutoReset)) {
   try {
    if(privateBlocks)PrivateBlockProbe.VerifyImage();
    Check(client.GetSupportedAudioObjectFormatEnumerator(out formats));
    IntPtr format; Check(formats.GetFormat(0,out format));
    int sampleRate=Marshal.ReadInt32(format,4);
    if(fixture && (Marshal.ReadInt16(format,0)!=3 || Marshal.ReadInt16(format,2)!=1 || Marshal.ReadInt16(format,14)!=32))throw new Exception("Fixture requires advertised mono float32");
    r["sampleRate"]=sampleRate;r["sourceTruthOnly"]=fixture;
    var p=new Params {format=format,mask=fixture?6u:(dynamic?0u:6u),min=dynamic?2u:0u,max=dynamic?2u:0u,category=6,ev=ev.SafeWaitHandle.DangerousGetHandle()};
    memory=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Params))); Marshal.StructureToPtr(p,memory,false);
    var pv=new PV {vt=65,bytes=(uint)Marshal.SizeOf(typeof(Params)),data=memory};
    Guid iid=typeof(Stream).GUID; int hr=client.ActivateSpatialAudioStream(ref pv,ref iid,out raw);
    r["activateHr"]=Hex(hr); Check(hr); stream=(Stream)raw;
    r["exerciseStage"]="ActivateObjects";
    Check(stream.ActivateSpatialAudioObject(dynamic?1u:2u,out a)); Check(stream.ActivateSpatialAudioObject(dynamic?1u:4u,out b));
    if(fixture){Check(stream.ActivateSpatialAudioObject(2,out left));Check(stream.ActivateSpatialAudioObject(4,out right));}
    r["clientBoundary"]=Boundary(client,typeof(Spatial),new[]{"GetStaticObjectPosition","GetNativeStaticObjectTypeMask","GetMaxDynamicObjectCount","GetSupportedAudioObjectFormatEnumerator","GetMaxFrameCount","IsAudioObjectFormatSupported","IsSpatialAudioStreamAvailable","ActivateSpatialAudioStream"});
    r["streamBoundary"]=Boundary(stream,typeof(Stream),new[]{"GetAvailableDynamicObjectCount","GetService","Start","Stop","Reset","BeginUpdatingAudioObjects","EndUpdatingAudioObjects","ActivateSpatialAudioObject"});
    r["objectBoundary"]=Boundary(a,typeof(AudioObject),new[]{"GetBuffer","SetEndOfStream","IsActive","GetAudioObjectType","SetPosition","SetVolume"});
    r["exerciseStage"]="Start";int startHr=stream.Start();r["startHr"]=Hex(startHr);Check(startHr); started=true; ulong framesTotal=0; int cycles=0;
    r["ownProcessModulesAfterStart"]=LoadedModules();
    int cycleLimit=keepalive?2000:(observerFixture?500:(fixture?100:20));
    r["observerFixture"]=observerFixture;r["cycleLimit"]=cycleLimit;
    if(observerFixture)r["positionPattern"]="stepped-hold-v1";
    r["lifecycleFixture"]=lifecycle;
    r["tailFixture"]=tail;
    r["exerciseStage"]="UpdateObjects";
    for(int i=0;i<cycleLimit;i++) {
     if(!ev.WaitOne(2000))throw new Exception("Spatial event timeout");
     uint available,frames; Check(stream.BeginUpdatingAudioObjects(out available,out frames));
     try {
      if(lifecycle && i==250){
       Release(a);a=null;Release(b);b=null;Release(left);left=null;Release(right);right=null;
       Check(stream.ActivateSpatialAudioObject(1,out a));Check(stream.ActivateSpatialAudioObject(1,out b));
       Check(stream.ActivateSpatialAudioObject(2,out left));Check(stream.ActivateSpatialAudioObject(4,out right));
      }
      AudioObject[] objects=fixture?new[]{a,b,left,right}:new[]{a,b};
      for(int oi=0;oi<objects.Length;oi++) {
       var obj=objects[oi];
       IntPtr buffer; uint bytes; Check(obj.GetBuffer(out buffer,out bytes));
       if(bytes>16777216)throw new Exception("Unexpected buffer size");
       byte[] payload=new byte[bytes];
       if(fixture){
        if(bytes!=frames*4)throw new Exception("Unexpected mono float32 buffer length");
        float[] samples=new float[frames];
        for(int n=0;n<samples.Length;n++)samples[n]=(float)(0.05*Math.Sin(2*Math.PI*(440+oi*110)*(framesTotal+(ulong)n)/sampleRate));
        if(observerFixture && samples.Length>0)samples[0]=(float)((i*4+oi+1)*0.000001);
        Buffer.BlockCopy(samples,0,payload,0,payload.Length);
       }
       Marshal.Copy(payload,0,buffer,(int)bytes);
       Check(obj.SetVolume(0));
       float x=oi==0 ? (i<50?-1f:1f) : 1f;
       bool positionUpdated=dynamic && oi<2;
       if(observerFixture && oi<2){
        int period=oi==0?50:75;
        x=oi==0?((i/period)%2==0?-1f:1f):((i/period)%2==0?0.25f:-0.75f);
        positionUpdated=i%period==0 || (lifecycle && i==250);
       }
       if(positionUpdated)Check(obj.SetPosition(x,0,-1));
       if(privateBlocks && (i==0 || i==49 || i==50 || i==99)) {
        uint objectType=oi<2?1u:(oi==2?2u:4u);
        float calibrationGain=(oi+1)*0.125f;
        try {
         Check(obj.SetVolume(calibrationGain));
         if(oi<2)Check(obj.SetPosition(x*0.5f,0.125f,-0.75f));
         blockSnapshots.Add(PrivateBlockProbe.Snapshot(obj,buffer,payload,frames,i,oi,"transient-calibration-not-submitted",objectType,x*0.5f,0.125f,-0.75f,calibrationGain));
        } finally {
         Check(obj.SetVolume(0));
         if(oi<2)Check(obj.SetPosition(x,0,-1));
        }
        blockSnapshots.Add(PrivateBlockProbe.Snapshot(obj,buffer,payload,frames,i,oi,"restored-before-submit",objectType,x,0,-1,0));
       }
       if(fixture){
        byte[] copied=new byte[bytes];Marshal.Copy(buffer,copied,0,(int)bytes);recordings[oi].Write(copied,0,copied.Length);
        bool ending=i==cycleLimit-1 || (lifecycle && i==249);
        uint eosFrames=tail && ending?new uint[]{1,120,239,479}[oi]:frames;
        events.Add(new {cycle=i,objectId=oi,generation=lifecycle && i>=250?1:0,type=oi<2?"dynamic":(oi==2?"front-left":"front-right"),sampleStart=framesTotal,frames=frames,gain=0,x=oi<2?(float?)x:null,y=oi<2?(float?)0:null,z=oi<2?(float?)-1:null,positionUpdated=positionUpdated,create=i==0 || (lifecycle && i==250),eos=ending,eosValidFrames=ending?(uint?)eosFrames:null});
        if(ending)Check(obj.SetEndOfStream(eosFrames));
       }
      }
     } finally {Check(stream.EndUpdatingAudioObjects());}
     framesTotal+=frames; cycles++;
    }
    if(observerFixture){int drainEvents=0;for(int d=0;d<2;d++){if(!ev.WaitOne(1000))break;drainEvents++;}r["postSubmitDrainEvents"]=drainEvents;}
    r["cycles"]=cycles; r["frames"]=framesTotal; r["result"]=fixture?"muted_fixture_four_objects_submitted":(dynamic?"silent_two_dynamic_objects_submitted":"silent_FL_FR_submitted");
   } catch(Exception ex){r["error"]=ex.Message;r["errorHr"]=Hex(ex.HResult);}
   finally {
    if(started && stream!=null)r["stopHr"]=Hex(stream.Stop());
    Release(a);Release(b);Release(left);Release(right);Release(raw);Release(formats);if(memory!=IntPtr.Zero)Marshal.FreeHGlobal(memory);
    if(fixture){Directory.CreateDirectory(fixtureDirectory);for(int oi=0;oi<4;oi++)File.WriteAllBytes(Path.Combine(fixtureDirectory,"object-"+oi+".f32"),recordings[oi].ToArray());File.WriteAllText(Path.Combine(fixtureDirectory,"source-events.json"),new JavaScriptSerializer().Serialize(events));}
    if(privateBlocks)File.WriteAllText(Path.Combine(fixtureDirectory,"private-block-snapshots.json"),new JavaScriptSerializer().Serialize(blockSnapshots));
    foreach(var recording in recordings)recording.Dispose();
   }
  }
  return r;
 }
 [MTAThread] static int Main(string[] args) {
  var report=new Dictionary<string,object>(); var rows=new List<object>(); Enumerator e=null; Collection devices=null;
  report["utc"]=DateTime.UtcNow.ToString("o"); report["process64"]=Environment.Is64BitProcess;
  report["mode"]=args.Length>0 ? "silent-exercise" : "query-only"; report["endpoints"]=rows;
  try {
   if(!Environment.Is64BitProcess)throw new Exception("This probe requires x64");
   e=(Enumerator)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")));
   Check(e.EnumAudioEndpoints(0,15,out devices)); uint count;Check(devices.GetCount(out count));
   for(uint i=0;i<count;i++) {
    Device device=null; Properties props=null; object obj=null; Formats formats=null;
    var row=new Dictionary<string,object>(); rows.Add(row); row["index"]=i;
    try {
     Check(devices.Item(i,out device)); string id;Check(device.GetId(out id)); row["id"]=id;
     uint state;Check(device.GetState(out state));row["state"]=state;
     Check(device.OpenPropertyStore(0,out props)); var key=new Key("a45c254e-df1c-4efd-8020-67d146a850e0",14); PV value;
     int ph=props.GetValue(ref key,out value); try{if(ph>=0 && value.vt==31)row["name"]=Marshal.PtrToStringUni(value.text);}finally{PropVariantClear(ref value);}
     if(state!=1){row["skipped"]="not-active";continue;}
     Guid iid=typeof(Spatial).GUID;int hr=device.Activate(ref iid,1,IntPtr.Zero,out obj); row["spatialActivateHr"]=Hex(hr);
     if(hr<0)continue; Spatial s=(Spatial)obj;uint n,mask;
     hr=s.GetMaxDynamicObjectCount(out n);row["dynamicCountHr"]=Hex(hr);if(hr>=0)row["maxDynamicObjects"]=n;
     int mh=s.GetNativeStaticObjectTypeMask(out mask);row["staticMaskHr"]=Hex(mh);if(mh>=0)row["staticMask"]="0x"+mask.ToString("X");
     int fh=s.GetSupportedAudioObjectFormatEnumerator(out formats);row["formatsHr"]=Hex(fh);
     if(fh>=0){uint fc;Check(formats.GetCount(out fc));row["formatCount"]=fc;
      if(fc>0){IntPtr fp;Check(formats.GetFormat(0,out fp));row["firstFormatTag"]=(ushort)Marshal.ReadInt16(fp,0);row["firstFormatChannels"]=(ushort)Marshal.ReadInt16(fp,2);row["firstFormatSampleRate"]=Marshal.ReadInt32(fp,4);row["firstFormatBits"]=(ushort)Marshal.ReadInt16(fp,14);uint frames;int fr=s.GetMaxFrameCount(fp,out frames);row["maxFramesHr"]=Hex(fr);if(fr>=0)row["maxFrames"]=frames;}}
     Guid si=typeof(Stream).GUID;row["basicStreamAvailableHr"]=Hex(s.IsSpatialAudioStreamAvailable(ref si,IntPtr.Zero));
     if(args.Length==2 && (args[0]=="--exercise" || args[0]=="--exercise-static") && id==args[1])row["exercise"]=Exercise(s,args[0]=="--exercise",null);
     if(args.Length==2 && args[0]=="--keepalive-static" && id==args[1])row["exercise"]=Exercise(s,false,null,false,false,false,false,true);
     if(args.Length==3 && args[0]=="--fixture" && id==args[1])row["exercise"]=Exercise(s,true,args[2]);
     if(args.Length==3 && args[0]=="--fixture-private-block" && id==args[1])row["exercise"]=Exercise(s,true,args[2],true);
     if(args.Length==3 && args[0]=="--fixture-observer" && id==args[1])row["exercise"]=Exercise(s,true,args[2],false,true);
     if(args.Length==3 && args[0]=="--fixture-lifecycle" && id==args[1])row["exercise"]=Exercise(s,true,args[2],false,true,true);
     if(args.Length==3 && args[0]=="--fixture-tail" && id==args[1])row["exercise"]=Exercise(s,true,args[2],false,true,true,true);
    }catch(Exception ex){row["error"]=ex.Message;row["errorHr"]=Hex(ex.HResult);}
    finally{Release(formats);Release(obj);Release(props);Release(device);}
   }
  }catch(Exception ex){report["fatal"]=ex.Message;report["fatalHr"]=Hex(ex.HResult);}
  finally{Release(devices);Release(e);}
  Console.WriteLine(new JavaScriptSerializer().Serialize(report));return report.ContainsKey("fatal")?1:0;
 }
}
