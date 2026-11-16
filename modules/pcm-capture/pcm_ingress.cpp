// Native middle-layer adapter: canonical interleaved PCM -> renderer bed packets.
#define NOMINMAX
#include "pcm_ring.h"
#include <array>
#include <atomic>
#include <thread>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <avrt.h>
using Submit=int(*)(void*,uint64_t,uint32_t,const unsigned char*,size_t);
using SubmitLeased=int(*)(void*,uint64_t,uint32_t,const unsigned char*,size_t,uint64_t,uint64_t);
struct PcmIngress {
 HANDLE mapping=nullptr,event=nullptr,stop=CreateEventW(nullptr,TRUE,FALSE,nullptr);PcmRing*ring=nullptr;
 void*pipeline=nullptr;Submit submit=nullptr;SubmitLeased submitLeased=nullptr;std::thread worker;
 std::atomic<uint64_t>received{0},submitted{0},frames{0},gaps{0},unsupported{0};std::atomic<int>error{0};
 std::array<unsigned char,8*(32+480*4)> packet{};
 uint32_t fill=0,channels=0,mask=0,generation=0;uint64_t stream=0,expected=0,lease=0;
 std::atomic<uint32_t>browserPid{0},tabId{0},windowId{0},bindingGeneration{0};std::atomic<uint64_t>sourceKeyHi{0},sourceKeyLo{0};
 static uint32_t bed_type(uint32_t bit){switch(bit){case 1:return 2;case 2:return 4;case 4:return 8;case 8:return 16;case 16:return 128;case 32:return 256;case 512:return 32;case 1024:return 64;default:return 0;}}
 bool consume(const PcmDescriptor&d,const float*pcm){
  if(d.size!=sizeof(d)||d.sampleRate!=48000||!d.channels||d.channels>8||!d.frames||d.frames>4096||__builtin_popcount(d.channelMask)!=(int)d.channels){unsupported++;return true;}
  uint32_t types[8]{},count=0;for(uint32_t i=0;i<32;i++)if(d.channelMask&(1u<<i)){uint32_t type=bed_type(1u<<i);if(!type){unsupported++;return true;}types[count++]=type;}
  for(uint32_t i=0;i<d.frames*d.channels;i++)if(!std::isfinite(pcm[i])){unsupported++;return true;}
  bool sourceChanged=lease==0||stream!=d.streamId||generation!=d.generation||channels!=d.channels||mask!=d.channelMask;
  if(sourceChanged)lease++;
  bool discontinuity=sourceChanged||expected!=d.firstSourceFrame;
  if(discontinuity){fill=0;gaps++;stream=d.streamId;generation=d.generation;channels=d.channels;mask=d.channelMask;}
  expected=d.firstSourceFrame+d.frames;
  browserPid=d.browser.browserPid;tabId=d.browser.tabId;windowId=d.browser.windowId;bindingGeneration=d.browser.generation;sourceKeyHi=d.browser.sourceKeyHi;sourceKeyLo=d.browser.sourceKeyLo;
  for(uint32_t frame=0;frame<d.frames;frame++){
   for(uint32_t channel=0;channel<channels;channel++){
    auto* out=packet.data()+channels*32+(channel*480+fill)*sizeof(float);
    memcpy(out,pcm+frame*channels+channel,sizeof(float));
   }
   fill++;
   if(fill==480){
    for(uint32_t channel=0;channel<channels;channel++){
     uint32_t identity=((uint32_t)gaps.load()&0xffffffu)<<8|channel+1;
     uint32_t property[8]={identity,480,types[channel],5,0,0,0,0x3f800000};memcpy(packet.data()+channel*32,property,32);
    }
    const size_t size=channels*(32+480*4);
    int code=submitLeased?submitLeased(pipeline,submitted.load(),channels,packet.data(),size,0,lease)
                         :submit(pipeline,submitted.load(),channels,packet.data(),size);
    if(code){error=code;return false;}submitted++;frames+=480;fill=0;
   }
  }return true;
 }
 void run(){
  DWORD taskIndex=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Pro Audio",&taskIndex);if(task)AvSetMmThreadPriority(task,AVRT_PRIORITY_NORMAL);
  uint32_t index=0;
  while(true){
   auto&slot=ring->blocks[index%PCM_SLOTS];
   if(InterlockedCompareExchange(&slot.ready,0,0)==1){received++;bool ok=consume(slot.descriptor,slot.pcm);InterlockedExchange(&slot.ready,0);index++;if(!ok)break;continue;}
   if(WaitForSingleObject(stop,0)==WAIT_OBJECT_0)break;
   HANDLE waits[]{stop,event};if(WaitForMultipleObjects(2,waits,FALSE,1000)==WAIT_OBJECT_0)break;
  }if(task)AvRevertMmThreadCharacteristics(task);
 }
 ~PcmIngress(){if(stop)SetEvent(stop);if(worker.joinable())worker.join();if(ring)UnmapViewOfFile(ring);if(mapping)CloseHandle(mapping);if(event)CloseHandle(event);if(stop)CloseHandle(stop);}
};
static void*open_ingress(const wchar_t*name,const wchar_t*event,void*pipeline,Submit submit,SubmitLeased leased){
 if(!name||!event||!pipeline||(!submit&&!leased))return nullptr;auto*p=new PcmIngress;p->pipeline=pipeline;p->submit=submit;p->submitLeased=leased;
 p->mapping=OpenFileMappingW(FILE_MAP_READ|FILE_MAP_WRITE,FALSE,name);p->event=OpenEventW(SYNCHRONIZE,FALSE,event);
 if(!p->mapping||!p->event){delete p;return nullptr;}p->ring=(PcmRing*)MapViewOfFile(p->mapping,FILE_MAP_READ|FILE_MAP_WRITE,0,0,sizeof(PcmRing));
 if(!p->ring||p->ring->magic!=PCM_MAGIC||p->ring->version!=2||p->ring->slots!=PCM_SLOTS||p->ring->slotBytes!=sizeof(PcmSlot)){delete p;return nullptr;}
 p->worker=std::thread([p]{p->run();});return p;
}
extern "C" __declspec(dllexport) void*nsp_pcm_ingress_open(const wchar_t*name,const wchar_t*event,void*pipeline,Submit submit){
 return open_ingress(name,event,pipeline,submit,nullptr);
}
extern "C" __declspec(dllexport) void*nsp_pcm_ingress_open_leased(const wchar_t*name,const wchar_t*event,void*pipeline,SubmitLeased submit){
 return open_ingress(name,event,pipeline,nullptr,submit);
}
extern "C" __declspec(dllexport) void nsp_pcm_ingress_stats(void*ctx,char*out,uint32_t n){if(!ctx||!out)return;auto*p=(PcmIngress*)ctx;snprintf(out,n,"{\"receivedPackets\":%llu,\"submittedBlocks\":%llu,\"submittedFrames\":%llu,\"discontinuities\":%llu,\"unsupported\":%llu,\"error\":%d,\"browserPid\":%u,\"tabId\":%u,\"windowId\":%u,\"bindingGeneration\":%u,\"sourceKeyHi\":\"%016llx\",\"sourceKeyLo\":\"%016llx\",\"presentationClockSynchronized\":false,\"outputPolicy\":\"host-configured render input\"}",p->received.load(),p->submitted.load(),p->frames.load(),p->gaps.load(),p->unsupported.load(),p->error.load(),p->browserPid.load(),p->tabId.load(),p->windowId.load(),p->bindingGeneration.load(),p->sourceKeyHi.load(),p->sourceKeyLo.load());}
extern "C" __declspec(dllexport) void nsp_pcm_ingress_close(void*ctx){delete (PcmIngress*)ctx;}
