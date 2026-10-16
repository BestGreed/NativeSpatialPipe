// Explicit LoadLibrary/export invocation, no Frida, shellcode, or reflective loader.
#include "Native-Hook.h"
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
 const unsigned char expected[32]={0x3d,0xd2,0x4f,0xe6,0xfa,0xf0,0xb5,0x6f,0x99,0xd5,0xb0,0x0f,0x24,0x68,0x7b,0x5f,0x3b,0x3e,0x76,0xe1,0x5b,0x21,0x9e,0x23,0xf0,0x96,0x31,0x14,0xa0,0xdc,0xe9,0xa5};
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
 DWORD access=PROCESS_QUERY_INFORMATION|PROCESS_CREATE_THREAD|PROCESS_VM_OPERATION|PROCESS_VM_WRITE|PROCESS_VM_READ;
 HANDLE process=OpenProcess(access,FALSE,pid);if(!process&&GetLastError()==ERROR_ACCESS_DENIED){enable_debug();process=OpenProcess(access,FALSE,pid);}if(!process){fprintf(stderr,"OpenProcess %lu\n",GetLastError());return 4;}
 wchar_t image[1024];DWORD length=1024;DWORD protection=0;int result=5;void *remote=NULL;HMODULE local=NULL;
 if(!QueryFullProcessImageNameW(process,0,image,&length)||!GetProcessInformation(process,ProcessProtectionLevelInfo,&protection,sizeof(protection))||protection!=PROTECTION_LEVEL_NONE)goto done;
 const wchar_t *name=wcsrchr(image,L'\\');if(!name)goto done;
 if(c.fixture){if(_wcsicmp(name+1,L"nsp_fixture_host.exe")||!module_base(pid,L"nsp_fixture.dll"))goto done;}
 else {wchar_t expected[1024];GetSystemDirectoryW(expected,1000);wcscat(expected,L"\\audiodg.exe");if(_wcsicmp(image,expected)||!verified_audio(image))goto done;}
 const wchar_t *dllname=wcsrchr(argv[2],L'\\');if(!dllname)dllname=wcsrchr(argv[2],L'/');dllname=dllname?dllname+1:argv[2];if(_wcsicmp(dllname,L"nsp_native_hook.dll"))goto done;
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
 const char *entry=!wcscmp(argv[3],L"start")?"nsp_hook_start":!wcscmp(argv[3],L"stop")?"nsp_hook_stop":!wcscmp(argv[3],L"status")?"nsp_hook_status":NULL;if(!entry)goto done;
 void *address=(void*)GetProcAddress(local,entry);if(!address)goto done;
 c.error=c.win32=0;c.output[0]=0;remote=VirtualAllocEx(process,NULL,sizeof(c),MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
 if(!remote||!WriteProcessMemory(process,remote,&c,sizeof(c),NULL))goto done;
 if(!remote_call(process,(void*)(base+(uintptr_t)address-(uintptr_t)local),remote,&code)){remote=NULL;fprintf(stderr,"Export timed out/failed; remote parameter retained\n");goto done;}
 if(!ReadProcessMemory(process,remote,&c,sizeof(c),NULL))goto done;
 if(code||c.error){fprintf(stderr,"native hook error=%lu config=%u win32=%u\n",code,c.error,c.win32);goto done;}
 puts(c.output);result=0;
 done:if(result)fprintf(stderr,"control failed win32=%lu\n",GetLastError());if(remote)VirtualFreeEx(process,remote,0,MEM_RELEASE);if(local)FreeLibrary(local);CloseHandle(process);return result;
}
