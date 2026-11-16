// Read-only audiodg queue discovery shared by PCM and browser identity.
#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include "profile.h"
#include <windows.h>
#include <mmdeviceapi.h>
#include <audiopolicy.h>
#include <mmreg.h>
#include <tlhelp32.h>
#include <bcrypt.h>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
struct QueueInfo {uint64_t base=0,mapping=0,control=0,stat=0;uint32_t pid=0,start=0,capacity=0,align=0,channels=0,mask=0;};
struct QueueProbe {
 HANDLE process=nullptr;
 DWORD host=0,source=0;
 uintptr_t image=0;
 std::wstring endpoint;
 ~QueueProbe(){if(process)CloseHandle(process);}
 std::atomic<uint64_t> eligibleCount{0},vtableHits{0},scanBytes{0},ownerMatches{0},formatRejected{0};
 std::atomic<uint32_t> lastCandidatePid{0},lastOwnerPid{0},pidMatches{0};
 bool read(uint64_t at,void*out,size_t n){SIZE_T got=0;return ReadProcessMemory(process,(void*)at,out,n,&got)&&got==n;}
 template<class T>bool value(uint64_t at,T&out){return read(at,&out,sizeof(out));}
 bool inspect(uint64_t base,QueueInfo& q,const std::set<DWORD>& allowed){
  uintptr_t vt=0,owner=0,association=0;DWORD pid=0,ownerPid=0;
  if(!value(base+0x1e0,vt)||vt!=image+PCM_INPUT_VTABLE_RVA||!value(base+0x1cc,pid))return false;
  lastCandidatePid=pid;if(!allowed.count(pid))return false;pidMatches++;
  if(value(base+0x40,owner)&&owner&&value(owner+0x10,ownerPid))lastOwnerPid=ownerPid;
  if(!value(base+0x40,owner)||!owner||!value(owner+0x10,ownerPid)||ownerPid!=pid||!value(owner+0x20,association)||association!=base+0x1c0)return false;
  ownerMatches++;
  q={};q.base=base;q.pid=pid;
  if(!value(base+0x50,q.mapping)||!value(base+0x58,q.control)||!value(base+0x60,q.stat)||!value(base+0x68,q.align)||!value(base+0xa8,q.capacity))return false;
  uint32_t end=0;BYTE fmt[40]{};
  if(!value(q.stat+0xa0,q.start)||!value(q.stat+0xa4,end)||!read(q.stat+0xb4,fmt,sizeof(fmt)))return false;
  WORD tag=0,bits=0,channels=0;DWORD rate=0,sub=0;
  memcpy(&tag,fmt,2);memcpy(&channels,fmt+2,2);memcpy(&rate,fmt+4,4);memcpy(&bits,fmt+14,2);sub=tag;
  if(tag==WAVE_FORMAT_EXTENSIBLE){memcpy(&sub,fmt+24,4);memcpy(&q.mask,fmt+20,4);}else if(channels==2)q.mask=3;
  q.channels=channels;
  bool ok=channels>=1&&channels<=8&&rate==48000&&bits==32&&sub==WAVE_FORMAT_IEEE_FLOAT&&q.align==channels*4&&q.capacity&&q.capacity<=65536&&q.capacity%q.align==0&&end>=q.start&&end-q.start==q.capacity&&end<=16*1024*1024&&q.mask&&__builtin_popcount(q.mask)==channels;
  if(!ok)formatRejected++;return ok;
 }
 std::set<DWORD> eligible(IMMDeviceEnumerator* enumerator){
  std::map<DWORD,std::set<std::wstring>> bindings;IMMDeviceCollection*devices=nullptr;
  if(FAILED(enumerator->EnumAudioEndpoints(eRender,DEVICE_STATE_ACTIVE,&devices)))return {};
  UINT count=0;devices->GetCount(&count);
  for(UINT i=0;i<count;i++){
   IMMDevice*d=nullptr;LPWSTR id=nullptr;IAudioSessionManager2*m=nullptr;IAudioSessionEnumerator*s=nullptr;
   if(SUCCEEDED(devices->Item(i,&d))&&SUCCEEDED(d->GetId(&id))&&SUCCEEDED(d->Activate(__uuidof(IAudioSessionManager2),CLSCTX_ALL,nullptr,(void**)&m))&&SUCCEEDED(m->GetSessionEnumerator(&s))){
    int n=0;s->GetCount(&n);for(int j=0;j<n;j++){IAudioSessionControl*c=nullptr;IAudioSessionControl2*c2=nullptr;AudioSessionState active=AudioSessionStateInactive;DWORD pid=0;
     if(SUCCEEDED(s->GetSession(j,&c))&&SUCCEEDED(c->GetState(&active))&&active==AudioSessionStateActive&&SUCCEEDED(c->QueryInterface(__uuidof(IAudioSessionControl2),(void**)&c2))&&SUCCEEDED(c2->GetProcessId(&pid))&&pid&&pid!=GetCurrentProcessId())bindings[pid].insert(id);
     if(c2)c2->Release();if(c)c->Release();}
   }if(s)s->Release();if(m)m->Release();if(id)CoTaskMemFree(id);if(d)d->Release();
  }devices->Release();std::set<DWORD> allowed;
  for(auto&[pid,ids]:bindings)if((!source||source==pid)&&ids.size()==1&&_wcsicmp(ids.begin()->c_str(),endpoint.c_str())==0)allowed.insert(pid);
  return allowed;
 }
};
static bool sha_matches(){
 wchar_t sys[MAX_PATH];GetSystemDirectoryW(sys,MAX_PATH);std::wstring path=std::wstring(sys)+L"\\audiodg.exe";
 HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);if(file==INVALID_HANDLE_VALUE)return false;
 BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;BYTE digest[32],block[65536];DWORD n=0;bool ok=BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0&&BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0)>=0;
 while(ok&&ReadFile(file,block,sizeof(block),&n,nullptr)&&n)ok=BCryptHashData(hash,block,n,0)>=0;
 if(ok)ok=BCryptFinishHash(hash,digest,32,0)>=0&&
  (memcmp(digest,PCM_IMAGE_SHA,32)==0||memcmp(digest,PCM_LOCAL_IMAGE_SHA,32)==0);
 if(hash)BCryptDestroyHash(hash);if(alg)BCryptCloseAlgorithmProvider(alg,0);CloseHandle(file);return ok;
}
template<class Read>
static bool with_audio_read_privilege(Read&& read){
 HANDLE token=nullptr;if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY|TOKEN_ADJUST_PRIVILEGES,&token))return false;
 bool ok=false;
 TOKEN_PRIVILEGES request{},old{};DWORD bytes=sizeof(old);request.PrivilegeCount=1;request.Privileges[0].Attributes=SE_PRIVILEGE_ENABLED;
 if(LookupPrivilegeValueW(nullptr,SE_DEBUG_NAME,&request.Privileges[0].Luid)){
  SetLastError(0);
  if(AdjustTokenPrivileges(token,FALSE,&request,sizeof(old),&old,&bytes)&&GetLastError()==0){
   ok=read()==ERROR_SUCCESS;
   if(!AdjustTokenPrivileges(token,FALSE,&old,0,nullptr,nullptr))ok=false;
  }
 }CloseHandle(token);return ok;
}
