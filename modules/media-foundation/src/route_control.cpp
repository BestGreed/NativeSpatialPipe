// SPDX-License-Identifier: GPL-3.0-or-later
// Explicit, allowlisted Win32 loader. No executable allocation or protection changes.
#include <windows.h>
#include <tlhelp32.h>
#include <cstdio>
#include <string>
#include <vector>
#include <cwctype>
#include <map>
#include "films_tv_persistent.h"
int nsp_route_watch_main(int,wchar_t**);
int wmain(int argc,wchar_t** argv){
 if(argc==2&&!wcscmp(argv[1],L"ensure"))return ensure_films_tv();
 if(argc==2&&!wcscmp(argv[1],L"disable"))return disable_persistent_route();
 NativeHandle singleton;
 if(argc==3&&!wcscmp(argv[1],L"watch")){
  wchar_t absolute[32768];if(!GetFullPathNameW(argv[2],32768,absolute,nullptr))return 2;
  std::wstring name=route_mutex(absolute);
  singleton.value=CreateMutexW(nullptr,TRUE,name.c_str());
  if(!singleton.value)return 3;if(GetLastError()==ERROR_ALREADY_EXISTS)return 0;
 }
 return nsp_route_watch_main(argc,argv);
}
#define wmain nsp_route_watch_main
struct Target {DWORD pid;ULONGLONG created;};
static volatile LONG finish=0;
static BOOL WINAPI signal(DWORD){InterlockedExchange(&finish,1);return TRUE;}
static std::wstring image(HANDLE p){wchar_t path[32768];DWORD n=32768;return QueryFullProcessImageNameW(p,0,path,&n)?std::wstring(path,n):L"";}
static bool allowed(std::wstring path,const std::wstring& config){for(auto& c:path)c=towlower(c);return (path.find(L"\\windowsapps\\appleinc.applemusicwin_")!=path.npos&&path.ends_with(L"\\amplibraryagent.exe")&&GetPrivateProfileIntW(L"routing",L"appleMusic",0,config.c_str()))||(path.find(L"\\windowsapps\\microsoft.zunevideo_")!=path.npos&&path.ends_with(L"\\video.ui.exe")&&GetPrivateProfileIntW(L"routing",L"filmsTV",0,config.c_str()));}
static ULONGLONG creation(HANDLE h){FILETIME c,e,k,u;if(!GetProcessTimes(h,&c,&e,&k,&u))return 0;return (ULONGLONG)c.dwHighDateTime<<32|c.dwLowDateTime;}
static uintptr_t module(DWORD pid,const wchar_t* path,bool full){HANDLE s=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);if(s==INVALID_HANDLE_VALUE)return 0;MODULEENTRY32W m{sizeof(m)};uintptr_t address=0;BOOL ok=Module32FirstW(s,&m);while(ok){if(!_wcsicmp(full?m.szExePath:m.szModule,path)){address=(uintptr_t)m.modBaseAddr;break;}ok=Module32NextW(s,&m);}CloseHandle(s);return address;}
static bool call(HANDLE h,uintptr_t address,void* arg,DWORD& result){HANDLE t=CreateRemoteThread(h,nullptr,0,(LPTHREAD_START_ROUTINE)address,arg,0,nullptr);if(!t)return false;DWORD wait=WaitForSingleObject(t,6000);bool ok=wait==WAIT_OBJECT_0&&GetExitCodeThread(t,&result);CloseHandle(t);return ok;}
static uintptr_t kernel(DWORD pid,HANDLE process,const char* name){auto p=GetProcAddress(GetModuleHandleW(L"kernel32.dll"),name);HMODULE owner=nullptr;if(!p||!GetModuleHandleExW(6,(LPCWSTR)p,&owner))return 0;wchar_t path[32768];GetModuleFileNameW(owner,path,32768);uintptr_t base=module(pid,path,true);BYTE original[24];SIZE_T n=0;uintptr_t address=base+(uintptr_t)p-(uintptr_t)owner;if(!base||!ReadProcessMemory(process,(void*)address,original,24,&n)||n!=24||memcmp(original,(void*)p,24))return 0;return address;}
static DWORD apply(Target target,const std::wstring& dll,const std::wstring& config,bool start){HANDLE process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_CREATE_THREAD|PROCESS_VM_OPERATION|PROCESS_VM_WRITE|PROCESS_VM_READ,FALSE,target.pid);if(!process)return GetLastError();DWORD error=0;do{if(creation(process)!=target.created||!allowed(image(process),config)){error=ERROR_INVALID_OWNER;break;}DWORD protection=0;auto getInfo=reinterpret_cast<BOOL(WINAPI*)(HANDLE,int,void*,DWORD)>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"GetProcessInformation"));if(!getInfo||!getInfo(process,7,&protection,sizeof(protection))||protection!=0xfffffffe){error=ERROR_ACCESS_DENIED;break;}uintptr_t remote=module(target.pid,dll.c_str(),true);DWORD result=0;if(!remote&&start){uintptr_t load=kernel(target.pid,process,"LoadLibraryW");if(!load){error=ERROR_INVALID_ADDRESS;break;}SIZE_T size=(dll.size()+1)*2;void* data=VirtualAllocEx(process,nullptr,size,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);if(!data){error=GetLastError();break;}SIZE_T written=0;if(!WriteProcessMemory(process,data,dll.c_str(),size,&written)||written!=size){VirtualFreeEx(process,data,0,MEM_RELEASE);error=ERROR_WRITE_FAULT;break;}if(!call(process,load,data,result)){error=WAIT_TIMEOUT;break;}VirtualFreeEx(process,data,0,MEM_RELEASE);remote=module(target.pid,dll.c_str(),true);}if(!remote){error=ERROR_MOD_NOT_FOUND;break;}HMODULE local=LoadLibraryExW(dll.c_str(),nullptr,DONT_RESOLVE_DLL_REFERENCES);if(!local){error=GetLastError();break;}auto fn=GetProcAddress(local,start?"NspRouteStartThread":"NspRouteStopThread");if(!fn||!call(process,remote+(uintptr_t)fn-(uintptr_t)local,nullptr,result))error=WAIT_TIMEOUT;else error=result;if(error==0||error==1){auto statusFn=GetProcAddress(local,"NspRouteStatusThread");DWORD state=0;if(statusFn&&call(process,remote+(uintptr_t)statusFn-(uintptr_t)local,nullptr,state)){printf("{\"pid\":%lu,\"routeStatus\":%lu}\n",target.pid,state);fflush(stdout);}}FreeLibrary(local);}while(false);CloseHandle(process);return error;}
int wmain(int argc,wchar_t** argv){if(argc!=3){fprintf(stderr,"usage: NspMftRouteControl watch|stop CONFIG.ini\n");return 2;}wchar_t absolute[32768];if(!GetFullPathNameW(argv[2],32768,absolute,nullptr))return 2;std::wstring config=absolute;auto slash=config.find_last_of(L"\\/");if(slash==config.npos)return 2;std::wstring dll=config.substr(0,slash+1)+L"NspMftRoute.dll";bool watch=!wcscmp(argv[1],L"watch"),stop=!wcscmp(argv[1],L"stop");if(!watch&&!stop)return 2;SetConsoleCtrlHandler(signal,TRUE);std::vector<Target> attached;std::map<std::pair<DWORD,ULONGLONG>,ULONGLONG> attempted;do{HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);PROCESSENTRY32W p{sizeof(p)};if(snapshot==INVALID_HANDLE_VALUE)return 3;BOOL ok=Process32FirstW(snapshot,&p);while(ok){if(!_wcsicmp(p.szExeFile,L"AMPLibraryAgent.exe")||!_wcsicmp(p.szExeFile,L"Video.UI.exe")){HANDLE h=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,p.th32ProcessID);if(h){Target t{p.th32ProcessID,creation(h)};bool match=t.created&&allowed(image(h),config);CloseHandle(h);bool seen=false;for(auto& old:attached)seen|=old.pid==t.pid&&old.created==t.created;auto key=std::make_pair(t.pid,t.created);bool cooled=!attempted.count(key)||GetTickCount64()-attempted[key]>=10000;if(match&&!seen&&cooled&&(stop||GetPrivateProfileIntW(L"routing",L"enabled",0,config.c_str()))){attempted[key]=GetTickCount64();DWORD result=apply(t,dll,config,!stop);printf("{\"pid\":%lu,\"start\":%s,\"result\":%lu}\n",t.pid,stop?"false":"true",result);fflush(stdout);if(result==0||result==1)attached.push_back(t);}}}ok=Process32NextW(snapshot,&p);}CloseHandle(snapshot);if(stop)break;if(!GetPrivateProfileIntW(L"routing",L"enabled",0,config.c_str()))break;Sleep(1000);}while(!finish);if(watch){for(auto& t:attached){DWORD result=apply(t,dll,config,false);printf("{\"pid\":%lu,\"stopResult\":%lu}\n",t.pid,result);}}return 0;}
