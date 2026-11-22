// Edge-version-specific read-only object and kernel-event identity core.
#pragma once
#include <winternl.h>
#include <psapi.h>
#include <cmath>
#include <sstream>
#include "../pcm-capture/queue_probe.h"
#include "../pcm-capture/pcm_ring.h"
#include <algorithm>
#include <vector>
using QueryObject=NTSTATUS(NTAPI*)(HANDLE,ULONG,PVOID,ULONG,PULONG);
using CompareHandles=BOOL(WINAPI*)(HANDLE,HANDLE);
static double nowms(){return pcm_qpc_ns()/1000000.0;}
static double cpums(){FILETIME c,e,k,u;GetProcessTimes(GetCurrentProcess(),&c,&e,&k,&u);ULARGE_INTEGER a,b;a.LowPart=k.dwLowDateTime;a.HighPart=k.dwHighDateTime;b.LowPart=u.dwLowDateTime;b.HighPart=u.dwHighDateTime;return (a.QuadPart+b.QuadPart)/10000.0;}
static PROCESS_MEMORY_COUNTERS_EX mem(){PROCESS_MEMORY_COUNTERS_EX m{};m.cb=sizeof(m);GetProcessMemoryInfo(GetCurrentProcess(),(PROCESS_MEMORY_COUNTERS*)&m,sizeof(m));return m;}
static HANDLE open_target(DWORD pid,DWORD access){
 HANDLE token=nullptr;TOKEN_PRIVILEGES old{},req{};DWORD size=sizeof(old);bool changed=false;
 if(OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY|TOKEN_ADJUST_PRIVILEGES,&token)){
  req.PrivilegeCount=1;req.Privileges[0].Attributes=SE_PRIVILEGE_ENABLED;
  LookupPrivilegeValueW(nullptr,SE_DEBUG_NAME,&req.Privileges[0].Luid);SetLastError(0);
  changed=AdjustTokenPrivileges(token,FALSE,&req,sizeof(old),&old,&size)&&GetLastError()==0;
 }
 HANDLE p=OpenProcess(access,FALSE,pid);
 if(changed)AdjustTokenPrivileges(token,FALSE,&old,0,nullptr,nullptr);if(token)CloseHandle(token);return p;
}
struct Token{uint64_t a=0,b=0;std::string text;uintptr_t address=0;};
struct Link{Token physical,logical;uintptr_t source=0,w1=0,w2=0,clientPtr=0;QueueInfo queue;uintptr_t clientEvent=0,hostEvent=0;HANDLE clientCopy=nullptr,hostCopy=nullptr;};
struct EdgeVtables {
 uintptr_t wasapiStream, converter, wrapper1, wrapper2, controller;
};
constexpr EdgeVtables edge156Vtables{0x117ccc30,0x117cca30,0x117ccab0,0x117ccb50,0x117ccaf0};
struct BrowserBindingCore{
 QueueProbe p;HANDLE client=nullptr,hostDup=nullptr;uintptr_t edgeBase=0;std::vector<Link>links;
 EdgeVtables vtables=edge156Vtables;
 uint64_t calls=0,bytes=0,hostScan=0,clientScan=0;CompareHandles compare=nullptr;

