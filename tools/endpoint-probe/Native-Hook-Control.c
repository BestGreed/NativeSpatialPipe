// Explicit LoadLibrary/export invocation, no Frida, shellcode, or reflective loader.
#ifdef NSP_ENUMERATION_CONTROL
#include "../pipeline-init/Enum-Hook.h"
#else
#include "Native-Hook.h"
#endif
#include <tlhelp32.h>
#include <stdio.h>
#include <wchar.h>
#include <wincrypt.h>
static uintptr_t module_base(DWORD pid,const wchar_t *name){
 HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);if(snap==INVALID_HANDLE_VALUE)return 0;
 MODULEENTRY32W m={.dwSize=sizeof(m)};uintptr_t found=0;
 if(Module32FirstW(snap,&m))do {if(!_wcsicmp(m.szModule,name)){found=(uintptr_t)m.modBaseAddr;break;}}while(Module32NextW(snap,&m));CloseHandle(snap);return found;
}
static int verified_audio(const wchar_t *path){
#ifdef NSP_ENUMERATION_CONTROL
 const unsigned char expected[32]={0x48,0x7e,0x51,0x03,0x5a,0x76,0x32,0x30,0x32,0xac,0xd5,0x99,0x65,0x11,0xc2,0x21,0xc4,0x49,0xcd,0x24,0x40,0x51,0xd3,0xb0,0xe0,0x57,0xee,0xc7,0x05,0x38,0xca,0x97};
#else
 const unsigned char expected[32]={0x3d,0xd2,0x4f,0xe6,0xfa,0xf0,0xb5,0x6f,0x99,0xd5,0xb0,0x0f,0x24,0x68,0x7b,0x5f,0x3b,0x3e,0x76,0xe1,0x5b,0x21,0x9e,0x23,0xf0,0x96,0x31,0x14,0xa0,0xdc,0xe9,0xa5};
#endif
 HCRYPTPROV provider=0;HCRYPTHASH hash=0;HANDLE f=INVALID_HANDLE_VALUE;int ok=0;unsigned char buf[65536],digest[32];DWORD got,size=32;
 if(!CryptAcquireContextW(&provider,NULL,NULL,PROV_RSA_AES,CRYPT_VERIFYCONTEXT)||!CryptCreateHash(provider,CALG_SHA_256,0,0,&hash))goto done;
 f=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,0,NULL);if(f==INVALID_HANDLE_VALUE)goto done;
 for(;;){if(!ReadFile(f,buf,sizeof(buf),&got,NULL))goto done;if(!got)break;if(!CryptHashData(hash,buf,got,0))goto done;}
 ok=CryptGetHashParam(hash,HP_HASHVAL,digest,&size,0)&&!memcmp(digest,expected,32);
 done:if(f!=INVALID_HANDLE_VALUE)CloseHandle(f);if(hash)CryptDestroyHash(hash);if(provider)CryptReleaseContext(provider,0);return ok;
}
static int remote_call(HANDLE process,void *function,void *arg,DWORD *code){
 HANDLE thread=CreateRemoteThread(process,NULL,0,(LPTHREAD_START_ROUTINE)function,arg,0,NULL);if(!thread)return 0;
 DWORD wait=WaitForSingleObject(thread,30000);int ok=wait==WAIT_OBJECT_0&&GetExitCodeThread(thread,code);CloseHandle(thread);return ok;
}
static void enable_debug(void){
 HANDLE token;TOKEN_PRIVILEGES p={.PrivilegeCount=1};
 if(!OpenProcessToken(GetCurrentProcess(),TOKEN_ADJUST_PRIVILEGES|TOKEN_QUERY,&token))return;
 if(LookupPrivilegeValueW(NULL,SE_DEBUG_NAME,&p.Privileges[0].Luid)){p.Privileges[0].Attributes=SE_PRIVILEGE_ENABLED;AdjustTokenPrivileges(token,FALSE,&p,sizeof(p),NULL,NULL);}
 CloseHandle(token); // Privilege is scoped to this short-lived controller process.
}
int wmain(int argc,wchar_t **argv){
 if(argc!=5){fprintf(stderr,"usage: control PID hook.dll start|stop config.bin\n");return 1;}
 DWORD pid=wcstoul(argv[1],NULL,10);HookConfig c={0};FILE *file=_wfopen(argv[4],L"rb");if(!file)return 2;
 int valid=fread(&c,1,sizeof(c),file)==sizeof(c)&&c.magic==NSP_HOOK_MAGIC&&c.size==sizeof(c);fclose(file);if(!valid)return 3;
 DWORD access=PROCESS_QUERY_INFORMATION|PROCESS_CREATE_THREAD|PROCESS_VM_OPERATION|PROCESS_VM_WRITE|PROCESS_VM_READ|PROCESS_DUP_HANDLE;
 HANDLE process=OpenProcess(access,FALSE,pid);if(!process&&GetLastError()==ERROR_ACCESS_DENIED){enable_debug();process=OpenProcess(access,FALSE,pid);}if(!process){fprintf(stderr,"OpenProcess %lu\n",GetLastError());return 4;}
 if(c.target_created){FILETIME created,exited,kernel,user;ULARGE_INTEGER value;
  if(!GetProcessTimes(process,&created,&exited,&kernel,&user)){CloseHandle(process);return 6;}
  value.LowPart=created.dwLowDateTime;value.HighPart=created.dwHighDateTime;
  if(value.QuadPart!=c.target_created){fprintf(stderr,"Target PID instance changed\n");CloseHandle(process);return 6;}
 }
 wchar_t image[1024];DWORD length=1024;DWORD protection=0;int result=5;void *remote=NULL;HMODULE local=NULL;
 if(!QueryFullProcessImageNameW(process,0,image,&length)||!GetProcessInformation(process,ProcessProtectionLevelInfo,&protection,sizeof(protection))||protection!=PROTECTION_LEVEL_NONE)goto done;
 const wchar_t *name=wcsrchr(image,L'\\');if(!name)goto done;
#ifdef NSP_ENUMERATION_CONTROL
 if(c.fixture){if(_wcsicmp(name+1,L"nsp_enum_fixture_host.exe")||!module_base(pid,L"nsp_enum_fixture.dll"))goto done;}
 else {
  wchar_t expected[1024],audio[1024];GetSystemDirectoryW(expected,1000);wcscpy(audio,expected);
  wcscat(expected,L"\\svchost.exe");wcscat(audio,L"\\AudioSrv.dll");
  if(_wcsicmp(image,expected)||!module_base(pid,L"AudioSrv.dll")||!verified_audio(audio))goto done;
  /* A system basename alone is insufficient: the remote module must be the
     verified System32 file, not an unrelated image with the same name. */
  HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
  MODULEENTRY32W m={.dwSize=sizeof(m)};int match=0;
  if(snapshot!=INVALID_HANDLE_VALUE){if(Module32FirstW(snapshot,&m))do{if(!_wcsicmp(m.szModule,L"AudioSrv.dll")){match=!_wcsicmp(m.szExePath,audio);break;}}while(Module32NextW(snapshot,&m));CloseHandle(snapshot);}
  if(!match)goto done;
 }
 const wchar_t *dllname=wcsrchr(argv[2],L'\\');if(!dllname)dllname=wcsrchr(argv[2],L'/');dllname=dllname?dllname+1:argv[2];
 if(_wcsicmp(dllname,L"nsp_enum_hook.dll"))goto done;
#else
 if(c.fixture){if(_wcsicmp(name+1,L"nsp_fixture_host.exe")||!module_base(pid,L"nsp_fixture.dll"))goto done;}
 else {wchar_t expected[1024];GetSystemDirectoryW(expected,1000);wcscat(expected,L"\\audiodg.exe");if(_wcsicmp(image,expected)||!verified_audio(image))goto done;}
 if(!wcscmp(argv[3],L"baseline")){
  unsigned char actual[16];const unsigned char expected[16]={0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x10,0x48,0x89,0x74,0x24,0x18,0x57};
  uintptr_t image_base=module_base(pid,L"audiodg.exe");
  if(!image_base||!ReadProcessMemory(process,(void*)(image_base+0x31930),actual,16,NULL)||memcmp(actual,expected,16))goto done;
  puts("{\"baselineVerified\":true}");result=0;goto done;
 }
 const wchar_t *dllname=wcsrchr(argv[2],L'\\');if(!dllname)dllname=wcsrchr(argv[2],L'/');dllname=dllname?dllname+1:argv[2];if(_wcsicmp(dllname,L"nsp_native_hook.dll")&&_wcsicmp(dllname,L"nsp_managed_hook.dll")&&_wcsicmp(dllname,L"nsp_managed_hook_v2.dll"))goto done;
 if(!wcscmp(argv[3],L"start") && _wcsicmp(dllname,L"nsp_native_hook.dll") && module_base(pid,L"nsp_native_hook.dll") && !c.legacy_stopped){fprintf(stderr,"Legacy hook stop evidence required\n");goto done;}
#endif
 local=LoadLibraryExW(argv[2],NULL,DONT_RESOLVE_DLL_REFERENCES);if(!local)goto done;
 uintptr_t base=module_base(pid,dllname);DWORD code=0;
 if(!base){
  if(wcscmp(argv[3],L"start"))goto done;
  SIZE_T size=(wcslen(argv[2])+1)*sizeof(wchar_t);remote=VirtualAllocEx(process,NULL,size,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);if(!remote||!WriteProcessMemory(process,remote,argv[2],size,NULL))goto done;
  void *load=(void*)GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"LoadLibraryW");HMODULE owner=NULL;wchar_t owner_path[1024];
  if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCWSTR)load,&owner)||!GetModuleFileNameW(owner,owner_path,1024))goto done;
  const wchar_t *owner_name=wcsrchr(owner_path,L'\\');uintptr_t remote_owner=module_base(pid,owner_name?owner_name+1:owner_path);if(!remote_owner)goto done;
  if(!remote_call(process,(void*)(remote_owner+(uintptr_t)load-(uintptr_t)owner),remote,&code)){remote=NULL;fprintf(stderr,"Load timed out/failed; remote parameter retained\n");goto done;}
  VirtualFreeEx(process,remote,0,MEM_RELEASE);remote=NULL;base=module_base(pid,dllname);if(!base)goto done;
 }
 /* Refuse export offsets from a different resident DLL build. Never call an
    offset computed from the new local image inside an older resident image. */
 IMAGE_DOS_HEADER remote_dos;IMAGE_NT_HEADERS64 remote_nt;
 IMAGE_DOS_HEADER *local_dos=(IMAGE_DOS_HEADER*)local;
 IMAGE_NT_HEADERS64 *local_nt=(IMAGE_NT_HEADERS64*)((BYTE*)local+local_dos->e_lfanew);
 if(!ReadProcessMemory(process,(void*)base,&remote_dos,sizeof(remote_dos),NULL)||remote_dos.e_magic!=IMAGE_DOS_SIGNATURE||remote_dos.e_lfanew<0||remote_dos.e_lfanew>4096)goto done;
 if(!ReadProcessMemory(process,(void*)(base+remote_dos.e_lfanew),&remote_nt,sizeof(remote_nt),NULL)||remote_nt.Signature!=IMAGE_NT_SIGNATURE||remote_nt.FileHeader.TimeDateStamp!=local_nt->FileHeader.TimeDateStamp||remote_nt.OptionalHeader.SizeOfImage!=local_nt->OptionalHeader.SizeOfImage||remote_nt.OptionalHeader.CheckSum!=local_nt->OptionalHeader.CheckSum){fprintf(stderr,"Resident hook build mismatch; refusing remote export offset\n");goto done;}
 const char *entry=!wcscmp(argv[3],L"start")?"nsp_hook_start":!wcscmp(argv[3],L"stop")?"nsp_hook_stop":!wcscmp(argv[3],L"status")?"nsp_hook_status":NULL;if(!entry)goto done;
 void *address=(void*)GetProcAddress(local,entry);if(!address)goto done;
 c.error=c.win32=0;c.output[0]=0;remote=VirtualAllocEx(process,NULL,sizeof(c),MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
 if(!wcscmp(argv[3],L"start")&&!c.duration_ms){
  DWORD ids[]={c.owner_pid,c.host_pid};uint64_t *values[]={&c.owner_handle,&c.host_handle};
  for(int i=0;i<2;i++)if(ids[i]){
   HANDLE source=OpenProcess(SYNCHRONIZE,FALSE,ids[i]),target_handle=NULL;
   if(!source)goto done;
   BOOL copied=DuplicateHandle(GetCurrentProcess(),source,process,&target_handle,SYNCHRONIZE,FALSE,0);CloseHandle(source);
   if(!copied)goto done;*values[i]=(uint64_t)(uintptr_t)target_handle;
  }
 }
 if(!remote||!WriteProcessMemory(process,remote,&c,sizeof(c),NULL))goto done;
 if(!remote_call(process,(void*)(base+(uintptr_t)address-(uintptr_t)local),remote,&code)){remote=NULL;fprintf(stderr,"Export timed out/failed; remote parameter retained\n");goto done;}
 if(!ReadProcessMemory(process,remote,&c,sizeof(c),NULL))goto done;
 if(code||c.error){fprintf(stderr,"native hook error=%lu config=%u win32=%u\n",code,c.error,c.win32);goto done;}
 puts(c.output);result=0;
 done:if(result)fprintf(stderr,"control failed win32=%lu\n",GetLastError());if(remote)VirtualFreeEx(process,remote,0,MEM_RELEASE);if(local)FreeLibrary(local);CloseHandle(process);return result;
}
