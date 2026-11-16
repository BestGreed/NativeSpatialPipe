// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <mmdeviceapi.h>
#include <wincrypt.h>
#include <memory>

struct NativeHandle {HANDLE value=nullptr;~NativeHandle(){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);}};
static bool file_hash(HCRYPTHASH hash,const std::wstring& path){
 NativeHandle file;file.value=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
 if(file.value==INVALID_HANDLE_VALUE)return false;
 BYTE bytes[65536];DWORD n;
 do{if(!ReadFile(file.value,bytes,sizeof(bytes),&n,nullptr))return false;if(n&&!CryptHashData(hash,bytes,n,0))return false;}while(n);
 return true;
}
static std::wstring route_mutex(const std::wstring& config){
 unsigned long long hash=1469598103934665603ull;
 for(wchar_t c:config){hash^=towlower(c);hash*=1099511628211ull;}
 wchar_t name[128];swprintf(name,128,L"Local\\NSP.FilmsTV.MFT.%016llx",hash);return name;
}
static bool prefix_path(const std::wstring& path,const std::wstring& root){
 return path.size()>root.size()&&!_wcsnicmp(path.c_str(),root.c_str(),root.size())&&path[root.size()]==L'\\';
}
static int disable_persistent_route(){
 wchar_t local[32768];
 DWORD size=GetEnvironmentVariableW(L"LOCALAPPDATA",local,32768);
 if(!size||size>=32768)return 20;
 const std::wstring home=std::wstring(local)+L"\\Packages\\Microsoft.ZuneVideo_8wekyb3d8bbwe\\LocalState\\NSP-Mft-FSA";
 const wchar_t* key=L"SOFTWARE\\Classes\\CLSID\\{7C860C4B-817C-481F-AC76-6D4CC2B5549D}\\InprocServer32";
 wchar_t path[32768]{};DWORD bytes=sizeof(path);
 const LSTATUS rc=RegGetValueW(HKEY_LOCAL_MACHINE,key,nullptr,RRF_RT_REG_SZ,nullptr,path,&bytes);
 if(rc==ERROR_FILE_NOT_FOUND||rc==ERROR_PATH_NOT_FOUND)return 0;
 if(rc!=ERROR_SUCCESS)return 30;
 std::wstring dll=path;
 if(!prefix_path(dll,home))return 0;
 const auto slash=dll.find_last_of(L'\\');
 if(slash==dll.npos||_wcsicmp(dll.c_str()+slash+1,L"NspDolbyMft.dll"))return 31;
 const std::wstring config=dll.substr(0,slash+1)+L"nsp-mft.ini";
 if(!WritePrivateProfileStringW(L"routing",L"enabled",L"0",config.c_str()))return 29;
 const auto mutex_name=route_mutex(config);
 NativeHandle watch;watch.value=OpenMutexW(SYNCHRONIZE,FALSE,mutex_name.c_str());
 if(watch.value){const DWORD wait=WaitForSingleObject(watch.value,15000);if(wait!=WAIT_OBJECT_0&&wait!=WAIT_ABANDONED)return 38;}
 HRESULT init=CoInitializeEx(nullptr,COINIT_MULTITHREADED);
 if(FAILED(init))return 27;
 HMODULE module=LoadLibraryExW(dll.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
 const auto unreg=module?reinterpret_cast<HRESULT(WINAPI*)()>(GetProcAddress(module,"DllUnregisterServer")):nullptr;
 const HRESULT hr=unreg?unreg():E_FAIL;
 if(module)FreeLibrary(module);
 CoUninitialize();
 return FAILED(hr)?32:0;
}
// Called by FSA automatically. Reuses the existing native watch implementation;
// no script, service, task, decoder shortcut or default-device change.
static int ensure_films_tv(){
 wchar_t own[32768],local[32768];
 if(!GetModuleFileNameW(nullptr,own,32768)||!GetEnvironmentVariableW(L"LOCALAPPDATA",local,32768))return 20;
 std::wstring source=own;source.resize(source.find_last_of(L"\\/"));
 const wchar_t* names[]={L"NspDolbyMft.dll",L"nsp_mf_decoder.dll",L"libunwind.dll",L"NspMftRoute.dll",L"NspMftRouteControl.exe"};
 HCRYPTPROV provider=0;HCRYPTHASH hash=0;
 if(!CryptAcquireContextW(&provider,nullptr,nullptr,PROV_RSA_AES,CRYPT_VERIFYCONTEXT))return 21;
 if(!CryptCreateHash(provider,CALG_SHA_256,0,0,&hash)){CryptReleaseContext(provider,0);return 22;}
 bool valid=true;for(auto name:names)valid=valid&&file_hash(hash,source+L"\\"+name);
 BYTE digest[32];DWORD size=32;valid=valid&&CryptGetHashParam(hash,HP_HASHVAL,digest,&size,0);
 CryptDestroyHash(hash);CryptReleaseContext(provider,0);if(!valid)return 23;
 wchar_t version[33]{};for(unsigned i=0;i<16;i++)swprintf(version+i*2,3,L"%02x",digest[i]);
 std::wstring home=std::wstring(local)+L"\\Packages\\Microsoft.ZuneVideo_8wekyb3d8bbwe\\LocalState\\NSP-Mft-FSA";
 if(!CreateDirectoryW(home.c_str(),nullptr)&&GetLastError()!=ERROR_ALREADY_EXISTS)return 24;
 std::wstring stage=home+L"\\"+version;
 if(!CreateDirectoryW(stage.c_str(),nullptr)&&GetLastError()!=ERROR_ALREADY_EXISTS)return 25;
 for(auto name:names){std::wstring target=stage+L"\\"+name;
  if(GetFileAttributesW(target.c_str())==INVALID_FILE_ATTRIBUTES&&!CopyFileW((source+L"\\"+name).c_str(),target.c_str(),TRUE))return 26;
 }
 HRESULT co=CoInitializeEx(nullptr,COINIT_MULTITHREADED);if(FAILED(co))return 27;
 IMMDeviceEnumerator* enumerator=nullptr;IMMDevice* device=nullptr;LPWSTR id=nullptr;
 HRESULT hr=CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,IID_PPV_ARGS(&enumerator));
 if(SUCCEEDED(hr))hr=enumerator->GetDefaultAudioEndpoint(eRender,eMultimedia,&device);
 if(SUCCEEDED(hr))hr=device->GetId(&id);
 std::wstring endpoint=id?id:L"";if(id)CoTaskMemFree(id);if(device)device->Release();if(enumerator)enumerator->Release();
 if(FAILED(hr)){CoUninitialize();return 28;}
 std::wstring config=stage+L"\\nsp-mft.ini";
 const auto write=[&](const wchar_t* section,const wchar_t* key,const wchar_t* value){return WritePrivateProfileStringW(section,key,value,config.c_str())!=FALSE;};
 valid=write(L"testing",L"pcmPreview",L"0")&&write(L"diagnostics",L"trace",L"0")&&
  write(L"spatial",L"enabled",L"1")&&write(L"spatial",L"asynchronous",L"1")&&write(L"spatial",L"endpoint",endpoint.c_str())&&
  write(L"routing",L"enabled",L"1")&&write(L"routing",L"filmsTV",L"1")&&write(L"routing",L"appleMusic",L"1")&&
  write(L"routing",L"hookEnumeration",L"1")&&write(L"routing",L"hookAppleMusic",L"1")&&write(L"routing",L"hookFilmsTV",L"0")&&write(L"routing",L"bindSessionClock",L"1");
 if(!valid){CoUninitialize();return 29;}
 const wchar_t* key=L"SOFTWARE\\Classes\\CLSID\\{7C860C4B-817C-481F-AC76-6D4CC2B5549D}\\InprocServer32";
 HKEY registry=nullptr;wchar_t registered[32768]{};DWORD bytes=sizeof(registered),type=0;
 if(RegOpenKeyExW(HKEY_LOCAL_MACHINE,key,0,KEY_READ|KEY_WOW64_64KEY,&registry)==ERROR_SUCCESS){
  LSTATUS rc=RegQueryValueExW(registry,nullptr,nullptr,&type,reinterpret_cast<BYTE*>(registered),&bytes);RegCloseKey(registry);
  if(rc!=ERROR_SUCCESS||type!=REG_SZ){CoUninitialize();return 30;}
 }
 std::wstring target=stage+L"\\NspDolbyMft.dll";
 if(*registered&&_wcsicmp(registered,target.c_str())){
  if(!prefix_path(registered,home)){CoUninitialize();return 31;}
  std::wstring oldConfig=registered;oldConfig.resize(oldConfig.find_last_of(L'\\'));oldConfig+=L"\\nsp-mft.ini";
  WritePrivateProfileStringW(L"routing",L"enabled",L"0",oldConfig.c_str());
  HMODULE old=LoadLibraryExW(registered,nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
  auto unreg=old?reinterpret_cast<HRESULT(WINAPI*)()>(GetProcAddress(old,"DllUnregisterServer")):nullptr;
  hr=unreg?unreg():E_FAIL;if(old)FreeLibrary(old);if(FAILED(hr)){CoUninitialize();return 32;}
 }
 if(!*registered||_wcsicmp(registered,target.c_str())){
  HMODULE module=LoadLibraryExW(target.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
  auto reg=module?reinterpret_cast<HRESULT(WINAPI*)()>(GetProcAddress(module,"DllRegisterServer")):nullptr;
  hr=reg?reg():E_FAIL;if(module)FreeLibrary(module);if(FAILED(hr)){CoUninitialize();return 33;}
 }
 CoUninitialize();
 std::wstring name=route_mutex(config);NativeHandle existing;existing.value=OpenMutexW(SYNCHRONIZE,FALSE,name.c_str());if(existing.value)return 0;
 std::wstring exe=stage+L"\\NspMftRouteControl.exe",command=L"\""+exe+L"\" watch \""+config+L"\"";
 STARTUPINFOW startup{sizeof(startup)};PROCESS_INFORMATION child{};
 SECURITY_ATTRIBUTES security{sizeof(security),nullptr,TRUE};NativeHandle log;
 log.value=CreateFileW((stage+L"\\route-watch.jsonl").c_str(),FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,&security,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
 if(log.value==INVALID_HANDLE_VALUE)return 37;
 startup.dwFlags|=STARTF_USESTDHANDLES;startup.hStdOutput=log.value;startup.hStdError=log.value;
 if(!CreateProcessW(exe.c_str(),command.data(),nullptr,nullptr,TRUE,CREATE_NO_WINDOW,nullptr,stage.c_str(),&startup,&child))return 34;
 CloseHandle(child.hThread);NativeHandle process;process.value=child.hProcess;
 for(unsigned attempt=0;attempt<100;attempt++){
  NativeHandle guard;guard.value=OpenMutexW(SYNCHRONIZE,FALSE,name.c_str());if(guard.value)return 0;
  if(WaitForSingleObject(process.value,20)==WAIT_OBJECT_0)return 35;
 }
 return 36;
}
