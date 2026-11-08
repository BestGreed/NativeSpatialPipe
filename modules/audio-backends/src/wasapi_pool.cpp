// SPDX-License-Identifier: Apache-2.0
// Bounded stereo shared/exclusive sink for Rust output-pool leases.
#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <endpointvolume.h>
#include <avrt.h>
#include <atomic>
#include <array>
#include <mutex>
#include <thread>
#include <condition_variable>
#include <cstring>
#include <cmath>
#include <cstdio>
#include <string>
#include <limits>
#include "wasapi_trace.h"
using Release = void (*)(void*);
struct Lease {const float* pcm{};UINT32 frames{},offset{};void* token{};Release release{};};
struct WasapiPool {
  std::wstring endpoint;
  bool exclusive=false;
  UINT32 requestedPeriodFrames=480,deviceFrames=0,deviceBits=32,validBits=32;
  bool deviceFloat=true;
  REFERENCE_TIME devicePeriod=0,streamLatency=0;
  std::array<float,8192> staging{};
  std::array<Lease,64> queue{};
  // The device clock may consume a lease later than the Rust pool can afford
  // to retain it (notably at preroll). One bounded native copy decouples them.
  std::array<std::array<float,8192>,64> storage{};
  size_t head=0,tail=0;
  std::mutex lock;
  std::condition_variable ready;
  std::condition_variable space;
  bool initialized=false;
  HANDLE stop=CreateEventW(nullptr,TRUE,FALSE,nullptr);
  std::thread worker;
  std::atomic<HRESULT> error{S_OK};
  std::atomic<UINT64> received{0},released{0},written{0},silence{0},blocks{0},overflow{0},highWater{0};
  std::atomic<UINT32> phase{0},stallPhase{0};
#ifdef NSP_WASAPI_TRACE
  wasapi_trace::Trace trace;
#endif
  // Only the host control thread uses these. The device writer never calls COM for volume.
  IAudioEndpointVolume* volume=nullptr;
  bool volumeInitialized=false, volumeCom=false;
  float volumeMaxDb=0;
  explicit WasapiPool(const wchar_t* id,bool x=false,UINT32 period=480):endpoint(id),exclusive(x),requestedPeriodFrames(period){
#ifdef NSP_WASAPI_TRACE
    trace.open();
#endif
  }
  ~WasapiPool(){if(stop)SetEvent(stop);if(worker.joinable())worker.join();if(stop)CloseHandle(stop);
    if(volume)volume->Release();if(volumeCom)CoUninitialize();}
  double hardwareVolumeDb(){
    const double unavailable=std::numeric_limits<double>::quiet_NaN();
    if(!volumeInitialized){
      volumeInitialized=true;
      const HRESULT co=CoInitializeEx(nullptr,COINIT_MULTITHREADED);
      volumeCom=SUCCEEDED(co);
      if(!volumeCom&&co!=RPC_E_CHANGED_MODE)return unavailable;
      IMMDeviceEnumerator* e=nullptr;
      IMMDevice* d=nullptr;
      IAudioEndpointVolume* candidate=nullptr;
      DWORD mask=0;
      float minimum=0,step=0;
      HRESULT hr=CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,IID_PPV_ARGS(&e));
      if(SUCCEEDED(hr))hr=e->GetDevice(endpoint.c_str(),&d);
      if(SUCCEEDED(hr))hr=d->Activate(__uuidof(IAudioEndpointVolume),CLSCTX_ALL,nullptr,(void**)&candidate);
      if(SUCCEEDED(hr))hr=candidate->QueryHardwareSupport(&mask);
      if(SUCCEEDED(hr)&&(mask&ENDPOINT_HARDWARE_SUPPORT_VOLUME)&&
          SUCCEEDED(candidate->GetVolumeRange(&minimum,&volumeMaxDb,&step)))volume=candidate;
      else if(candidate)candidate->Release();
      if(d)d->Release();if(e)e->Release();
    }
    float level=0;
    if(!volume||FAILED(volume->GetMasterVolumeLevel(&level))||!std::isfinite(level))return unavailable;
    return (std::min)(0.,double(level)-volumeMaxDb);
  }
  void discard(){std::lock_guard guard(lock);tail=head;space.notify_all();}
  void run(){
    HRESULT co=CoInitializeEx(nullptr,COINIT_MULTITHREADED),hr=co;
    IMMDeviceEnumerator* e=nullptr;IMMDevice* d=nullptr;IAudioClient* c=nullptr;IAudioRenderClient* r=nullptr;
    WAVEFORMATEX* f=nullptr;HANDLE event=CreateEventW(nullptr,FALSE,FALSE,nullptr),mmcss=nullptr;
    UINT32 capacity=0;bool started=false;
    if(SUCCEEDED(hr))hr=CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,IID_PPV_ARGS(&e));
    if(SUCCEEDED(hr))hr=e->GetDevice(endpoint.c_str(),&d);
    if(SUCCEEDED(hr))hr=d->Activate(__uuidof(IAudioClient),CLSCTX_ALL,nullptr,(void**)&c);
    WAVEFORMATEXTENSIBLE selected{};
    if(SUCCEEDED(hr)&&exclusive){
      REFERENCE_TIME minimum=0;hr=c->GetDevicePeriod(&devicePeriod,&minimum);
      if(SUCCEEDED(hr)){
        hr=AUDCLNT_E_UNSUPPORTED_FORMAT;
        const UINT32 bits[]={32,32,32,24,16},valid[]={32,24,32,24,16};
        for(UINT32 i=0;i<5;i++){
          WAVEFORMATEXTENSIBLE candidate{};auto& fmt=candidate.Format;
          fmt.wFormatTag=WAVE_FORMAT_EXTENSIBLE;fmt.nChannels=2;fmt.nSamplesPerSec=48000;
          fmt.wBitsPerSample=(WORD)bits[i];fmt.nBlockAlign=(WORD)(2*bits[i]/8);
          fmt.nAvgBytesPerSec=48000*fmt.nBlockAlign;fmt.cbSize=22;
          candidate.Samples.wValidBitsPerSample=(WORD)valid[i];candidate.dwChannelMask=3;
          candidate.SubFormat={(DWORD)(i==0?WAVE_FORMAT_IEEE_FLOAT:WAVE_FORMAT_PCM),0,0x10,{0x80,0,0,0xaa,0,0x38,0x9b,0x71}};
          if(c->IsFormatSupported(AUDCLNT_SHAREMODE_EXCLUSIVE,&fmt,nullptr)==S_OK){selected=candidate;deviceBits=bits[i];validBits=valid[i];deviceFloat=i==0;hr=S_OK;break;}
        }
      }
      if(SUCCEEDED(hr)){
        REFERENCE_TIME period=(std::max)(minimum,(REFERENCE_TIME)llround(requestedPeriodFrames*10000000.0/48000));
        hr=c->Initialize(AUDCLNT_SHAREMODE_EXCLUSIVE,AUDCLNT_STREAMFLAGS_EVENTCALLBACK,period,period,&selected.Format,nullptr);
        if(hr==AUDCLNT_E_BUFFER_SIZE_NOT_ALIGNED){
          UINT32 aligned=0;hr=c->GetBufferSize(&aligned);
          if(SUCCEEDED(hr)){
            period=(REFERENCE_TIME)llround(aligned*10000000.0/48000);
            c->Release();c=nullptr;
            hr=d->Activate(__uuidof(IAudioClient),CLSCTX_ALL,nullptr,(void**)&c);
            if(SUCCEEDED(hr))hr=c->Initialize(AUDCLNT_SHAREMODE_EXCLUSIVE,AUDCLNT_STREAMFLAGS_EVENTCALLBACK,period,period,&selected.Format,nullptr);
          }
        }
        devicePeriod=period;
      }
    }
    if(SUCCEEDED(hr)&&!exclusive)hr=c->GetMixFormat(&f);
    if(SUCCEEDED(hr)&&!exclusive){
      const bool floating=f->wFormatTag==WAVE_FORMAT_IEEE_FLOAT || (f->wFormatTag==WAVE_FORMAT_EXTENSIBLE && reinterpret_cast<WAVEFORMATEXTENSIBLE*>(f)->SubFormat.Data1==WAVE_FORMAT_IEEE_FLOAT);
      if(f->nChannels!=2||f->nSamplesPerSec!=48000||f->wBitsPerSample!=32||!floating)hr=AUDCLNT_E_UNSUPPORTED_FORMAT;
    }
    if(SUCCEEDED(hr)&&!exclusive)hr=c->Initialize(AUDCLNT_SHAREMODE_SHARED,AUDCLNT_STREAMFLAGS_EVENTCALLBACK,200000,0,f,nullptr);
    if(f)CoTaskMemFree(f);
    if(SUCCEEDED(hr))hr=c->SetEventHandle(event);
    if(SUCCEEDED(hr))hr=c->GetBufferSize(&capacity);
    if(SUCCEEDED(hr)){deviceFrames=capacity;if(capacity>4096)hr=AUDCLNT_E_BUFFER_SIZE_ERROR;else c->GetStreamLatency(&streamLatency);}
    if(SUCCEEDED(hr))hr=c->GetService(IID_PPV_ARGS(&r));
    if(SUCCEEDED(hr)){DWORD task=0;mmcss=AvSetMmThreadCharacteristicsW(L"Pro Audio",&task);if(!mmcss)hr=E_FAIL;}
