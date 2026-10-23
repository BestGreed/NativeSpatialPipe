/* AudioSrv enumeration only. Same pinned MinHook engine as native capture. */
#include "Enum-Hook.h"
#include "MinHook.h"
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>

typedef LONG (*StatusFn)(void*,const wchar_t*,const wchar_t*,void*);
typedef LONG (*SubtypeFn)(void*,const wchar_t*,int,void*);
typedef LONG (*ExtendedFn)(void*,const wchar_t*,unsigned,void*);
static StatusFn status_original;
static SubtypeFn subtype_original;
static ExtendedFn extended_original;
static SRWLOCK control=SRWLOCK_INIT;
static volatile LONG state,wrappers,gate=1,generation;
static ULONGLONG lease;
static HANDLE watcher,stop_event,host;
static wchar_t endpoint[512];
static unsigned mask;
static void *targets[3];
static unsigned char before[3][32],after[3][32];
static DWORD stop_error;
static const unsigned rvas[]={0x57558,0x57248,0x8e304};
static const wchar_t *guids[]={L"1459ac38-3875-49bf-bb59-0fe80f4d395d",L"4c81e564-c8ef-4ad9-9f2c-9ef995533790",L"4444acb0-8dc0-4c2c-a0d8-2c76db470f86",L"adafd3c6-ac4c-404a-836a-9615e9060564"};
static LONG atom(volatile LONG *p){return InterlockedCompareExchange(p,0,0);}
static int contains(const wchar_t *text,const wchar_t *part){
 if(!text)return 0;
 /* Same bounded UTF-16 substring contract as the former agent. */
 for(unsigned i=0;i<512 && text[i];i++){
  unsigned j=0;while(part[j] && i+j<512 && text[i+j] && towlower(text[i+j])==towlower(part[j]))j++;
  if(!part[j])return 1;
 }
 return 0;
}
static int enter(LONG epoch){
 LONG old;
 do{old=atom(&gate);if(old&1)return 0;}while(InterlockedCompareExchange(&gate,old+2,old)!=old);
 if(epoch!=atom(&generation)){InterlockedExchangeAdd(&gate,-2);return 0;}
 return 1;
}
static void put32(void *p,unsigned offset,uint32_t v){memcpy((BYTE*)p+offset,&v,4);}
static void expiry(void *p,unsigned offset){uint64_t v=UINT64_C(0x24c85a5ed1c03fff);memcpy((BYTE*)p+offset,&v,8);}
static LONG status_detour(void *self,const wchar_t *guid,const wchar_t *ep,void *out){
 InterlockedIncrement(&wrappers);LONG epoch=atom(&generation),r;
 __try{
  r=status_original(self,guid,ep,out);DWORD last=GetLastError();
  if(enter(epoch)){
   __try{if(r>=0 && out && contains(ep,endpoint))for(unsigned i=0;i<4;i++)if((mask&(1u<<(i+2))) && contains(guid,guids[i])){put32(out,0,1);put32(out,4,1);expiry(out,8);break;}}
   __finally{InterlockedExchangeAdd(&gate,-2);}
  }
  SetLastError(last);
 }__finally{InterlockedDecrement(&wrappers);}return r;
}
static LONG subtype_detour(void *self,const wchar_t *ep,int index,void *out){
 InterlockedIncrement(&wrappers);LONG epoch=atom(&generation),r;
 __try{
  r=subtype_original(self,ep,index,out);DWORD last=GetLastError();
  if(enter(epoch)){
   __try{if(r>=0 && out && index>=2 && index<=5 && (mask&(1u<<index)) && contains(ep,endpoint)){((BYTE*)out)[0]=1;((BYTE*)out)[1]=1;put32(out,4,0);put32(out,8,0);expiry(out,0x10);}}
   __finally{InterlockedExchangeAdd(&gate,-2);}
  }
  SetLastError(last);
 }__finally{InterlockedDecrement(&wrappers);}return r;
}
static LONG extended_detour(void *self,const wchar_t *ep,unsigned count,void *out){
 InterlockedIncrement(&wrappers);LONG epoch=atom(&generation),r;
 __try{
  r=extended_original(self,ep,count,out);DWORD last=GetLastError();
  if(enter(epoch)){
   __try{if(r>=0 && out && count>=7 && contains(ep,endpoint))for(unsigned i=2;i<=5;i++)if(mask&(1u<<i)){void *p=(BYTE*)out+i*0x130;put32(p,0x18,1);expiry(p,0x20);put32(p,0x28,0);}}
   __finally{InterlockedExchangeAdd(&gate,-2);}
  }
  SetLastError(last);
 }__finally{InterlockedDecrement(&wrappers);}return r;
}
/* Retain disabled trampolines and pin their owner: a thread may have entered
   an original call before disable. Never unload its return address or code. */
