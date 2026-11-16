// Live ABI around the exact joint pipeline used by the deadline experiment.
#include "joint_pipeline.h"
namespace {
thread_local char errorText[512]{};
#ifdef NSP_JOINT_DIRECTML
constexpr const char* inferenceBackend="DirectML-Radeon";
#elif defined(NSP_JOINT_CPU)
constexpr const char* inferenceBackend="CPU-OpenVINO";
#else
constexpr const char* inferenceBackend="NPU-OpenVINO-shared-fixed";
#endif
struct Live {
 Joint pipeline;unsigned latency;uint64_t frame=0,epochStart=0;unsigned epoch=0;
 std::atomic<uint64_t>available{0},fallback{0},warmup{0},resets{0};
 std::atomic<int64_t>lastFallbackAgeUs{-1};
 Live(const wchar_t*model,const wchar_t*fs,unsigned delay):pipeline(model,fs),latency(delay){if(delay!=960)throw std::runtime_error("Joint live latency must be 960 frames");}
 uint64_t submit(const float*input,unsigned nextEpoch){
  if(!nextEpoch||nextEpoch>=0xffffff)return 0;
  if(epoch!=nextEpoch){epoch=nextEpoch;epochStart=frame;resets++;}
  const auto first=frame;frame+=B;
  return pipeline.submit(input,first,epoch)?first+1:0;
 }
 bool read(uint64_t source,unsigned sourceEpoch,float*out){
  bool ready=true;
  for(unsigned i=0;i<B;i++){auto&r=pipeline.result[(source+i)%R];auto expected=tag(sourceEpoch,source+i);
   if(r.stamp.load(std::memory_order_acquire)!=expected){ready=false;break;}
   for(unsigned c=0;c<12;c++)out[c*B+i]=r.pcm[c].load(std::memory_order_relaxed);
   if(r.stamp.load(std::memory_order_acquire)!=expected){ready=false;break;}
  }
  if(ready)available++;else{
   auto&input=pipeline.raw[source%R];
   lastFallbackAgeUs=input.stamp.load(std::memory_order_acquire)==tag(sourceEpoch,source)
       ? (nowNs()-input.submittedNs.load())/1000 : -1;
   std::fill_n(out,12*B,0.f);fallback++;
  }
  return ready;
 }
 bool ready(uint64_t source,unsigned sourceEpoch) const {
  const auto last=source+B-1;
  // The residual worker publishes source samples in order. Check both ends
  // to reject a stale ticket whose beginning was already recycled.
  return pipeline.result[last%R].stamp.load(std::memory_order_acquire)==tag(sourceEpoch,last)
      && pipeline.result[source%R].stamp.load(std::memory_order_acquire)==tag(sourceEpoch,source);
 }
 bool process(const float*input,float*out,unsigned nextEpoch){
  if(!nextEpoch||nextEpoch>=0xffffff){std::fill_n(out,12*B,0.f);return false;}
  const auto first=frame;
  submit(input,nextEpoch);
  if(first-epochStart>=latency)return read(first-latency,nextEpoch,out);
  std::fill_n(out,12*B,0.f);warmup++;return false;
 }
};
}
extern "C" __declspec(dllexport) void*nsp_joint_open(const wchar_t*model,const wchar_t*fs,unsigned latency){try{return new Live(model,fs,latency);}catch(const std::exception&e){snprintf(errorText,sizeof(errorText),"%s",e.what());return nullptr;}}
extern "C" __declspec(dllexport) const char*nsp_joint_error(){return errorText;}
#ifdef NSP_JOINT_DIRECTML
extern "C" __declspec(dllexport) int nsp_joint_uses_directml(){return 1;}
#endif
extern "C" __declspec(dllexport) int nsp_joint_uses_cpu(){
#ifdef NSP_JOINT_CPU
 return 1;
#else
 return 0;
#endif
}
extern "C" __declspec(dllexport) int nsp_joint_process(void*p,const float*input,float*out,unsigned n,unsigned epoch){if(!p||!input||!out||n!=B)return -1;return ((Live*)p)->process(input,out,epoch)?1:0;}
// Ticket is the exact source-frame origin plus one. Zero is a rejected input.
extern "C" __declspec(dllexport) uint64_t nsp_joint_submit(void*p,const float*input,unsigned n,unsigned epoch){
 if(!p||!input||n!=B)return 0;
 return static_cast<Live*>(p)->submit(input,epoch);
}
extern "C" __declspec(dllexport) int nsp_joint_read(void*p,uint64_t ticket,unsigned epoch,float*out,unsigned n){
 if(!p||!out||!ticket||!epoch||epoch>=0xffffff||n!=B)return -1;
 return static_cast<Live*>(p)->read(ticket-1,epoch,out)?1:0;
}
extern "C" __declspec(dllexport) int nsp_joint_ready(void*p,uint64_t ticket,unsigned epoch){
 if(!p||!ticket||!epoch||epoch>=0xffffff)return 0;
 return static_cast<Live*>(p)->ready(ticket-1,epoch)?1:0;
}
extern "C" __declspec(dllexport) int nsp_joint_set_ready_event(void*p,HANDLE event){
 if(!p||!event)return 0;
 HANDLE owned=nullptr;
 auto process=GetCurrentProcess();
 if(!DuplicateHandle(process,event,process,&owned,EVENT_MODIFY_STATE,FALSE,0))return 0;
 HANDLE empty=nullptr;
 if(!static_cast<Live*>(p)->pipeline.resultEvent.compare_exchange_strong(empty,owned)){
  CloseHandle(owned);
  return 0;
 }
 return 1;
}
extern "C" __declspec(dllexport) int nsp_joint_faulted(void*p){return p&&static_cast<Live*>(p)->pipeline.errors.load(std::memory_order_relaxed)!=0;}
extern "C" __declspec(dllexport) void nsp_joint_close(void*p){delete (Live*)p;}
extern "C" __declspec(dllexport) void nsp_joint_stats(void*p,char*out,unsigned size){if(!p||!out||!size)return;auto&s=*(Live*)p;auto&q=s.pipeline;auto hops=q.hops.load();
 snprintf(out,size,"{\"pipelineId\":1,\"candidateAvailable\":true,\"latencyFrames\":%u,\"latencyMs\":%.3f,\"upmixDelayMs\":0,\"srcFilterDelayFrames\":0,\"modelHops\":%llu,\"meanInferenceUs\":%.2f,\"maxInferenceUs\":%lld,\"availableBlocks\":%llu,\"fallbackBlocks\":%llu,\"warmupBlocks\":%llu,\"inputDrops\":%llu,\"errors\":%llu,\"resets\":%llu,\"postDrops\":%llu,\"queueHighWater\":%llu,\"handoffPcmCopiedBytes\":%llu,\"inferenceBackend\":\"%s\",\"residualBackend\":\"FreeSurround 480/240 joint\",\"experimentalDirect48\":true}",
 s.latency,s.latency/48.,(unsigned long long)hops,hops?double(q.totalUs.load())/hops:0.,(long long)q.maxUs.load(),(unsigned long long)s.available.load(),(unsigned long long)s.fallback.load(),(unsigned long long)s.warmup.load(),(unsigned long long)q.drops.load(),(unsigned long long)q.errors.load(),(unsigned long long)s.resets.load(),(unsigned long long)q.postDrops.load(),(unsigned long long)q.postHighWater.load(),(unsigned long long)q.handoffCopiedBytes.load(),inferenceBackend);
 std::string error;
 {
  std::lock_guard<std::mutex> lock(q.failureMutex);
  for(unsigned char c:q.failure){
   if(error.size()>=220)break;
   if(c=='"'||c=='\\')error+='\\';
   error+=(c>=32&&c<127)?char(c):' ';
  }
 }
#ifdef NSP_VOCAL_SHARED_OUTPUT
 const auto nonfinite=q.model.nonfiniteOutputHops();
#else
 const auto nonfinite=0;
#endif
 size_t len=strlen(out);if(len&&len<size)snprintf(out+len-1,size-len+1,",\"mmcssRegistered\":%s,\"postMmcssRegistered\":%s,\"inferenceEfficientCorePreferred\":%s,\"postEfficientCorePreferred\":%s,\"nonfiniteVocalHops\":%llu,\"lastFallbackAgeUs\":%lld,\"lastError\":\"%s\"}",q.inferenceMmcss.load()?"true":"false",q.postMmcss.load()?"true":"false",q.inferenceEfficientCore.load()?"true":"false",q.postEfficientCore.load()?"true":"false",(unsigned long long)nonfinite,(long long)s.lastFallbackAgeUs.load(),error.c_str());
}
