#pragma once
#include "../browser-identity/registry.h"
#include <vector>
#include <mutex>
#include <thread>
#include <atomic>
#include <string>
#include <map>
struct BrowserIdentityReader {
 std::wstring path;uint64_t leaseNs=500000000;std::thread worker;HANDLE stop=CreateEventW(nullptr,TRUE,FALSE,nullptr);std::mutex lock;std::vector<BrowserIdentityRecord>records;
 std::atomic<uint64_t>updates{0},rejected{0};
 static uint64_t birth(HANDLE p){FILETIME c,e,k,u;if(!GetProcessTimes(p,&c,&e,&k,&u))return 0;ULARGE_INTEGER n;n.LowPart=c.dwLowDateTime;n.HighPart=c.dwHighDateTime;return n.QuadPart;}
 bool start(const wchar_t*file,uint32_t leaseMs){if(worker.joinable()||!file||!*file||leaseMs<100||leaseMs>5000)return false;path=file;leaseNs=(uint64_t)leaseMs*1000000;worker=std::thread([this]{run();});return true;}
 void run(){std::vector<BYTE>data(32+128*96);std::map<uint32_t,std::pair<HANDLE,uint64_t>>processes;
  while(WaitForSingleObject(stop,0)!=WAIT_OBJECT_0){std::vector<BrowserIdentityRecord>next;HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
   if(file!=INVALID_HANDLE_VALUE){DWORD size=GetFileSize(file,nullptr),got=0;if(size>=32&&size<=data.size()&&ReadFile(file,data.data(),size,&got,nullptr)&&got==size){auto*h=(BrowserIdentityHeader*)data.data();uint64_t now=pcm_qpc_ns();
     if(h->magic==BROWSER_IDENTITY_MAGIC&&h->version==BROWSER_IDENTITY_VERSION&&h->count<=128&&h->recordSize==sizeof(BrowserIdentityRecord)&&size==32+h->count*sizeof(BrowserIdentityRecord)&&h->publishedQpcNs<=now&&now-h->publishedQpcNs<=leaseNs){
      auto*r=(BrowserIdentityRecord*)(data.data()+32);for(uint32_t i=0;i<h->count;i++){auto&v=r[i];if(!v.sourcePid||!v.tag.tabId||!v.tag.generation||!v.tag.browserPid||(!v.tag.sourceKeyHi&&!v.tag.sourceKeyLo)||v.validatedQpcNs>now||now-v.validatedQpcNs>leaseNs)continue;
       auto it=processes.find(v.sourcePid);if(it==processes.end()||WaitForSingleObject(it->second.first,0)==WAIT_OBJECT_0){if(it!=processes.end()){CloseHandle(it->second.first);processes.erase(it);}HANDLE p=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,v.sourcePid);if(p)it=processes.emplace(v.sourcePid,std::make_pair(p,birth(p))).first;else continue;}
       if(it->second.second==v.sourceStarted)next.push_back(v);
      }updates++;
     }else rejected++;
    }else rejected++;CloseHandle(file);
   }{std::lock_guard guard(lock);records=std::move(next);}WaitForSingleObject(stop,50);
  }for(auto&[pid,p]:processes)CloseHandle(p.first);
 }
 BrowserIdentityTag lookup(uint32_t host,uint32_t source,uint64_t queue,uint64_t mapping,uint64_t control){BrowserIdentityTag result{};unsigned matches=0;uint64_t now=pcm_qpc_ns();std::lock_guard guard(lock);
  for(auto&r:records)if(r.hostPid==host&&r.sourcePid==source&&r.queue==queue&&r.mapping==mapping&&r.control==control&&r.validatedQpcNs<=now&&now-r.validatedQpcNs<=leaseNs){result=r.tag;matches++;}return matches==1?result:BrowserIdentityTag{};
 }
 ~BrowserIdentityReader(){SetEvent(stop);if(worker.joinable())worker.join();CloseHandle(stop);}
};
