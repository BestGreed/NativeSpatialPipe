using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

// Research-only inspection of objects owned by this fixture process. Never a system capture.
// This layout is pinned to one exact AudioSes image and verified getter RVAs.
static class PrivateBlockProbe {
 const string ExpectedHash="74745DD6226403DB0AEE8F04F2B65A756D4B910583A570AFA385E9741B3F3AE9";
 static bool verified;
 [StructLayout(LayoutKind.Explicit,Size=48)] struct MemoryInfo {
  [FieldOffset(0)] public IntPtr start; [FieldOffset(24)] public UIntPtr size;
  [FieldOffset(32)] public uint state; [FieldOffset(36)] public uint protect;
 }
 [DllImport("kernel32.dll")] static extern UIntPtr VirtualQuery(IntPtr address,out MemoryInfo info,UIntPtr length);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool GetModuleHandleEx(uint flags,IntPtr address,out IntPtr module);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern uint GetModuleFileName(IntPtr module,StringBuilder name,int size);
 [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int Getter(IntPtr instance,uint index,out IntPtr data,out uint count);
 static void Readable(IntPtr p,ulong bytes) {
  MemoryInfo m;if(p==IntPtr.Zero || VirtualQuery(p,out m,(UIntPtr)48)==UIntPtr.Zero || m.state!=0x1000 || (m.protect&0x101)!=0 || (m.protect&0xEE)==0)throw new Exception("Private probe: unreadable range");
  ulong offset=unchecked((ulong)(p.ToInt64()-m.start.ToInt64()));if(offset>m.size.ToUInt64() || bytes>m.size.ToUInt64()-offset)throw new Exception("Private probe: range crosses region");
 }
 static IntPtr Pointer(IntPtr p,int offset){var at=IntPtr.Add(p,offset);Readable(at,8);return Marshal.ReadIntPtr(at);}
 public static void VerifyImage() {
  string path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"AudioSes.dll");
  using(var file=File.OpenRead(path))using(var hash=SHA256.Create())if(BitConverter.ToString(hash.ComputeHash(file)).Replace("-","")!=ExpectedHash)throw new Exception("Private probe disabled: AudioSes hash mismatch");
  verified=true;
 }
 static IntPtr Method(IntPtr instance,int slot,long rva) {
  IntPtr address=Pointer(Pointer(instance,0),slot*8),module;
  if(!GetModuleHandleEx(6,address,out module) || address.ToInt64()-module.ToInt64()!=rva)throw new Exception("Private probe: method RVA mismatch");
  var name=new StringBuilder(32768);GetModuleFileName(module,name,name.Capacity);
  if(!string.Equals(name.ToString(),Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"AudioSes.dll"),StringComparison.OrdinalIgnoreCase))throw new Exception("Private probe: unexpected module");
  return address;
 }
 public static object Snapshot(AudioObject obj,IntPtr publicBuffer,byte[] expected,uint frames,int cycle,int objectId,string phase,uint type,float x,float y,float z,float gain) {
  if(!verified)throw new Exception("Private probe image was not verified");
  IntPtr unknown=Marshal.GetIUnknownForObject(obj),p=IntPtr.Zero;
  try {
   Guid iid=typeof(AudioObject).GUID;Marshal.ThrowExceptionForHR(Marshal.QueryInterface(unknown,ref iid,out p));
   Method(p,3,0x523A0);Method(p,7,0x30680);Method(p,8,0x307C0);
   IntPtr parent=Pointer(p,0x58),block=Pointer(parent,0x6C0);
   Readable(IntPtr.Add(p,0x50),4);uint index=unchecked((uint)Marshal.ReadInt32(p,0x50));
   var samples=(Getter)Marshal.GetDelegateForFunctionPointer(Method(block,4,0x32780),typeof(Getter));
   var properties=(Getter)Marshal.GetDelegateForFunctionPointer(Method(block,5,0x31FE0),typeof(Getter));
   IntPtr pcm,prop;uint capacity,length;
   Marshal.ThrowExceptionForHR(samples(block,index,out pcm,out capacity));
   Marshal.ThrowExceptionForHR(properties(block,index,out prop,out length));
   if(length!=32 || capacity<frames || capacity>262144)throw new Exception("Private probe: unexpected returned lengths");
   Readable(prop,length);Readable(pcm,(ulong)expected.Length);
   byte[] raw=new byte[length],copy=new byte[expected.Length];Marshal.Copy(prop,raw,0,raw.Length);Marshal.Copy(pcm,copy,0,copy.Length);
   uint rawType=BitConverter.ToUInt32(raw,8);float px=BitConverter.ToSingle(raw,16),py=BitConverter.ToSingle(raw,20),pz=BitConverter.ToSingle(raw,24),pg=BitConverter.ToSingle(raw,28);
   bool pcmEqual=copy.SequenceEqual(expected),positionEqual=type!=1 || (px==x && py==y && pz==z);
   return new {scope="own-process-private-client-block-only",cycle=cycle,objectId=objectId,phase=phase,privateSlotIndex=index,propertyBytes=length,bufferCapacityFrames=capacity,publicBufferPointerMatches=pcm==publicBuffer,pcmBitExact=pcmEqual,expectedGain=gain,expectedType=type,expectedX=type==1?(float?)x:null,expectedY=type==1?(float?)y:null,expectedZ=type==1?(float?)z:null,rawPropertiesHex=BitConverter.ToString(raw).Replace("-",""),candidateIdentity=BitConverter.ToUInt32(raw,0),frameCount=BitConverter.ToUInt32(raw,4),type=rawType,flags=BitConverter.ToUInt32(raw,12),x=px,y=py,z=pz,gain=pg,fieldsMatch=pcmEqual && pcm==publicBuffer && rawType==type && pg==gain && positionEqual && BitConverter.ToUInt32(raw,4)==frames};
  } finally {if(p!=IntPtr.Zero)Marshal.Release(p);Marshal.Release(unknown);}
 }
}
