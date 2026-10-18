// Native-only MinHook adapter. The small trampoline owner is intentionally pinned.
#include "Native-Hook.h"
#include "MinHook.h"
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef uintptr_t (*Boundary)(void*,void*,void*);
typedef void* (*Create)(void*,void*,void*,void*,uint64_t);
typedef void (*Packet)(void*,void*);
typedef int (*Stop)(void*);
typedef int (*Report)(void*,char*,uint32_t);
typedef void (*Destroy)(void*);
typedef void* (*Open)(const wchar_t*,const wchar_t*,int);
typedef void (*Close)(void*);
static SRWLOCK control=SRWLOCK_INIT;
static volatile LONG gate=1,generation=0,wrappers=0,state=0;
static Boundary original;static void *target,*ctx,*link;
static Packet packet;static Stop stop_capture;static Report report_capture;static Destroy destroy_capture;static Close close_link;
static HMODULE capture_dll,send_dll;static HANDLE stop_event,watchdog,owner_process,host_process;
static unsigned char before[32];static char final_report[10000];static DWORD stop_error;
static LONG read_atomic(volatile LONG *p){return InterlockedCompareExchange(p,0,0);}
static uintptr_t detour(void *a,void *b,void *c){
 InterlockedIncrement(&wrappers);LONG epoch=read_atomic(&generation);uintptr_t result=0;
 __try {
  result=original(a,b,c);DWORD last=GetLastError();LONG old;
  do {old=read_atomic(&gate);if(old&1)goto done;}while(InterlockedCompareExchange(&gate,old+2,old)!=old);
  if(epoch==read_atomic(&generation))packet(ctx,(char*)a-0x590);
  InterlockedExchangeAdd(&gate,-2);
 done:SetLastError(last);
 } __finally {InterlockedDecrement(&wrappers);}
 return result;
}
static void finish(void){
 LONG old;do {old=read_atomic(&gate);}while(InterlockedCompareExchange(&gate,old|1,old)!=old);
 MH_STATUS disabled=MH_DisableHook(target);
 if(disabled!=MH_OK){stop_error=100+(DWORD)disabled;return;}
 ULONGLONG deadline=GetTickCount64()+5000;
 while((read_atomic(&gate)>>1)&&GetTickCount64()<deadline)Sleep(1);
 if(read_atomic(&gate)>>1){stop_error=201;return;}
 if(stop_capture(ctx)!=0){stop_error=202;return;}
 if(report_capture(ctx,final_report,8192)<0){stop_error=203;return;}
 destroy_capture(ctx);ctx=NULL;BOOL capture_released=FreeLibrary(capture_dll);capture_dll=NULL;
 close_link(link);link=NULL;BOOL send_released=FreeLibrary(send_dll);send_dll=NULL;
 size_t n=strlen(final_report);if(n&&final_report[n-1]=='}')n--;
 snprintf(final_report+n,sizeof(final_report)-n,",\"hookBackend\":\"minhook\",\"hookBytesRestored\":%s,\"captureLibraryReleased\":%s,\"transportLibraryReleased\":%s,\"hookModuleResident\":true,\"trampolineRetained\":true,\"activeOriginalCallsAtStop\":%ld}",memcmp(before,target,32)==0?"true":"false",capture_released?"true":"false",send_released?"true":"false",read_atomic(&wrappers));
}
static DWORD WINAPI watch(void *duration){
 DWORD timeout=(DWORD)(uintptr_t)duration;
 if(owner_process){HANDLE events[]={stop_event,owner_process,host_process};WaitForMultipleObjects(host_process?3:2,events,FALSE,INFINITE);}
 else WaitForSingleObject(stop_event,timeout);
 finish();InterlockedExchange(&state,stop_error?3:2);return 0;
}
static DWORD fail(HookConfig *c,DWORD e){c->error=e;c->win32=GetLastError();return e;}
__declspec(dllexport) DWORD WINAPI nsp_hook_start(HookConfig *c){
 if(!c||c->magic!=NSP_HOOK_MAGIC||c->size!=sizeof(*c))return 1;
 AcquireSRWLockExclusive(&control);DWORD result=0;
 if(read_atomic(&state)==1||read_atomic(&wrappers)||stop_error){result=fail(c,2);goto done;}
 if((c->duration_ms && (c->duration_ms<100||c->duration_ms>1200000))||c->fixture>1||(!c->duration_ms&&!c->owner_pid)){result=fail(c,3);goto done;}
 if(owner_process){CloseHandle(owner_process);owner_process=NULL;}
 if(host_process){CloseHandle(host_process);host_process=NULL;}
 if(!c->duration_ms){
  owner_process=(HANDLE)(uintptr_t)c->owner_handle;
  if(!owner_process||WaitForSingleObject(owner_process,0)!=WAIT_TIMEOUT){result=fail(c,30);goto cleanup;}
  if(c->host_pid){host_process=(HANDLE)(uintptr_t)c->host_handle;if(!host_process||WaitForSingleObject(host_process,0)!=WAIT_TIMEOUT){result=fail(c,31);goto cleanup;}}
 }
 c->capture[1023]=0;c->send[1023]=0;c->mapping[255]=0;c->event[255]=0;
 HMODULE image=GetModuleHandleW(c->fixture?L"nsp_fixture.dll":NULL);
 wchar_t image_path[1024];if(!image||!GetModuleFileNameW(image,image_path,1024)){result=fail(c,4);goto done;}
 if(!c->fixture){const wchar_t *name=wcsrchr(image_path,L'\\');if(!name||_wcsicmp(name+1,L"audiodg.exe")){result=fail(c,5);goto done;}}
 void *address=c->fixture?(void*)GetProcAddress(image,"boundary"):(char*)image+0x31930;
 const unsigned char prologue[]={0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x10,0x48,0x89,0x74,0x24,0x18,0x57};
 if(!address||(!c->fixture&&memcmp(address,prologue,sizeof(prologue)))){result=fail(c,6);goto done;}
 if(target&&target!=address){result=fail(c,7);goto done;}
 capture_dll=LoadLibraryW(c->capture);send_dll=LoadLibraryW(c->send);
 if(!capture_dll||!send_dll){result=fail(c,8);goto cleanup;}
 Create create=(Create)GetProcAddress(capture_dll,"nsp_capture_create");packet=(Packet)GetProcAddress(capture_dll,"nsp_capture_packet");stop_capture=(Stop)GetProcAddress(capture_dll,"nsp_capture_stop");report_capture=(Report)GetProcAddress(capture_dll,"nsp_capture_report");destroy_capture=(Destroy)GetProcAddress(capture_dll,"nsp_capture_destroy");
 Open open=(Open)GetProcAddress(send_dll,"nsp_link_open");close_link=(Close)GetProcAddress(send_dll,"nsp_link_close");void *publish=(void*)GetProcAddress(send_dll,"nsp_link_send");
 if(!create||!packet||!stop_capture||!report_capture||!destroy_capture||!open||!close_link||!publish){result=fail(c,9);goto cleanup;}
 link=open(c->mapping,c->event,0);if(!link){result=fail(c,10);goto cleanup;}
 ctx=create((char*)image+0x9f4f8,(char*)image+0xa2d58,publish,link,c->budget);if(!ctx){result=fail(c,11);goto cleanup;}
 if(!target){
  MH_STATUS status=MH_Initialize();if(status!=MH_OK){result=fail(c,100+(DWORD)status);goto cleanup;}
  memcpy(before,address,32);status=MH_CreateHook(address,detour,(void**)&original);
  if(status!=MH_OK){MH_Uninitialize();result=fail(c,100+(DWORD)status);goto cleanup;}
  target=address;
  HMODULE owner;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,(LPCWSTR)&detour,&owner)){result=fail(c,12);goto cleanup;}
 }
 if(watchdog){CloseHandle(watchdog);CloseHandle(stop_event);watchdog=stop_event=NULL;}
 stop_event=CreateEventW(NULL,TRUE,FALSE,NULL);if(!stop_event){result=fail(c,13);goto cleanup;}
 final_report[0]=0;InterlockedIncrement(&generation);InterlockedExchange(&gate,0);
 MH_STATUS status=MH_EnableHook(target);if(status!=MH_OK){InterlockedExchange(&gate,1);result=fail(c,100+(DWORD)status);goto cleanup;}
 InterlockedExchange(&state,1);watchdog=CreateThread(NULL,0,watch,(void*)(uintptr_t)c->duration_ms,0,NULL);
 if(!watchdog){finish();InterlockedExchange(&state,2);result=fail(c,14);goto done;}
 snprintf(c->output,sizeof(c->output),"{\"kind\":\"ready\",\"pid\":%lu,\"mode\":\"minhook-native\",\"hookModuleResident\":true}",GetCurrentProcessId());goto done;
 cleanup:
 if(ctx){stop_capture(ctx);destroy_capture(ctx);ctx=NULL;}if(link){close_link(link);link=NULL;}
 if(capture_dll){FreeLibrary(capture_dll);capture_dll=NULL;}if(send_dll){FreeLibrary(send_dll);send_dll=NULL;}
 if(owner_process){CloseHandle(owner_process);owner_process=NULL;}
 if(host_process){CloseHandle(host_process);host_process=NULL;}
 done:
 if(result&&read_atomic(&state)!=1){if(owner_process){CloseHandle(owner_process);owner_process=NULL;}if(host_process){CloseHandle(host_process);host_process=NULL;}}
 ReleaseSRWLockExclusive(&control);return result;
}
__declspec(dllexport) DWORD WINAPI nsp_hook_stop(HookConfig *c){
 if(!c||c->magic!=NSP_HOOK_MAGIC||c->size!=sizeof(*c))return 1;
 AcquireSRWLockExclusive(&control);DWORD result=0;
 if(!watchdog){result=fail(c,20);goto done;}
 SetEvent(stop_event);if(WaitForSingleObject(watchdog,15000)!=WAIT_OBJECT_0){result=fail(c,21);goto done;}
 if(stop_error){result=fail(c,stop_error);goto done;}
 memcpy(c->output,final_report,sizeof(c->output));
 done:ReleaseSRWLockExclusive(&control);return result;
}
__declspec(dllexport) DWORD WINAPI nsp_hook_status(HookConfig *c){
 if(!c||c->magic!=NSP_HOOK_MAGIC||c->size!=sizeof(*c))return 1;
 LONG s=read_atomic(&state);
 snprintf(c->output,sizeof(c->output),"{\"state\":%ld,\"activeOriginalCalls\":%ld,\"stopError\":%lu,\"hookBytesRestored\":%s,\"hookEnabled\":%s}",s,read_atomic(&wrappers),stop_error,(s==2&&target&&!memcmp(before,target,32))?"true":"false",s==1?"true":"false");return 0;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD why,LPVOID reserved){(void)reserved;if(why==DLL_PROCESS_ATTACH)DisableThreadLibraryCalls(h);return TRUE;}