static void finish(void){
 LONG old;do{old=atom(&gate);}while(InterlockedCompareExchange(&gate,old|1,old)!=old);
 for(unsigned i=0;i<3;i++)if(targets[i]){
  MH_STATUS s=MH_DisableHook(targets[i]);if(s!=MH_OK && s!=MH_ERROR_DISABLED)stop_error=100+(DWORD)s;
 }
 ULONGLONG deadline=GetTickCount64()+5000;
 while((atom(&gate)>>1) && GetTickCount64()<deadline)Sleep(1);
 if(atom(&gate)>>1)stop_error=201;
 for(unsigned i=0;i<3;i++)if(targets[i] && memcmp(targets[i],before[i],32))stop_error=202;
 InterlockedExchange(&state,stop_error?3:2);
}
static DWORD WINAPI watch(void *unused){
 (void)unused;
 for(;;){
  HANDLE events[]={stop_event,host};DWORD wait=WaitForMultipleObjects(host?2:1,events,FALSE,250);
  AcquireSRWLockExclusive(&control);
  int done=wait!=WAIT_TIMEOUT || GetTickCount64()-lease>5000;
  if(done)finish();
  ReleaseSRWLockExclusive(&control);
  if(done)return 0;
 }
}
static DWORD fail(HookConfig *c,DWORD code){c->error=code;c->win32=GetLastError();return code;}
static int valid(HookConfig *c){return c && c->magic==NSP_HOOK_MAGIC && c->size==sizeof(*c);}
static void report(HookConfig *c){
 size_t n=(size_t)snprintf(c->output,sizeof(c->output),"{\"backend\":\"minhook\",\"state\":%ld,\"hookEnabled\":%s,\"stopError\":%lu,\"hookModuleResident\":true,\"trampolineRetained\":true,\"entries\":[",atom(&state),atom(&state)==1?"true":"false",stop_error);
 for(unsigned i=0;i<3;i++){
  n+=(size_t)snprintf(c->output+n,sizeof(c->output)-n,"%s{\"rva\":%u,\"restored\":%s,\"before\":[",i?",":"",rvas[i],targets[i]&&!memcmp(targets[i],before[i],32)?"true":"false");
  for(unsigned j=0;j<32;j++)n+=(size_t)snprintf(c->output+n,sizeof(c->output)-n,"%s%u",j?",":"",before[i][j]);
  n+=(size_t)snprintf(c->output+n,sizeof(c->output)-n,"]}");
 }
 snprintf(c->output+n,sizeof(c->output)-n,"]}");
}
__declspec(dllexport) DWORD WINAPI nsp_hook_start(HookConfig *c){
 if(!valid(c))return 1;
 AcquireSRWLockExclusive(&control);DWORD result=0;
 if(atom(&state)==1 || atom(&wrappers) || stop_error){result=fail(c,2);goto done;}
 if(c->fixture>1 || !c->mask || (c->mask&~0x3cu) || !c->endpoint[0] || c->endpoint[511] || !c->host_handle){result=fail(c,3);goto done;}
 if(watcher && WaitForSingleObject(watcher,0)!=WAIT_OBJECT_0){result=fail(c,4);goto done;}
 HMODULE image=GetModuleHandleW(c->fixture?L"nsp_enum_fixture.dll":L"AudioSrv.dll");
 if(!image){result=fail(c,5);goto done;}
 static const unsigned char prologues[3][16]={
  {0x40,0x53,0x56,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0xe8,0x00},
  {0x40,0x55,0x53,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,0x48,0x8b,0xec},
  {0x48,0x89,0x5c,0x24,0x18,0x55,0x56,0x57,0x41,0x56,0x41,0x57,0x48,0x8b,0xec,0x48}};
 const char *exports[]={"license_status","subtype","extended"};
 void *addresses[3];
 for(unsigned i=0;i<3;i++){
  addresses[i]=c->fixture?(void*)GetProcAddress(image,exports[i]):(BYTE*)image+rvas[i];
  if(!addresses[i] || (targets[i] && addresses[i]!=targets[i]) || (!targets[i] && !c->fixture && memcmp(addresses[i],prologues[i],16))){result=fail(c,6);goto done;}
 }
 if(!targets[0]){
  MH_STATUS s=MH_Initialize();if(s!=MH_OK){result=fail(c,100+(DWORD)s);goto done;}
  HMODULE pinned;
  if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,(LPCWSTR)&status_detour,&pinned)){result=fail(c,7);goto done;}
  void *detours[]={(void*)&status_detour,(void*)&subtype_detour,(void*)&extended_detour};
  void **originals[]={(void**)&status_original,(void**)&subtype_original,(void**)&extended_original};
  for(unsigned i=0;i<3;i++){
   memcpy(before[i],addresses[i],32);s=MH_CreateHook(addresses[i],detours[i],originals[i]);
   if(s!=MH_OK){stop_error=100+(DWORD)s;result=fail(c,stop_error);goto done;}
   targets[i]=addresses[i];
  }
 }
 if(watcher){CloseHandle(watcher);watcher=NULL;}
 if(stop_event)CloseHandle(stop_event);
 if(host)CloseHandle(host);
 host=(HANDLE)(uintptr_t)c->host_handle;c->host_handle=0;
 if(WaitForSingleObject(host,0)!=WAIT_TIMEOUT){result=fail(c,8);goto done;}
 stop_event=CreateEventW(NULL,TRUE,FALSE,NULL);if(!stop_event){result=fail(c,9);goto done;}
 wcscpy(endpoint,c->endpoint);mask=c->mask;lease=GetTickCount64();
 InterlockedIncrement(&generation);InterlockedExchange(&gate,0);
 for(unsigned i=0;i<3;i++){
  MH_STATUS s=MH_EnableHook(targets[i]);
  if(s!=MH_OK){result=fail(c,100+(DWORD)s);finish();goto done;}
  memcpy(after[i],targets[i],32);
 }
 InterlockedExchange(&state,1);
 watcher=CreateThread(NULL,0,watch,NULL,0,NULL);
 if(!watcher){result=fail(c,10);finish();goto done;}
 report(c);
 done:
 /* A rejected start must not leak the controller's duplicated handle. */
 if(c->host_handle){CloseHandle((HANDLE)(uintptr_t)c->host_handle);c->host_handle=0;}
 ReleaseSRWLockExclusive(&control);return result;
}
__declspec(dllexport) DWORD WINAPI nsp_hook_status(HookConfig *c){
 if(!valid(c))return 1;
 AcquireSRWLockExclusive(&control);
 if(atom(&state)==1){
  int healthy=GetTickCount64()-lease<=5000;
  for(unsigned i=0;i<3;i++)if(memcmp(targets[i],after[i],32))healthy=0;
  if(healthy)lease=GetTickCount64();else{stop_error=203;finish();}
 }
 report(c);ReleaseSRWLockExclusive(&control);return 0;
}
__declspec(dllexport) DWORD WINAPI nsp_hook_stop(HookConfig *c){
 if(!valid(c))return 1;
 AcquireSRWLockExclusive(&control);if(stop_event)SetEvent(stop_event);HANDLE thread=watcher;ReleaseSRWLockExclusive(&control);
 if(thread && WaitForSingleObject(thread,12000)!=WAIT_OBJECT_0)return fail(c,20);
 AcquireSRWLockExclusive(&control);report(c);DWORD result=stop_error?fail(c,stop_error):0;ReleaseSRWLockExclusive(&control);return result;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID unused){(void)unused;if(reason==DLL_PROCESS_ATTACH)DisableThreadLibraryCalls(h);return TRUE;}
