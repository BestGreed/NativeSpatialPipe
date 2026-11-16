#define NOMINMAX
#include <windows.h>
#include <avrt.h>
#include "vocal_model.h"
#include "StemgenRT/StreamingSampleRateAdapter.h"
#include <atomic>
#include <thread>
#include <cstdio>
#include <chrono>
#include <mutex>
#include <xmmintrin.h>

namespace {
constexpr unsigned N=480,INPUTS=32,OUTPUTS=32768;
struct Input {std::atomic<unsigned>ready{0};uint32_t epoch=0;uint64_t frame=0;std::array<float,2*N>pcm{};};
struct Result {std::atomic<uint64_t>stamp{0};std::atomic<float>l{0},r{0};};
uint64_t stamp(uint32_t epoch,uint64_t frame){return ((uint64_t)(epoch&0xffffff)<<40)|(frame+1);}
struct Separator {
 VocalModel model;audio_plugin::StreamingSampleRateAdapter down,up;
 HANDLE event=CreateEventW(nullptr,FALSE,FALSE,nullptr);std::atomic<bool>stop{false};std::thread worker;
 std::array<Input,INPUTS>inputs;std::array<Result,OUTPUTS>outputs;
 std::atomic<uint32_t>liveEpoch{1};uint32_t producerEpoch=0;uint64_t producerFrame=0,write=0;
 std::mutex diagnosticMutex;std::string lastError,lastStage;uint64_t lastErrorFrame=0;uint32_t lastErrorEpoch=0;unsigned workerMxcsr=0,errorMxcsr=0;
 uint32_t latency;double filterDelay=0;int64_t filterDelayFrames=0;
 std::atomic<unsigned>mmcssRegistered{0},mmcssHigh{0},mmcssError{0};
 std::atomic<uint64_t>hops{0},errors{0},dropped{0},available{0},fallback{0},warmup{0},resets{0},totalUs{0},maxUs{0},queueMax{0},read{0};
 explicit Separator(const wchar_t*path,uint32_t delay):model(path),latency(delay){
  if(!event||delay<960||delay>9600||delay%N)throw std::runtime_error("Separator delay/event contract");
  if(!down.prepare(48000,44100,2)||!up.prepare(44100,48000,2))throw std::runtime_error("SRC prepare");
  double raw=down.filterGroupDelayInputSamples()+up.filterGroupDelayOutputSamples();double extra=std::ceil(raw)-raw;
  if(extra>1e-8&&!up.prepare(44100,48000,2,extra))throw std::runtime_error("SRC fractional alignment");
  filterDelay=down.filterGroupDelayInputSamples()+up.filterGroupDelayOutputSamples();filterDelayFrames=llround(filterDelay);
  if(std::abs(filterDelay-filterDelayFrames)>1e-5||filterDelayFrames+480>delay)throw std::runtime_error("SRC/latency alignment");
  worker=std::thread([this]{run();});
 }
 void run(){
  SetThreadDescription(GetCurrentThread(),L"NSp vocal inference");DWORD taskIndex=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Audio",&taskIndex);mmcssRegistered=task?1:0;if(task){mmcssHigh=AvSetMmThreadPriority(task,AVRT_PRIORITY_HIGH)?1:0;if(!mmcssHigh)mmcssError=GetLastError();}else{mmcssError=GetLastError();SetThreadPriority(GetCurrentThread(),THREAD_PRIORITY_ABOVE_NORMAL);}
  workerMxcsr=_mm_getcsr();
  uint64_t index=0,expected=0,origin=0,upIndex=0;uint32_t epoch=0,fill=0;bool valid=false;
  std::array<float,2048>dl{},dr{};std::array<float,256>chunk{};std::array<float,512>ul{},ur{};
  while(!stop.load(std::memory_order_acquire)){
   auto&slot=inputs[index%INPUTS];if(!slot.ready.load(std::memory_order_acquire)){WaitForSingleObject(event,5);continue;}
   if(slot.epoch!=liveEpoch.load(std::memory_order_acquire)){slot.ready.store(0,std::memory_order_release);read.store(++index);continue;}
   const char* stage="reset";
   try{
    if(epoch!=slot.epoch||expected!=slot.frame){epoch=slot.epoch;origin=slot.frame;expected=slot.frame;model.reset();down.reset();up.reset();fill=0;upIndex=0;valid=false;resets++;}
    expected=slot.frame+N;const float*in[]{slot.pcm.data(),slot.pcm.data()+N};float*out[]{dl.data(),dr.data()};
    stage="downsample";auto converted=down.process(in,N,out,dl.size());if(!converted.ok||converted.inputConsumed!=N)throw std::runtime_error("Downsample contract");
    for(size_t f=0;f<converted.outputProduced;f++){
     chunk[fill]=dl[f];chunk[128+fill]=dr[f];if(++fill<128)continue;fill=0;
     stage="inference";auto begin=std::chrono::steady_clock::now();const float*v=model.process(chunk.data());auto elapsed=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-begin).count();hops++;totalUs+=elapsed;uint64_t prior=maxUs.load();while((uint64_t)elapsed>prior&&!maxUs.compare_exchange_weak(prior,elapsed)){}
     if(!valid){valid=true;continue;}const float*vin[]{v,v+128};float*vout[]{ul.data(),ur.data()};stage="upsample";auto restored=up.process(vin,128,vout,ul.size());if(!restored.ok)throw std::runtime_error("Upsample contract");
     for(size_t i=0;i<restored.outputProduced;i++,upIndex++){
      int64_t local=(int64_t)upIndex-filterDelayFrames;if(local<0)continue;uint64_t frame=origin+(uint64_t)local;auto&sample=outputs[frame%OUTPUTS];sample.stamp.store(0,std::memory_order_release);sample.l.store(ul[i],std::memory_order_relaxed);sample.r.store(ur[i],std::memory_order_relaxed);sample.stamp.store(stamp(epoch,frame),std::memory_order_release);
     }
    }
   }catch(const std::exception& e){auto count=errors.fetch_add(1);{std::lock_guard<std::mutex> lock(diagnosticMutex);lastError=e.what();lastError.resize(std::min<size_t>(lastError.size(),500));lastStage=stage;lastErrorFrame=slot.frame;lastErrorEpoch=slot.epoch;errorMxcsr=_mm_getcsr();}if(count<3)fprintf(stderr,"NSp vocal %s error at source frame %llu: %s\n",VocalModel::backend(),(unsigned long long)slot.frame,e.what());epoch=0;}catch(...){errors++;epoch=0;}
   slot.ready.store(0,std::memory_order_release);read.store(++index,std::memory_order_release);
  }if(task)AvRevertMmThreadCharacteristics(task);
 }
 bool process(const float*pcm,float*vocal,uint32_t epoch){
  if(epoch==0||epoch>=0xffffff)return false;
  if(epoch!=producerEpoch){producerEpoch=epoch;producerFrame=0;liveEpoch.store(epoch,std::memory_order_release);}
  uint64_t frame=producerFrame;producerFrame+=N;auto&slot=inputs[write%INPUTS];
  if(!slot.ready.load(std::memory_order_acquire)){slot.epoch=epoch;slot.frame=frame;std::copy_n(pcm,2*N,slot.pcm.begin());slot.ready.store(1,std::memory_order_release);write++;uint64_t queue=write-read.load(std::memory_order_acquire),prior=queueMax.load();while(queue>prior&&!queueMax.compare_exchange_weak(prior,queue)){}SetEvent(event);}else dropped++;
  bool ok=frame>=latency;if(ok){uint64_t start=frame-latency;for(unsigned i=0;i<N;i++){auto&s=outputs[(start+i)%OUTPUTS];uint64_t expected=stamp(epoch,start+i);if(s.stamp.load(std::memory_order_acquire)!=expected){ok=false;break;}vocal[i]=s.l.load(std::memory_order_relaxed);vocal[N+i]=s.r.load(std::memory_order_relaxed);if(s.stamp.load(std::memory_order_acquire)!=expected){ok=false;break;}}}
  if(!ok){std::fill_n(vocal,2*N,0.f);if(frame<latency)warmup++;else fallback++;}else available++;
  return ok;
 }
 ~Separator(){stop.store(true,std::memory_order_release);SetEvent(event);if(worker.joinable())worker.join();CloseHandle(event);}
};
thread_local char errorText[512]{};
}
extern "C" __declspec(dllexport) void*nsp_vocal_open(const wchar_t*model,uint32_t latencyFrames){try{return new Separator(model,latencyFrames);}catch(const std::exception&e){snprintf(errorText,sizeof(errorText),"%s",e.what());return nullptr;}}
extern "C" __declspec(dllexport) const char*nsp_vocal_error(){return errorText;}
extern "C" __declspec(dllexport) int nsp_vocal_process(void*p,const float*pcm,float*vocal,uint32_t n,uint32_t epoch){if(!p||!pcm||!vocal||n!=N)return -1;return ((Separator*)p)->process(pcm,vocal,epoch)?1:0;}
extern "C" __declspec(dllexport) void nsp_vocal_stats(void*p,char*out,uint32_t size){if(!p||!out||!size)return;auto&s=*(Separator*)p;auto count=s.hops.load();snprintf(out,size,"{\"latencyFrames\":%u,\"latencyMs\":%.3f,\"srcFilterDelayFrames\":%lld,\"modelHops\":%llu,\"meanInferenceUs\":%.2f,\"maxInferenceUs\":%llu,\"availableBlocks\":%llu,\"fallbackBlocks\":%llu,\"warmupBlocks\":%llu,\"inputDrops\":%llu,\"errors\":%llu,\"resets\":%llu,\"queueHighWater\":%llu}",s.latency,s.latency/48.,(long long)s.filterDelayFrames,(unsigned long long)count,count?(double)s.totalUs.load()/count:0.,(unsigned long long)s.maxUs.load(),(unsigned long long)s.available.load(),(unsigned long long)s.fallback.load(),(unsigned long long)s.warmup.load(),(unsigned long long)s.dropped.load(),(unsigned long long)s.errors.load(),(unsigned long long)s.resets.load(),(unsigned long long)s.queueMax.load());size_t len=strlen(out);if(len&&len<size)snprintf(out+len-1,size-len+1,",\"mmcssRegistered\":%s,\"mmcssHigh\":%s,\"mmcssError\":%u}",s.mmcssRegistered.load()?"true":"false",s.mmcssHigh.load()?"true":"false",s.mmcssError.load());len=strlen(out);if(len&&len<size)snprintf(out+len-1,size-len+1,",\"inferenceBackend\":\"%s\"}",VocalModel::backend());std::lock_guard<std::mutex> lock(s.diagnosticMutex);std::string escaped;for(unsigned char c:s.lastError){if(c=='"'||c=='\\'){escaped+='\\';escaped+=c;}else if(c=='\n')escaped+="\\n";else if(c>=32)escaped+=c;}len=strlen(out);if(len&&len<size)snprintf(out+len-1,size-len+1,",\"workerMxcsr\":%u,\"lastError\":{\"message\":\"%s\",\"stage\":\"%s\",\"frame\":%llu,\"epoch\":%u,\"mxcsr\":%u}}",s.workerMxcsr,escaped.c_str(),s.lastStage.c_str(),(unsigned long long)s.lastErrorFrame,s.lastErrorEpoch,s.errorMxcsr);}
extern "C" __declspec(dllexport) void nsp_vocal_close(void*p){delete (Separator*)p;}