 bool read(HANDLE process,uintptr_t at,void*out,size_t n){SIZE_T got=0;calls++;bool ok=ReadProcessMemory(process,(void*)at,out,n,&got)&&got==n;bytes+=got;return ok;}
 template<class T>bool value(HANDLE process,uintptr_t at,T&out){return read(process,at,&out,sizeof(out));}
 void clear(){for(auto&l:links){if(l.clientCopy)CloseHandle(l.clientCopy);if(l.hostCopy)CloseHandle(l.hostCopy);}links.clear();}
 bool init(DWORD host,DWORD source,uintptr_t base,const wchar_t*endpoint){
  p.host=host;p.source=source;p.endpoint=endpoint;edgeBase=base;
  p.process=open_target(host,PROCESS_VM_READ|PROCESS_QUERY_LIMITED_INFORMATION);hostDup=open_target(host,PROCESS_QUERY_INFORMATION|PROCESS_DUP_HANDLE);client=open_target(source,PROCESS_VM_READ|PROCESS_QUERY_INFORMATION|PROCESS_DUP_HANDLE);
  // The module snapshot itself needs debug privilege for audiodg. Handle opens
  // above restore privileges, so do not depend on the scheduler's token state.
  HANDLE token=nullptr;TOKEN_PRIVILEGES old{},req{};DWORD oldSize=sizeof(old);bool changed=false;
  if(OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY|TOKEN_ADJUST_PRIVILEGES,&token)){
   req.PrivilegeCount=1;req.Privileges[0].Attributes=SE_PRIVILEGE_ENABLED;
   if(LookupPrivilegeValueW(nullptr,SE_DEBUG_NAME,&req.Privileges[0].Luid)){SetLastError(0);changed=AdjustTokenPrivileges(token,FALSE,&req,sizeof(old),&old,&oldSize)&&GetLastError()==0;}
  }
  HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,host);MODULEENTRY32W mod{sizeof(mod)};
  if(snap!=INVALID_HANDLE_VALUE){if(Module32FirstW(snap,&mod))p.image=(uintptr_t)mod.modBaseAddr;CloseHandle(snap);}
  if(changed)AdjustTokenPrivileges(token,FALSE,&old,0,nullptr,nullptr);if(token)CloseHandle(token);
  compare=(CompareHandles)GetProcAddress(GetModuleHandleW(L"kernelbase.dll"),"CompareObjectHandles");return p.process&&hostDup&&client&&compare&&p.image&&p.select_pcm_profile();
 }
 bool event(HANDLE process,uintptr_t handle,HANDLE&copy){
  if(!handle||!DuplicateHandle(process,(HANDLE)handle,GetCurrentProcess(),&copy,0,FALSE,0))return false;
  BYTE info[1024]{};ULONG n=0;auto query=(QueryObject)GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"NtQueryObject");
  bool ok=query(copy,2,info,sizeof(info),&n)>=0&&((UNICODE_STRING*)info)->Length==10&&!wcsncmp(((UNICODE_STRING*)info)->Buffer,L"Event",5);
  if(!ok){CloseHandle(copy);copy=nullptr;}return ok;
 }
 std::vector<QueueInfo>queues(){
  IMMDeviceEnumerator*e=nullptr;CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,__uuidof(IMMDeviceEnumerator),(void**)&e);
  auto allowed=e?p.eligible(e):std::set<DWORD>{};if(e)e->Release();std::vector<QueueInfo>out;if(!allowed.count(p.source))return out;
  std::vector<BYTE>buffer(1024*1024);uint64_t scanned=0;
  for(uintptr_t at=0x10000;at<0x00007fffffff0000ULL&&scanned<256*1024*1024ULL;){
   MEMORY_BASIC_INFORMATION r{};if(!VirtualQueryEx(p.process,(void*)at,&r,sizeof(r)))break;uintptr_t next=(uintptr_t)r.BaseAddress+r.RegionSize;if(next<=at)break;at=next;
   if(r.State!=MEM_COMMIT||r.Type!=MEM_PRIVATE||(r.Protect&(PAGE_GUARD|PAGE_NOACCESS))||!(r.Protect&(PAGE_READWRITE|PAGE_WRITECOPY))||r.RegionSize>32*1024*1024)continue;
   for(size_t off=0;off<r.RegionSize;off+=buffer.size()){
    size_t n=(std::min)(buffer.size(),r.RegionSize-off);uintptr_t address=(uintptr_t)r.BaseAddress+off;scanned+=n;hostScan+=n;if(!read(p.process,address,buffer.data(),n))continue;
    for(size_t i=0;i+8<=n;i+=8){uintptr_t v=0;memcpy(&v,buffer.data()+i,8);if(v==p.inputVtable&&address+i>=0x1e0){QueueInfo q;if(p.inspect(address+i-0x1e0,q,allowed)&&std::none_of(out.begin(),out.end(),[&](auto&o){return o.base==q.base;}))out.push_back(q);}}
   }
  }return out;
 }
 bool locate(std::vector<Token>&tokens){
  std::vector<BYTE>buffer(1024*1024);std::vector<unsigned>hits(tokens.size());uint64_t scanned=0;
  for(uintptr_t at=0x10000;at<0x00007fffffff0000ULL&&scanned<512*1024*1024ULL;){
   MEMORY_BASIC_INFORMATION r{};if(!VirtualQueryEx(client,(void*)at,&r,sizeof(r)))break;uintptr_t next=(uintptr_t)r.BaseAddress+r.RegionSize;if(next<=at)break;at=next;
   if(r.State!=MEM_COMMIT||r.Type!=MEM_PRIVATE||(r.Protect&(PAGE_GUARD|PAGE_NOACCESS))||!(r.Protect&(PAGE_READWRITE|PAGE_WRITECOPY))||r.RegionSize>64*1024*1024)continue;
   for(size_t off=0;off<r.RegionSize;off+=buffer.size()-16){size_t n=(std::min)(buffer.size(),r.RegionSize-off);uintptr_t addr=(uintptr_t)r.BaseAddress+off;scanned+=n;clientScan+=n;if(!read(client,addr,buffer.data(),n))continue;
    for(size_t i=0;i+16<=n;i+=8){uint64_t a,b;memcpy(&a,buffer.data()+i,8);memcpy(&b,buffer.data()+i+8,8);
     for(size_t t=0;t<tokens.size();t++)if(a==tokens[t].a&&b==tokens[t].b){tokens[t].address=addr+i;hits[t]++;}}
   }
  }return std::all_of(hits.begin(),hits.end(),[](unsigned n){return n==1;});
 }
 bool vt(uintptr_t address,uintptr_t rva){uintptr_t v=0;return value(client,address,v)&&v==edgeBase+rva;}
 bool graph(Link&l,bool initial){
  uint64_t token[2]{};uintptr_t source=0,w1=0,w2=0,controller=0,ev=0,ac=0;
  if(!read(client,l.physical.address,token,16)||token[0]!=l.physical.a||token[1]!=l.physical.b||!vt(l.physical.address-0x10,vtables.wasapiStream)||!value(client,l.physical.address+0x100,source)||!vt(source,vtables.converter)||!value(client,source+0x10,w1)||!vt(w1,vtables.wrapper1)||!value(client,w1+0x38,w2)||!vt(w2,vtables.wrapper2)||!value(client,w2+0x20,controller)||!value(client,l.physical.address+0x120,ev)||!value(client,l.physical.address+0x110,ac))return false;
  if(!initial&&(source!=l.source||w1!=l.w1||w2!=l.w2||controller+0x18!=l.logical.address||ev!=l.clientEvent||ac!=l.clientPtr))return false;
  if(initial){l.source=source;l.w1=w1;l.w2=w2;l.logical.address=controller+0x18;l.clientEvent=ev;l.clientPtr=ac;}
  return vt(controller,vtables.controller);
 }
 bool validate(){
  if(links.empty())return false;bool ok=true;
  for(auto&l:links){uint64_t t[2]{};uintptr_t vtptr=0,mapping=0,control=0,ev=0;DWORD pid=0;
   if(!graph(l,false)||!read(client,l.logical.address,t,16)||t[0]!=l.logical.a||t[1]!=l.logical.b||!value(p.process,l.queue.base+0x1e0,vtptr)||vtptr!=p.inputVtable||!value(p.process,l.queue.base+0x1cc,pid)||pid!=p.source||!value(p.process,l.queue.base+0x50,mapping)||mapping!=l.queue.mapping||!value(p.process,l.queue.base+0x58,control)||control!=l.queue.control||!value(p.process,l.queue.base+0x1b8,ev)||ev!=l.hostEvent){ok=false;break;}
   HANDLE c=nullptr,h=nullptr;bool same=event(client,l.clientEvent,c)&&event(hostDup,l.hostEvent,h)&&compare(c,l.clientCopy)&&compare(h,l.hostCopy)&&compare(c,h);if(c)CloseHandle(c);if(h)CloseHandle(h);if(!same){ok=false;break;}
  }if(!ok)clear();return ok;
 }
 bool bind(std::vector<Token>tokens,size_t physicalCount,std::string&reason,double&structural,double&live){
  clear();double start=nowms();auto qs=queues();std::vector<uint64_t>before(qs.size());for(size_t i=0;i<qs.size();i++)value(p.process,qs[i].control+0x10,before[i]);
  if(qs.empty()){reason="no eligible queues";return false;}if(!locate(tokens)){reason="token absent or nonunique";return false;}
  std::set<uintptr_t>used;
  for(size_t i=0;i<physicalCount;i++){Link l;l.physical=tokens[i];if(!graph(l,true))continue;
   auto logical=std::find_if(tokens.begin()+physicalCount,tokens.end(),[&](auto&t){return t.address==l.logical.address;});if(logical==tokens.end())continue;l.logical=*logical;
   if(!event(client,l.clientEvent,l.clientCopy)){reason="client event unavailable";clear();return false;}
   unsigned count=0;for(auto&q:qs){uintptr_t ev=0;HANDLE h=nullptr;if(value(p.process,q.base+0x1b8,ev)&&event(hostDup,ev,h)){if(compare(l.clientCopy,h)){count++;l.queue=q;l.hostEvent=ev;if(l.hostCopy)CloseHandle(l.hostCopy);l.hostCopy=h;}else CloseHandle(h);}}
   if(count!=1||!used.insert(l.queue.base).second){if(l.clientCopy)CloseHandle(l.clientCopy);if(l.hostCopy)CloseHandle(l.hostCopy);reason="event/queue ambiguous";clear();return false;}links.push_back(l);
  }
  std::set<uintptr_t> controllers;for(auto&l:links)controllers.insert(l.logical.address);if(links.empty()||controllers.size()!=links.size()||!validate()){reason="snapshot invalidated";clear();return false;}
  structural=nowms()-start;double wait=nowms();bool advancing=false;
  do{advancing=true;for(auto&l:links){auto at=std::find_if(qs.begin(),qs.end(),[&](auto&q){return q.base==l.queue.base;});uint64_t pos=0;if(!value(p.process,l.queue.control+0x10,pos)||pos<=before[at-qs.begin()])advancing=false;}if(!advancing)Sleep(1);}while(!advancing&&nowms()-wait<50);
  live=nowms()-wait;if(!advancing){reason="queue did not advance";clear();return false;}return true;
 }
 ~BrowserBindingCore(){clear();if(client)CloseHandle(client);if(hostDup)CloseHandle(hostDup);}
};
