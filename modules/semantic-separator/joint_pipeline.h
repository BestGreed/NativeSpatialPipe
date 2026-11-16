#pragma once
// Isolated experiment: early publication -> matched residual -> half-hop FS.
// Never opens an audio device or changes the running playback configuration.
#define NOMINMAX
#include <windows.h>
#include <avrt.h>
#include "vocal_model.h"
#include <filesystem>
#include <fstream>
#include "StemgenRT/StreamingSampleRateAdapter.h"
#include <atomic>
#include <thread>
#include <memory>
#include <fstream>
#include <iostream>
#include <chrono>
#include <algorithm>
#include <mutex>
#ifndef NSP_JOINT_BLOCK
#define NSP_JOINT_BLOCK 120
#endif
constexpr unsigned B=NSP_JOINT_BLOCK,Q=128,R=32768;
#ifdef NSP_JOINT_LIVE
constexpr bool LiveJoint=true;
#else
constexpr bool LiveJoint=false;
#endif
using Clock=std::chrono::steady_clock;
// A preference lets Windows move the thread when the efficient core is busy.
static bool prefer_efficient_core(unsigned worker) {
 try {
  auto kernel=GetModuleHandleW(L"kernel32.dll");
  using ReadTopology=BOOL(WINAPI*)(PSYSTEM_CPU_SET_INFORMATION,ULONG,PULONG,HANDLE,ULONG);
  using ReadDefault=BOOL(WINAPI*)(HANDLE,PULONG,ULONG,PULONG);
  auto readTopology=reinterpret_cast<ReadTopology>(GetProcAddress(kernel,"GetSystemCpuSetInformation"));
  auto readDefault=reinterpret_cast<ReadDefault>(GetProcAddress(kernel,"GetProcessDefaultCpuSets"));
  if(!readTopology||!readDefault)return false;
  DWORD bytes=0;
  readTopology(nullptr,0,&bytes,GetCurrentProcess(),0);
  if(!bytes)return false;
  std::vector<unsigned char> topology(bytes);
  if(!readTopology(reinterpret_cast<PSYSTEM_CPU_SET_INFORMATION>(topology.data()),bytes,&bytes,GetCurrentProcess(),0))return false;
  GROUP_AFFINITY allowed{};
  if(!GetThreadGroupAffinity(GetCurrentThread(),&allowed))return false;
  ULONG assigned=0;
  readDefault(GetCurrentProcess(),nullptr,0,&assigned);
  if(assigned)return false;
  std::vector<PROCESSOR_NUMBER> efficient;
  unsigned lowest=256,highest=0;
  for(DWORD at=0;at<bytes;){
   const auto* row=reinterpret_cast<const SYSTEM_CPU_SET_INFORMATION*>(topology.data()+at);
   if(!row->Size||row->Size>bytes-at)return false;
   at+=row->Size;
   if(row->Type!=CpuSetInformation)continue;
   const auto& cpu=row->CpuSet;
   if(cpu.Group!=allowed.Group||!(allowed.Mask&(KAFFINITY(1)<<cpu.LogicalProcessorIndex))||
      cpu.Parked||(cpu.Allocated&&!cpu.AllocatedToTargetProcess)||cpu.RealTime)continue;
   highest=std::max(highest,unsigned(cpu.EfficiencyClass));
   if(cpu.EfficiencyClass<lowest){lowest=cpu.EfficiencyClass;efficient.clear();}
   if(cpu.EfficiencyClass==lowest)efficient.push_back({cpu.Group,cpu.LogicalProcessorIndex,0});
  }
  if(efficient.empty()||lowest==highest)return false;
  auto& preferred=efficient[worker%efficient.size()];
  return SetThreadIdealProcessorEx(GetCurrentThread(),&preferred,nullptr)!=FALSE;
 }catch(const std::bad_alloc&){return false;}
}
struct Input {std::atomic<bool> ready{false};unsigned epoch=0;uint64_t frame=0;int64_t submittedNs=0;std::array<float,B*2> pcm{};};
struct Raw {std::atomic<uint64_t> stamp{0};std::atomic<float> l{0},r{0},v{0},w{0};std::atomic<int64_t> submittedNs{0},vocalReadyNs{0};};
struct Output {std::atomic<uint64_t> stamp{0};std::array<std::atomic<float>,12> pcm;std::atomic<int64_t> readyNs{0};};
struct PublishedRange {uint64_t first=0;unsigned frames=0;int64_t readyNs=0;};
struct HopTrace {uint64_t id=0,inputFirst=0;int64_t submitted=0,workerBegin=0,inferBegin=0,inferEnd=0;};
struct PublicationTrace {uint64_t first=0,hop=0;unsigned frames=0;int64_t fsBegin=0,fsEnd=0,ready=0;};
struct VocalJob {std::atomic<bool>ready{false};uint64_t first=0;unsigned epoch=0,frames=0;int64_t queuedNs=0;bool steady=false;const float*sharedL=nullptr,*sharedR=nullptr;int lease=-1;std::array<float,512>l{},r{};};
static uint64_t tag(unsigned epoch,uint64_t f){return (uint64_t(epoch)<<48)|(f+1);}
static int64_t nowNs(){return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();}
static uint64_t cpuTicks(bool thread=false){FILETIME created{},exited{},kernel{},user{};BOOL ok=thread?GetThreadTimes(GetCurrentThread(),&created,&exited,&kernel,&user):GetProcessTimes(GetCurrentProcess(),&created,&exited,&kernel,&user);if(!ok)throw std::runtime_error("CPU accounting failed");return (uint64_t(kernel.dwHighDateTime)<<32)+kernel.dwLowDateTime+(uint64_t(user.dwHighDateTime)<<32)+user.dwLowDateTime;}
struct Metric {
 std::vector<double> samples;
 Metric(){if(!LiveJoint)samples.reserve(50000);}
 void add(double v){if(!LiveJoint)samples.push_back(v);}
 void json(const char*name){std::sort(samples.begin(),samples.end());double sum=0;for(double x:samples)sum+=x;auto p=[&](double f){return samples.empty()?0.:samples[std::min(samples.size()-1,size_t(f*samples.size()))];};std::cout<<"\""<<name<<"\":{\"count\":"<<samples.size()<<",\"mean\":"<<(samples.empty()?0.:sum/samples.size())<<",\"p50\":"<<p(.5)<<",\"p90\":"<<p(.9)<<",\"p99\":"<<p(.99)<<",\"p999\":"<<p(.999)<<",\"max\":"<<p(1)<<"}";}
};
struct Joint {
 VocalModel model;audio_plugin::StreamingSampleRateAdapter down,up;
 std::array<Input,Q> queue;std::array<Raw,R> raw;std::array<Output,R> result;
 std::atomic<bool> stop{false};std::atomic<uint64_t> errors{0},drops{0},hops{0},processed{0};
 std::atomic<int64_t> totalUs{0},maxUs{0};std::atomic<double> reconstruction{0};
 std::atomic<int64_t> originNs{0};std::vector<double> readyAges;
 Metric queueUs,downUs,inferUs,upUs,residualUs,fsUs,publishUs;
 Metric separationMs,fsFutureWaitMs,fsTraceMs,publishTraceMs,totalTraceMs;
 Metric firstResidualMs,lastResidualMs,residualSpanMs,firstFsMs,fsSpanMs;
 Metric postDispatchUs;std::array<VocalJob,64>postQueue;
 Metric inPacketInferenceGapUs;
 std::atomic<bool>inferenceMmcss{false},postMmcss{false},inferenceEfficientCore{false},postEfficientCore{false};std::atomic<bool>postStop{false};std::thread postWorker;HANDLE postEvent=CreateEventW(nullptr,FALSE,FALSE,nullptr);
 std::atomic<HANDLE> resultEvent{nullptr};
 std::atomic<uint64_t>postRead{0},postHighWater{0},postDrops{0};uint64_t postWrite=0;
 std::atomic<uint64_t> handoffCopiedBytes{0};
 unsigned postEpoch=0;uint64_t postExpected=0;std::mutex failureMutex;
 struct Scratch {std::array<float,1024>residual{};std::array<float,6144>decoded{},referenceDecoded{};}scratch;
 std::vector<PublishedRange> publications;uint64_t nextMatureFrame=0,postOrigin=0;
 bool traceEnabled=false;uint64_t activeTraceHop=0;std::vector<HopTrace>hopTraces;std::vector<PublicationTrace>publicationTraces;
 HANDLE event=CreateEventW(nullptr,FALSE,FALSE,nullptr);std::thread worker;
 HMODULE fsModule=nullptr;void* fs=nullptr;
 using Process=int(*)(void*,const float*,float*,unsigned);
 Process fsProcess=nullptr;void(*fsReset)(void*)=nullptr;void(*fsClose)(void*)=nullptr;
 int(*fsPush)(void*,const float*,unsigned)=nullptr;int(*fsPull)(void*,float*,unsigned,uint64_t*,unsigned*)=nullptr;
 void* referenceFs=nullptr;Process referenceProcess=nullptr;void(*referenceReset)(void*)=nullptr;void(*referenceClose)(void*)=nullptr;
 uint64_t parityFrames=0;double parityMaxError=0;
 int64_t warmupUs=0;
 uint64_t inferenceCpuTicks=0,postCpuTicks=0;
 unsigned fsDelay=0;uint64_t write=0;int64_t srcDelay=0;std::string failure;
 Joint(const wchar_t*path,const wchar_t*fsPath,bool verify=false,bool trace=false):model(path),traceEnabled(trace){
#ifdef NSP_JOINT_DUAL_WORKER
  if(trace)throw std::runtime_error("Detailed trace currently targets the original single-worker path");
#endif
#ifdef NSP_EXPERIMENTAL_DIRECT48
  // Same 44.1-kHz-trained weights, deliberately evaluated at 48 kHz. This
  // isolates the timing opportunity; it is NOT a native-48 quality claim.
  srcDelay=0;
#else
  down.prepare(48000,44100,2);up.prepare(44100,48000,2);
  double delay=down.filterGroupDelayInputSamples()+up.filterGroupDelayOutputSamples();
  up.prepare(44100,48000,2,std::ceil(delay)-delay);
  srcDelay=llround(down.filterGroupDelayInputSamples()+up.filterGroupDelayOutputSamples());
#endif
  fsModule=LoadLibraryExW(fsPath,nullptr,0x1100);if(!fsModule)throw std::runtime_error("FS load");
#ifdef NSP_JOINT_MATURE_FS
  auto open=(void*(*)())GetProcAddress(fsModule,"nsp_fs_mature_open");
  fsPush=(decltype(fsPush))GetProcAddress(fsModule,"nsp_fs_mature_push");fsPull=(decltype(fsPull))GetProcAddress(fsModule,"nsp_fs_mature_pull");fsReset=(void(*)(void*))GetProcAddress(fsModule,"nsp_fs_mature_reset");fsClose=(void(*)(void*))GetProcAddress(fsModule,"nsp_fs_mature_close");
  if(!open||!fsPush||!fsPull||!fsReset||!fsClose)throw std::runtime_error("Mature FS ABI unavailable");
#else
  auto open=(void*(*)())GetProcAddress(fsModule,"nsp_fs_open");
  fsProcess=(Process)GetProcAddress(fsModule,"nsp_fs_process");fsReset=(void(*)(void*))GetProcAddress(fsModule,"nsp_fs_reset");fsClose=(void(*)(void*))GetProcAddress(fsModule,"nsp_fs_close");
#endif
  fsDelay=((unsigned(*)())GetProcAddress(fsModule,"nsp_fs_delay"))();fs=open();if(!fs)throw std::runtime_error("FS init");
  if(verify){
#ifdef NSP_JOINT_MATURE_FS
   auto refOpen=(void*(*)())GetProcAddress(fsModule,"nsp_fs_open");referenceProcess=(Process)GetProcAddress(fsModule,"nsp_fs_process");referenceReset=(void(*)(void*))GetProcAddress(fsModule,"nsp_fs_reset");referenceClose=(void(*)(void*))GetProcAddress(fsModule,"nsp_fs_close");referenceFs=refOpen();if(!referenceFs)throw std::runtime_error("Reference FS init");
#else
   throw std::runtime_error("Parity verification requires mature publication build");
#endif
  }
  // Exercise device/runtime allocations before the source clock starts. Reset
  // all recurrent states afterward; warmup samples never enter source audio.
  std::array<float,256> warm{};auto warmBegin=nowNs();for(unsigned i=0;i<4;i++)model.process(warm.data());model.reset();warmupUs=(nowNs()-warmBegin)/1000;
  readyAges.reserve(40000);publications.reserve(100000);if(trace){hopTraces.reserve(50000);publicationTraces.reserve(100000);}
#ifdef NSP_JOINT_DUAL_WORKER
  postWorker=std::thread([this]{runPost();});
#endif
  worker=std::thread([this]{run();});
 }
 void publish(int64_t first,const float*decoded,unsigned count,unsigned epoch,int64_t fsBegin,int64_t ready){
  unsigned skipped=first<0?(unsigned)std::min<int64_t>(count,-first):0;uint64_t rangeFirst=(uint64_t)(first+skipped);
  for(unsigned i=skipped;i<count;i++){uint64_t source=first+i;auto&r=raw[source%R];if(r.stamp.load(std::memory_order_acquire)!=tag(epoch,source))throw std::runtime_error("vocal timestamp mismatch");
   auto&o=result[source%R];o.stamp=0;for(unsigned c=0;c<12;c++)o.pcm[c]=decoded[c*count+i]+(c==0?r.v.load():(c==1?r.w.load():0.f));auto published=nowNs();o.readyNs=published;o.stamp.store(tag(epoch,source),std::memory_order_release);
   // Split trace intervals on consumer boundaries: a request ending at239
   // must not be charged for publication of later samples240..255.
   if((source+1)%240==0||i+1==count){auto frames=(unsigned)(source+1-rangeFirst);if(!LiveJoint)publications.push_back({rangeFirst,frames,published});if(traceEnabled)publicationTraces.push_back({rangeFirst,activeTraceHop,frames,fsBegin,ready,published});rangeFirst=source+1;}
   if(source>=48000&&source%B==0)firstFsMs.add((published-r.submittedNs.load())*1e-6);
   if(source%B==B-1){if(LiveJoint){if(auto event=resultEvent.load(std::memory_order_acquire))SetEvent(event);}
    if(!LiveJoint)readyAges.push_back((published-originNs.load())*1e-6-(source+1)*1000./48000.);
    if(source>=48000){auto arrival=r.submittedNs.load(),vready=r.vocalReadyNs.load();separationMs.add((vready-arrival)*1e-6);fsFutureWaitMs.add((fsBegin-vready)*1e-6);fsTraceMs.add((ready-fsBegin)*1e-6);publishTraceMs.add((published-ready)*1e-6);totalTraceMs.add((published-arrival)*1e-6);fsSpanMs.add((published-result[(source-B+1)%R].readyNs.load())*1e-6);}
   }
  }
 }
 void writeTrace(const std::filesystem::path&folder){if(!traceEnabled)return;std::filesystem::create_directories(folder);std::ofstream hopFile(folder/"hops.csv"),pubFile(folder/"publications.csv");if(!hopFile||!pubFile)throw std::runtime_error("Cannot write timing trace");auto origin=originNs.load();
  hopFile<<"hop,inputFirst,inputFrames,submittedNs,workerBeginNs,inferBeginNs,inferEndNs\n";for(const auto&h:hopTraces)hopFile<<h.id<<','<<h.inputFirst<<','<<B<<','<<h.submitted-origin<<','<<h.workerBegin-origin<<','<<h.inferBegin-origin<<','<<h.inferEnd-origin<<'\n';
  pubFile<<"first,frames,hop,fsBeginNs,fsEndNs,readyNs\n";for(const auto&p:publicationTraces)pubFile<<p.first<<','<<p.frames<<','<<p.hop<<','<<p.fsBegin-origin<<','<<p.fsEnd-origin<<','<<p.ready-origin<<'\n';
 }
 void finishWorkers(){stop=true;SetEvent(event);if(worker.joinable())worker.join();postStop=true;SetEvent(postEvent);if(postWorker.joinable())postWorker.join();}
 ~Joint(){finishWorkers();if(auto ready=resultEvent.load())CloseHandle(ready);if(referenceFs)referenceClose(referenceFs);if(fs)fsClose(fs);if(fsModule)FreeLibrary(fsModule);CloseHandle(event);CloseHandle(postEvent);}
 void recordFailure(const std::exception&e){std::lock_guard<std::mutex>lock(failureMutex);failure=e.what();errors++;}
 void enqueueVocal(uint64_t first,const float*l,const float*r,unsigned frames,unsigned epoch,bool steady){
  if(frames>512)throw std::runtime_error("Vocal handoff capacity");auto&job=postQueue[postWrite%postQueue.size()];if(job.ready.load(std::memory_order_acquire)){postDrops++;throw std::runtime_error("Vocal handoff queue full");}
  job.first=first;job.frames=frames;job.epoch=epoch;job.steady=steady;job.sharedL=job.sharedR=nullptr;job.lease=-1;std::copy_n(l,frames,job.l.begin());std::copy_n(r,frames,job.r.begin());handoffCopiedBytes+=frames*2*sizeof(float);job.queuedNs=nowNs();job.ready.store(true,std::memory_order_release);postWrite++;
  auto depth=postWrite-postRead.load(std::memory_order_acquire),old=postHighWater.load();while(depth>old&&!postHighWater.compare_exchange_weak(old,depth)){}SetEvent(postEvent);
 }
#ifdef NSP_VOCAL_SHARED_OUTPUT
 void enqueueSharedVocal(uint64_t first,const float*pcm,unsigned epoch,bool steady){unsigned lease=model.leaseLastOutput();auto&job=postQueue[postWrite%postQueue.size()];if(job.ready.load(std::memory_order_acquire)){model.releaseOutput(lease);postDrops++;throw std::runtime_error("Shared output handoff queue full");}
  job.first=first;job.frames=128;job.epoch=epoch;job.steady=steady;job.sharedL=pcm;job.sharedR=pcm+128;job.lease=int(lease);job.queuedNs=nowNs();job.ready.store(true,std::memory_order_release);postWrite++;
  auto depth=postWrite-postRead.load(std::memory_order_acquire),old=postHighWater.load();while(depth>old&&!postHighWater.compare_exchange_weak(old,depth)){}SetEvent(postEvent);
 }
#endif
 void runPost(){SetThreadDescription(GetCurrentThread(),L"NSp residual FS pipeline");if(LiveJoint)postEfficientCore=prefer_efficient_core(1);DWORD index=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Pro Audio",&index);postMmcss=task!=nullptr;if(task)AvSetMmThreadPriority(task,AVRT_PRIORITY_NORMAL);
  auto cpuBegin=cpuTicks(true);
  uint64_t read=0;while(true){auto&job=postQueue[read%postQueue.size()];if(!job.ready.load(std::memory_order_acquire)){if(postStop.load())break;WaitForSingleObject(postEvent,5);continue;}
   try{if(job.steady)postDispatchUs.add((nowNs()-job.queuedNs)*1e-3);consumeVocal(job.first,job.sharedL?job.sharedL:job.l.data(),job.sharedR?job.sharedR:job.r.data(),job.frames,job.epoch,job.steady);}catch(const std::exception&e){
    recordFailure(e);
    // The current job may have advanced FS only partway. Reset on the next job.
    postEpoch=0;
   }
#ifdef NSP_VOCAL_SHARED_OUTPUT
   if(job.lease>=0){model.releaseOutput((unsigned)job.lease);job.lease=-1;}
#endif
   job.ready.store(false,std::memory_order_release);postRead.store(++read,std::memory_order_release);
  }postCpuTicks=cpuTicks(true)-cpuBegin;if(task)AvRevertMmThreadCharacteristics(task);
 }
 bool submit(const float*pcm,uint64_t frame,unsigned epoch){auto& s=queue[write%Q];if(s.ready.load(std::memory_order_acquire)){drops++;return false;}
  s.submittedNs=nowNs();
  for(unsigned i=0;i<B;i++){auto& r=raw[(frame+i)%R];r.stamp=0;r.l=pcm[i];r.r=pcm[B+i];r.submittedNs=s.submittedNs;r.stamp.store(tag(epoch,frame+i),std::memory_order_release);}
  s.frame=frame;s.epoch=epoch;std::copy_n(pcm,2*B,s.pcm.begin());s.ready.store(true,std::memory_order_release);write++;SetEvent(event);return true;
 }
 void consumeVocal(uint64_t firstUp,const float*ul,const float*ur,unsigned samples,unsigned epoch,bool steady){
  if(postEpoch!=epoch||postExpected!=firstUp){fsReset(fs);if(referenceFs)referenceReset(referenceFs);nextMatureFrame=postOrigin=firstUp;postEpoch=epoch;}postExpected=firstUp+samples;
  auto&residual=scratch.residual;auto&decoded=scratch.decoded;auto&referenceDecoded=scratch.referenceDecoded;uint64_t upIndex=firstUp;
     auto residualBegin=nowNs();
     unsigned count=0;uint64_t first=0;
     for(unsigned i=0;i<samples;i++,upIndex++){
      int64_t frame=(int64_t)upIndex-srcDelay;if(frame<0)continue;if(!count)first=frame;
      auto&r=raw[frame%R];if(r.stamp.load(std::memory_order_acquire)!=tag(epoch,frame))throw std::runtime_error("raw timestamp mismatch");
      r.v=ul[i];r.w=ur[i];float x=r.l.load(),y=r.r.load(),a=x-ul[i],b=y-ur[i];residual[count]=a;residual[512+count]=b;
      double err=std::max(std::abs(double(a+ul[i])-x),std::abs(double(b+ur[i])-y));auto prior=reconstruction.load();while(err>prior&&!reconstruction.compare_exchange_weak(prior,err)){}count++;
     }
     if(!count)return;std::copy_n(residual.data()+512,count,residual.data()+count);
     auto residualDone=nowNs();if(steady)residualUs.add((residualDone-residualBegin)*1e-3);
     for(unsigned i=0;i<count;i++){auto frame=first+i;auto&r=raw[frame%R];r.vocalReadyNs=residualDone;
      if(frame>=48000){double age=(residualDone-r.submittedNs.load())*1e-6;if(frame%B==0)firstResidualMs.add(age);if(frame%B==B-1){lastResidualMs.add(age);residualSpanMs.add((residualDone-raw[(frame-B+1)%R].vocalReadyNs.load())*1e-6);}}
     }
     auto fsBegin=nowNs();
#ifdef NSP_JOINT_MATURE_FS
     if(fsPush(fs,residual.data(),count)!=0)throw std::runtime_error("Mature FS push failed");
     int64_t ready=nowNs();if(steady)fsUs.add((ready-fsBegin)*1e-3);
     while(true){uint64_t source=0;unsigned frames=0;int pulled=fsPull(fs,decoded.data(),512,&source,&frames);if(pulled<0)throw std::runtime_error("Mature FS pull failed");if(!pulled)break;
      source+=postOrigin;if(source!=nextMatureFrame||frames!=fsDelay/2)throw std::runtime_error("Mature source interval contract");nextMatureFrame+=frames;
      publish((int64_t)source,decoded.data(),frames,epoch,fsBegin,ready);
     }
#else
     if(fsProcess(fs,residual.data(),decoded.data(),count)!=1)throw std::runtime_error("FS contract");
     int64_t ready=nowNs();if(steady)fsUs.add((ready-fsBegin)*1e-3);
     publish((int64_t)first-fsDelay,decoded.data(),count,epoch,fsBegin,ready);
#endif
     if(steady)publishUs.add((nowNs()-ready)*1e-3);
     if(referenceFs){
      if(referenceProcess(referenceFs,residual.data(),referenceDecoded.data(),count)!=1)throw std::runtime_error("Reference FS contract");
      for(unsigned i=0;i<count;i++){int64_t source=(int64_t)first+i-fsDelay;if(source<0)continue;auto&r=raw[source%R];auto&o=result[source%R];if(o.stamp.load(std::memory_order_acquire)!=tag(epoch,source))throw std::runtime_error("Reference source not yet published");
       for(unsigned c=0;c<12;c++){float expected=referenceDecoded[c*count+i]+(c==0?r.v.load():(c==1?r.w.load():0.f));parityMaxError=std::max(parityMaxError,std::abs(double(expected)-o.pcm[c].load()));}parityFrames++;
      }
     }
 }
 void run(){SetThreadDescription(GetCurrentThread(),L"NSp joint inference");if(LiveJoint)inferenceEfficientCore=prefer_efficient_core(0);DWORD index=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Audio",&index);inferenceMmcss=task!=nullptr;if(task)AvSetMmThreadPriority(task,AVRT_PRIORITY_HIGH);
  auto cpuBegin=cpuTicks(true);
  uint64_t read=0,upIndex=0,expected=0;unsigned epoch=0,fill=0;bool valid=false;
  std::array<float,512>dl{},dr{},ul{},ur{};std::array<float,256>chunk{};
  while(!stop){auto&s=queue[read%Q];if(!s.ready.load(std::memory_order_acquire)){WaitForSingleObject(event,5);continue;}
   const auto workerBegin=nowNs();
   const bool steady=s.frame>=48000;if(steady)queueUs.add((nowNs()-s.submittedNs)*1e-3);
   try{
    if(epoch!=s.epoch||expected!=s.frame){epoch=s.epoch;model.reset();down.reset();up.reset();upIndex=s.frame;fill=0;valid=false;}
    expected=s.frame+B;const float*in[]={s.pcm.data(),s.pcm.data()+B};float*out[]={dl.data(),dr.data()};auto downBegin=nowNs();
#ifdef NSP_EXPERIMENTAL_DIRECT48
    std::copy_n(in[0],B,dl.data());std::copy_n(in[1],B,dr.data());audio_plugin::StreamingSampleRateAdapter::ProcessResult d{B,B,true};
#else
    auto d=down.process(in,B,out,512);
#endif
    if(steady)downUs.add((nowNs()-downBegin)*1e-3);if(!d.ok||d.inputConsumed!=B)throw std::runtime_error("downsample contract");
    int64_t previousHopEnd=0;
    for(unsigned f=0;f<d.outputProduced;f++){
     chunk[fill]=dl[f];chunk[128+fill]=dr[f];if(++fill<128)continue;fill=0;
     auto begin=Clock::now();if(steady&&previousHopEnd)inPacketInferenceGapUs.add((std::chrono::duration_cast<std::chrono::nanoseconds>(begin.time_since_epoch()).count()-previousHopEnd)*1e-3);const float*v=model.process(chunk.data());auto hopEnd=Clock::now();previousHopEnd=std::chrono::duration_cast<std::chrono::nanoseconds>(hopEnd.time_since_epoch()).count();auto us=std::chrono::duration_cast<std::chrono::microseconds>(hopEnd-begin).count();hops++;totalUs+=us;auto old=maxUs.load();while(us>old&&!maxUs.compare_exchange_weak(old,us)){}
     if(steady)inferUs.add(us);
     if(traceEnabled){activeTraceHop=hops.load();hopTraces.push_back({activeTraceHop,s.frame,s.submittedNs,workerBegin,std::chrono::duration_cast<std::chrono::nanoseconds>(begin.time_since_epoch()).count(),previousHopEnd});}
     if(!valid){valid=true;continue;}
#ifdef NSP_VOCAL_SHARED_OUTPUT
     enqueueSharedVocal(upIndex,v,epoch,steady);upIndex+=128;continue;
#endif
     const float*vi[]={v,v+128};float*vo[]={ul.data(),ur.data()};auto upBegin=nowNs();
#ifdef NSP_EXPERIMENTAL_DIRECT48
     std::copy_n(v,128,ul.data());std::copy_n(v+128,128,ur.data());audio_plugin::StreamingSampleRateAdapter::ProcessResult u{128,128,true};
#else
     auto u=up.process(vi,128,vo,512);
#endif
     if(steady)upUs.add((nowNs()-upBegin)*1e-3);if(!u.ok)throw std::runtime_error("upsample contract");
#ifdef NSP_JOINT_DUAL_WORKER
     enqueueVocal(upIndex,ul.data(),ur.data(),(unsigned)u.outputProduced,epoch,steady);
#else
     consumeVocal(upIndex,ul.data(),ur.data(),(unsigned)u.outputProduced,epoch,steady);
#endif
     upIndex+=u.outputProduced;
    }
   }catch(const std::exception&e){
    recordFailure(e);
    // A failed hop loses part of this packet. Re-anchor the vocal cursor to
    // the next packet instead of publishing every later result too far back.
    epoch=0;
   }
   processed++;s.ready.store(false,std::memory_order_release);read++;
  }inferenceCpuTicks=cpuTicks(true)-cpuBegin;if(task)AvRevertMmThreadCharacteristics(task);
 }
};