#ifdef NSP_WASAPI_TRACE
    trace.mark(wasapi_trace::scheduling,3,mmcss!=nullptr,0);
    UINT64 deviceCursor=0;
#endif
    {std::lock_guard guard(lock);error=hr;initialized=true;}ready.notify_all();
    while(SUCCEEDED(hr)&&WaitForSingleObject(stop,0)!=WAIT_OBJECT_0){
      phase=1;
      if(!started){
        // Never sleep while holding the publication mutex: repeatedly taking
        // it at preroll can starve the first publisher and exhaust Rust's pool.
        std::unique_lock guard(lock);
        ready.wait_for(guard,std::chrono::milliseconds(20),[this]{return head-tail>=2||WaitForSingleObject(stop,0)==WAIT_OBJECT_0;});
        if(head-tail<2)continue;
      }
      if(started){
        phase=2;
        HANDLE waits[]{stop,event};DWORD wait=WaitForMultipleObjects(2,waits,FALSE,exclusive?1000:5);
        if(wait==WAIT_OBJECT_0)break;
        if(exclusive&&wait==WAIT_TIMEOUT){hr=HRESULT_FROM_WIN32(ERROR_TIMEOUT);break;}
        if(wait!=WAIT_OBJECT_0+1&&wait!=WAIT_TIMEOUT){hr=E_FAIL;break;}
      }
      phase=3;UINT32 padding=0;if(!exclusive)hr=c->GetCurrentPadding(&padding);if(FAILED(hr))break;
      UINT32 frames=capacity-padding;if(!frames)continue;
#ifdef NSP_WASAPI_TRACE
      trace.mark(wasapi_trace::device_request,deviceCursor,frames,0);
#endif
      phase=4;BYTE* bytes=nullptr;hr=r->GetBuffer(frames,&bytes);if(FAILED(hr))break;
      auto* output=deviceFloat?reinterpret_cast<float*>(bytes):staging.data();UINT32 at=0;
      {
        phase=5;
        std::lock_guard guard(lock);
        while(at<frames&&tail!=head){auto& b=queue[tail%queue.size()];UINT32 n=(std::min)(frames-at,b.frames-b.offset);
          std::memcpy(output+at*2,b.pcm+b.offset*2,n*2*sizeof(float));at+=n;b.offset+=n;
          if(b.offset==b.frames){tail++;space.notify_all();}
        }
      }
      if(at<frames){std::memset(output+at*2,0,(frames-at)*2*sizeof(float));silence+=frames-at;}
      if(!deviceFloat){
        for(UINT32 i=0;i<frames*2;i++){
          if(deviceBits==16){int16_t value=(int16_t)lrintf(output[i]*32767.f);memcpy(bytes+i*2,&value,2);}
          else if(validBits==24){
            int32_t value=(int32_t)llround((double)output[i]*8388607.);
            if(deviceBits==32){value=(int32_t)((int64_t)value*256);memcpy(bytes+i*4,&value,4);}
            else{uint32_t bits=(uint32_t)value;bytes[i*3]=(BYTE)bits;bytes[i*3+1]=(BYTE)(bits>>8);bytes[i*3+2]=(BYTE)(bits>>16);}
          }else{int32_t value=(int32_t)llround((double)output[i]*2147483647.);memcpy(bytes+i*4,&value,4);}
        }
      }
      phase=6;hr=r->ReleaseBuffer(frames,0);
#ifdef NSP_WASAPI_TRACE
      if(SUCCEEDED(hr))trace.mark(wasapi_trace::device_write,deviceCursor,written.load(),at);
      deviceCursor+=frames;
#endif
      if(SUCCEEDED(hr))written+=at;
      if(SUCCEEDED(hr)&&!started){hr=c->Start();started=SUCCEEDED(hr);}
    }
    if(started)c->Stop();error=hr;discard();
    if(r)r->Release();if(c)c->Release();if(d)d->Release();if(e)e->Release();
    if(event)CloseHandle(event);if(mmcss)AvRevertMmThreadCharacteristics(mmcss);if(SUCCEEDED(co))CoUninitialize();
  }
};
static std::atomic<HRESULT> lastOpenError{S_OK};
extern "C" __declspec(dllexport) void* nsp_wasapi_open_v2(const wchar_t* id,UINT32 exclusive,UINT32 periodFrames){
  if(!id||!*id||exclusive>1||periodFrames<144||periodFrames>1920){lastOpenError=E_INVALIDARG;return nullptr;}auto* p=new WasapiPool(id,exclusive!=0,periodFrames);if(!p->stop){lastOpenError=HRESULT_FROM_WIN32(GetLastError());delete p;return nullptr;}
  p->worker=std::thread([p]{p->run();});std::unique_lock guard(p->lock);
  bool ok=p->ready.wait_for(guard,std::chrono::seconds(5),[p]{return p->initialized;});
  guard.unlock();if(!ok||FAILED(p->error)){lastOpenError=ok?p->error.load():HRESULT_FROM_WIN32(ERROR_TIMEOUT);delete p;return nullptr;}lastOpenError=S_OK;return p;
}
extern "C" __declspec(dllexport) void* nsp_wasapi_open(const wchar_t* id){return nsp_wasapi_open_v2(id,0,480);}
extern "C" __declspec(dllexport) double nsp_wasapi_hardware_volume_db(void* context){
  return context?static_cast<WasapiPool*>(context)->hardwareVolumeDb():std::numeric_limits<double>::quiet_NaN();
}
extern "C" __declspec(dllexport) HRESULT nsp_wasapi_last_open_error(){return lastOpenError.load();}
extern "C" __declspec(dllexport) int nsp_wasapi_process(void*,UINT32 op,float*,UINT32 capacity,UINT32 channels,UINT32* frames){
  if(!frames||channels!=2)return -1;*frames=op==0?capacity:0;return 0;
}
extern "C" __declspec(dllexport) int nsp_wasapi_publish(void* context,[[maybe_unused]] UINT64 first,const float* pcm,UINT32 frames,UINT32 channels,UINT32,void* token,Release release){
  auto* p=static_cast<WasapiPool*>(context);if(!p||!pcm||!release||!token||channels!=2||!frames||frames>4096||FAILED(p->error))return -1;
  // Reject nonfinite/over-range output rather than silently clip or alter DSP.
  for(UINT32 i=0;i<frames*2;i++)if(!std::isfinite(pcm[i])||std::fabs(pcm[i])>1.f)return -2;
  std::unique_lock guard(p->lock);
  if(!p->space.wait_for(guard,std::chrono::milliseconds(1000),[p]{return p->head-p->tail<p->queue.size()||FAILED(p->error)||WaitForSingleObject(p->stop,0)==WAIT_OBJECT_0;})){
    p->stallPhase=p->phase.load();p->overflow++;return -3;
  }
  if(FAILED(p->error)||WaitForSingleObject(p->stop,0)==WAIT_OBJECT_0)return -1;
  size_t index=p->head%p->queue.size();std::memcpy(p->storage[index].data(),pcm,frames*2*sizeof(float));
  p->queue[index]={p->storage[index].data(),frames,0,nullptr,nullptr};p->head++;
  p->highWater=(std::max)(p->highWater.load(),UINT64(p->head-p->tail));
  p->received+=frames;p->blocks++;
#ifdef NSP_WASAPI_TRACE
  p->trace.mark(wasapi_trace::sink_publish,first,frames,p->received.load()-p->written.load());
#endif
  guard.unlock();p->ready.notify_one();release(token);p->released++;return 0;
}
#ifdef NSP_WASAPI_TRACE
extern "C" __declspec(dllexport) void nsp_wasapi_trace_mark(void* context,UINT32 kind,UINT64 sequence,UINT64 a,UINT64 b){
  static_cast<WasapiPool*>(context)->trace.mark(kind,sequence,a,b);
}
#endif
extern "C" __declspec(dllexport) void nsp_wasapi_stats(void* context,char* out,UINT32 capacity){
  if(!context||!out||!capacity)return;auto* p=static_cast<WasapiPool*>(context);
  snprintf(out,capacity,"{\"mode\":\"%s\",\"deviceBits\":%u,\"validBits\":%u,\"deviceFloat\":%s,\"deviceBufferFrames\":%u,\"devicePeriodMs\":%.6f,\"reportedStreamLatencyMs\":%.6f,\"receivedFrames\":%llu,\"writtenFrames\":%llu,\"releasedBlocks\":%llu,\"blocks\":%llu,\"silenceFrames\":%llu,\"overflow\":%llu,\"queueHighWater\":%llu,\"nativeCopiesBeforeWasapi\":1,\"phase\":%u,\"stallPhase\":%u,\"hr\":%ld}",p->exclusive?"exclusive":"shared",p->deviceBits,p->validBits,p->deviceFloat?"true":"false",p->deviceFrames,p->devicePeriod/10000.,p->streamLatency/10000.,p->received.load(),p->written.load(),p->released.load(),p->blocks.load(),p->silence.load(),p->overflow.load(),p->highWater.load(),p->phase.load(),p->stallPhase.load(),(long)p->error.load());
}
extern "C" __declspec(dllexport) void nsp_wasapi_close(void* context){delete static_cast<WasapiPool*>(context);}
extern "C" __declspec(dllexport) UINT32 nsp_wasapi_abi_version(){return 1;}
extern "C" __declspec(dllexport) HRESULT nsp_wasapi_error(void* context){return context?static_cast<WasapiPool*>(context)->error.load():E_POINTER;}
extern "C" __declspec(dllexport) int nsp_wasapi_drain(void* context,UINT32 timeout){
  if(!context||!timeout)return -1;auto* p=static_cast<WasapiPool*>(context);
  std::unique_lock guard(p->lock);
  if(!p->space.wait_for(guard,std::chrono::milliseconds(timeout),[p]{return p->head==p->tail||FAILED(p->error);}))return -2;
  bool failed=FAILED(p->error);guard.unlock();
  if(!failed&&p->exclusive)Sleep((DWORD)ceil(p->deviceFrames*2000./48000));
  return failed?-3:0;
}
