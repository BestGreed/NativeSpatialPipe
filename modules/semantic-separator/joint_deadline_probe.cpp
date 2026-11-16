#include "joint_pipeline.h"
int wmain(int argc,wchar_t**argv){try{
 if(argc<4)return 2;unsigned seconds=argc>4?_wtoi(argv[4]):30,deadline=argc>5?_wtoi(argv[5]):960;
 // A packet-budget run separates post-arrival processing from capture batching.
 // It retains a paced 240-frame consumer with a fixed, never-adaptive offset.
 bool packetBudget=argc>6&&wcscmp(argv[6],L"packet-budget")==0;
 unsigned consumerDeadline=deadline+(packetBudget?B:0);
 std::vector<float>music;std::ifstream file(argv[3],std::ios::binary|std::ios::ate);if(!file)throw std::runtime_error("music input");auto bytes=file.tellg();music.resize((size_t)bytes/4);file.seekg(0);file.read((char*)music.data(),bytes);if(music.size()<960)throw std::runtime_error("music length");
 // Source file is captured planar 480-frame stereo blocks.
 bool trace=argc>7&&wcscmp(argv[6],L"trace")==0;
 auto ctx=std::make_unique<Joint>(argv[1],argv[2],argc>6&&wcscmp(argv[6],L"verify")==0,trace);
 HANDLE timer=CreateWaitableTimerExW(nullptr,nullptr,2,TIMER_ALL_ACCESS);if(!timer)throw std::runtime_error("timer");
 DWORD mainIndex=0;HANDLE mainTask=AvSetMmThreadCharacteristicsW(L"Pro Audio",&mainIndex);if(mainTask)AvSetMmThreadPriority(mainTask,AVRT_PRIORITY_NORMAL);
 std::array<float,2*B>audio{};uint64_t miss=0,checked=0,nonfinite=0,maxRun=0,run=0;double maxLatenessMs=0;auto start=Clock::now();
 auto processCpuBegin=cpuTicks();
 ctx->originNs=std::chrono::duration_cast<std::chrono::nanoseconds>(start.time_since_epoch()).count();
 const uint64_t programFrames=uint64_t(seconds)*48000;
 uint64_t deviceChecked=0,deviceMissed=0,deviceStartupMissed=0,deviceMissingFrames=0,deviceNonfinite=0,deviceLongestRun=0;
 bool deviceMmcss=false;Metric deviceWakeUs,deviceRequiredMs;
 std::thread device([&]{
  DWORD taskIndex=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Pro Audio",&taskIndex);deviceMmcss=task!=nullptr;if(task)AvSetMmThreadPriority(task,AVRT_PRIORITY_NORMAL);
  HANDLE tick=CreateWaitableTimerExW(nullptr,nullptr,2,TIMER_ALL_ACCESS);if(!tick){deviceStartupMissed++;return;}
  uint64_t run=0;
  for(uint64_t source=0;source+240<=programFrames;source+=240){
   int64_t dueNs=ctx->originNs.load()+(source+consumerDeadline)*1000000000LL/48000;
   int64_t remaining=dueNs-nowNs();if(remaining>0){LARGE_INTEGER due;due.QuadPart=-std::max<int64_t>(1,remaining/100);SetWaitableTimer(tick,&due,0,nullptr,nullptr,FALSE);WaitForSingleObject(tick,1000);}
   if(source>=48000)deviceWakeUs.add((nowNs()-dueNs)*1e-3);
   bool ok=true,allPresent=true;int64_t latest=0;
   for(unsigned i=0;i<240;i++){auto&o=ctx->result[(source+i)%R];if(o.stamp.load(std::memory_order_acquire)!=tag(1,source+i)){ok=false;allPresent=false;deviceMissingFrames++;continue;}
    auto ready=o.readyNs.load();latest=std::max(latest,ready);if(ready>dueNs)ok=false;for(auto&v:o.pcm)if(!std::isfinite(v.load()))deviceNonfinite++;
   }
   if(source>=48000){deviceChecked++;if(!ok){deviceMissed++;deviceLongestRun=std::max(deviceLongestRun,++run);}else run=0;if(allPresent)deviceRequiredMs.add((latest-ctx->originNs.load())*1e-6-source/48.);}
   else if(!ok)deviceStartupMissed++;
  }
  CloseHandle(tick);if(task)AvRevertMmThreadCharacteristics(task);
 });
 // Flush source EOF with zero samples while the consumer drains source audio.
 unsigned blocks=(unsigned)((programFrames+2048+B-1)/B);unsigned epoch=1;
 for(unsigned block=0;block<blocks;block++){
  uint64_t frame=uint64_t(block)*B;for(unsigned i=0;i<B;i++){uint64_t t=(frame+i)%(music.size()/960*480);for(unsigned c=0;c<2;c++)audio[c*B+i]=frame+i<programFrames?music[(t/480)*960+c*480+t%480]:0.f;}
  auto target=start+std::chrono::nanoseconds((frame+B)*1000000000LL/48000);auto remaining=std::chrono::duration_cast<std::chrono::nanoseconds>(target-Clock::now()).count();
  if(remaining>0){LARGE_INTEGER due;due.QuadPart=-std::max<int64_t>(1,remaining/100);SetWaitableTimer(timer,&due,0,nullptr,nullptr,FALSE);WaitForSingleObject(timer,1000);}
  maxLatenessMs=std::max(maxLatenessMs,std::chrono::duration<double,std::milli>(Clock::now()-target).count());
  ctx->submit(audio.data(),frame,epoch);
  // Deadline is relative to arrival of the complete source block at the
  // separation stage. Capture batching is reported separately, not hidden.
  if(frame>=deadline){uint64_t source=frame-deadline;bool ok=true;
   int64_t deadlineNs=ctx->originNs.load()+(source+B+deadline)*1000000000LL/48000;
   for(unsigned i=0;i<B;i++){auto&o=ctx->result[(source+i)%R];if(o.stamp.load(std::memory_order_acquire)!=tag(epoch,source+i)){ok=false;continue;}if(o.readyNs.load()>deadlineNs)ok=false;for(auto&v:o.pcm)if(!std::isfinite(v.load()))nonfinite++;
   }
   if(block>48000/B&&source+B<=programFrames){checked++;if(!ok){miss++;run++;maxRun=std::max(maxRun,run);}else run=0;}
  }
 }
 device.join();
 // Stop before reading the worker-owned failure text.
 ctx->finishWorkers();CloseHandle(timer);if(mainTask)AvRevertMmThreadCharacteristics(mainTask);
 double elapsedSeconds=std::chrono::duration<double>(Clock::now()-start).count();auto processCpuUsed=cpuTicks()-processCpuBegin;SYSTEM_INFO cpuInfo{};GetSystemInfo(&cpuInfo);
 if(trace)ctx->writeTrace(argv[7]);
 auto&ages=ctx->readyAges;std::sort(ages.begin(),ages.end());auto percentile=[&](double p){return ages.empty()?0.:ages[std::min(ages.size()-1,size_t(p*ages.size()))];};
 std::cout<<"{\"seconds\":"<<seconds<<",\"sourceBlockFrames\":"<<B<<",\"deadlineMs\":"<<deadline/48.<<",\"deadlineReference\":\"stage-input-block-arrival\",\"fsDelayFrames\":"<<ctx->fsDelay<<",\"srcDelayFrames\":"<<ctx->srcDelay<<",\"checkedBlocks\":"<<checked<<",\"missedBlocks\":"<<miss<<",\"maxConsecutiveMisses\":"<<maxRun<<",\"errors\":"<<ctx->errors<<",\"drops\":"<<ctx->drops<<",\"nonfinite\":"<<nonfinite<<",\"modelHops\":"<<ctx->hops<<",\"meanInferenceUs\":"<<(ctx->hops?ctx->totalUs.load()/double(ctx->hops.load()):0)<<",\"maxInferenceUs\":"<<ctx->maxUs<<",\"maxReconstructionError\":"<<ctx->reconstruction<<",\"maxSourceWakeLatenessMs\":"<<maxLatenessMs<<",\"readyAgeP50Ms\":"<<percentile(.5)<<",\"readyAgeP99Ms\":"<<percentile(.99)<<",\"readyAgeMaxMs\":"<<percentile(1);
#ifdef NSP_EXPERIMENTAL_DIRECT48
 std::cout<<",\"experimentalDirect48\":true,\"modelTrainingSampleRate\":44100,\"processingSampleRate\":48000";
#else
 std::cout<<",\"experimentalDirect48\":false,\"modelTrainingSampleRate\":44100,\"processingSampleRate\":44100";
#endif
 std::cout<<",\"timingUs\":{";ctx->queueUs.json("queue");std::cout<<",";ctx->downUs.json("downsample");std::cout<<",";ctx->inferUs.json("inference");std::cout<<",";ctx->upUs.json("upsample");std::cout<<",";ctx->residualUs.json("residual");std::cout<<",";ctx->fsUs.json("fsCompute");std::cout<<",";ctx->publishUs.json("publishBatch");
 std::cout<<"},\"criticalPathMs\":{";ctx->separationMs.json("separationReady");std::cout<<",";ctx->fsFutureWaitMs.json("waitForFutureResidual");std::cout<<",";ctx->fsTraceMs.json("fsCompute");std::cout<<",";ctx->publishTraceMs.json("mixPublish");std::cout<<",";ctx->totalTraceMs.json("total");
 std::cout<<"},\"blockProgressMs\":{";ctx->firstResidualMs.json("firstResidual");std::cout<<",";ctx->lastResidualMs.json("lastResidual");std::cout<<",";ctx->residualSpanMs.json("residualSpan");std::cout<<",";ctx->firstFsMs.json("firstFsOutput");std::cout<<",";ctx->fsSpanMs.json("fsOutputSpan");std::cout<<"}";
#ifdef NSP_JOINT_MATURE_FS
 std::cout<<",\"matureFsPublication\":true";
#else
 std::cout<<",\"matureFsPublication\":false";
#endif
 std::cout<<",\"parityCheckedFrames\":"<<ctx->parityFrames<<",\"maxMixedParityError\":"<<ctx->parityMaxError<<",\"modelWarmupHops\":4,\"modelWarmupUs\":"<<ctx->warmupUs;
#ifdef NSP_JOINT_DUAL_WORKER
 std::cout<<",\"threadMode\":\"inference-plus-residual-fs\"";
#else
 std::cout<<",\"threadMode\":\"serial\"";
#endif
 std::cout<<",\"cpu\":{\"scope\":\"isolated prototype; includes source and consumer\",\"processMsPerAudioSecond\":"<<processCpuUsed*.0001/seconds<<",\"wholeMachinePercent\":"<<processCpuUsed*1e-7/elapsedSeconds/cpuInfo.dwNumberOfProcessors*100<<",\"inferenceWorkerMsPerAudioSecond\":"<<ctx->inferenceCpuTicks*.0001/seconds<<",\"residualWorkerMsPerAudioSecond\":"<<ctx->postCpuTicks*.0001/seconds<<"}";
 std::cout<<",\"postQueue\":{\"highWater\":"<<ctx->postHighWater<<",\"drops\":"<<ctx->postDrops<<",\"pcmCopiedBytes\":"<<ctx->handoffCopiedBytes<<",";ctx->postDispatchUs.json("dispatchUs");std::cout<<"}";
#ifdef NSP_VOCAL_SHARED_OUTPUT
 std::cout<<",\"tensorMemory\":\"NPU-context-owned host tensors; output lease shared with CPU\",\"outputTensorRebindings\":"<<ctx->model.outputRebindings();
#else
 std::cout<<",\"tensorMemory\":\"standard host tensors\"";
#endif
 std::cout<<",\"inPacketInferenceGapUs\":";std::cout<<"{";std::cout<<"\"scope\":\"between consecutive model hops inside one already-arrived source packet\",";ctx->inPacketInferenceGapUs.json("timing");std::cout<<"}";
 std::cout<<",\"consumer240\":{\"scope\":\"paced independent pre-DSP device-request simulation; no audio device\",\"deadlineReference\":\"source-first-sample-clock\",\"reserveMs\":"<<consumerDeadline/48.<<",\"checked\":"<<deviceChecked<<",\"missed\":"<<deviceMissed<<",\"startupMissed\":"<<deviceStartupMissed<<",\"missingFramesIncludingStartup\":"<<deviceMissingFrames<<",\"longestMissRun\":"<<deviceLongestRun<<",\"nonfinite\":"<<deviceNonfinite<<",\"mmcss\":"<<(deviceMmcss?"true":"false")<<",";deviceWakeUs.json("wakeLatenessUs");std::cout<<",";deviceRequiredMs.json("observedRequiredReserveMs");std::cout<<"}";
 std::cout<<",\"publicationSweep240\":[";bool comma=false;
 for(unsigned reserve:{960u,1080u,1200u,1440u}){uint64_t checked=0,late=0;size_t at=0;double maxRequired=0;
  for(uint64_t source=48000;source+240<=programFrames;source+=240){while(at<ctx->publications.size()&&ctx->publications[at].first+ctx->publications[at].frames<=source)at++;
   size_t k=at;uint64_t cursor=source;int64_t ready=0;while(k<ctx->publications.size()&&cursor<source+240){auto&p=ctx->publications[k];if(p.first>cursor)break;ready=std::max(ready,p.readyNs);cursor=std::min(source+240,p.first+p.frames);k++;}
   checked++;double required=ready?(ready-ctx->originNs.load())*1e-6-source/48.:0;maxRequired=std::max(maxRequired,required);if(cursor!=source+240||required>reserve/48.)late++;
  }
  if(comma)std::cout<<",";comma=true;std::cout<<"{\"reserveMs\":"<<reserve/48.<<",\"checked\":"<<checked<<",\"missed\":"<<late<<",\"maxRequiredMs\":"<<maxRequired<<"}";
 }
 std::cout<<"]}\n";
 if(ctx->errors)std::cerr<<ctx->failure<<"\n";return (ctx->errors||ctx->drops||nonfinite||deviceNonfinite||ctx->parityMaxError>1e-6)?3:0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
