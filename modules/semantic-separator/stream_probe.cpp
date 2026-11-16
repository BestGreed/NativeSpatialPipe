#define NOMINMAX
#include <windows.h>
#include "StemgenRT/StreamingSampleRateAdapter.h"
#include <array>
#include <vector>
#include <cmath>
#include <cstdio>
#include <chrono>
#include <stdexcept>
#include <iostream>
int wmain(int argc,wchar_t**argv){try{
 if(argc<3)return 2;auto module=LoadLibraryExW(argv[1],nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);if(!module)throw std::runtime_error("DLL load");
 auto open=(void*(*)(const wchar_t*,uint32_t))GetProcAddress(module,"nsp_vocal_open");auto process=(int(*)(void*,const float*,float*,uint32_t,uint32_t))GetProcAddress(module,"nsp_vocal_process");auto close=(void(*)(void*))GetProcAddress(module,"nsp_vocal_close");auto stats=(void(*)(void*,char*,uint32_t))GetProcAddress(module,"nsp_vocal_stats");auto err=(const char*(*)())GetProcAddress(module,"nsp_vocal_error");
 unsigned delay=argc>4?_wtoi(argv[4]):1920;auto*ctx=open(argv[2],delay);if(!ctx)throw std::runtime_error(err());
 audio_plugin::StreamingSampleRateAdapter down,up;down.prepare(48000,44100,2);up.prepare(44100,48000,2);double d=down.filterGroupDelayInputSamples()+up.filterGroupDelayOutputSamples();up.prepare(44100,48000,2,std::ceil(d)-d);d=down.filterGroupDelayInputSamples()+up.filterGroupDelayOutputSamples();
 std::array<float,960> in{},v{};std::array<float,1024>dl{},dr{},ul{},ur{};int peak=-1;float max=0;uint64_t restored=0;
 for(unsigned block=0;block<12;block++){in.fill(0);if(!block)in[0]=in[480]=1;const float*ip[]{in.data(),in.data()+480};float*dp[]{dl.data(),dr.data()};auto a=down.process(ip,480,dp,1024);const float*dip[]{dl.data(),dr.data()};float*uop[]{ul.data(),ur.data()};auto b=up.process(dip,a.outputProduced,uop,1024);for(size_t i=0;i<b.outputProduced;i++){if(std::abs(ul[i])>max){max=std::abs(ul[i]);peak=restored+i;}}restored+=b.outputProduced;}
 if(peak!=llround(d))throw std::runtime_error("SRC content alignment");
 const int blocks=argc>3?_wtoi(argv[3]):2000;uint64_t good=0,late=0;double maxCall=0,maxReconstruction=0;uint32_t epoch=1;HANDLE timer=CreateWaitableTimerExW(nullptr,nullptr,2,TIMER_ALL_ACCESS);if(!timer)throw std::runtime_error("High resolution timer");auto start=std::chrono::steady_clock::now();
 for(int block=0;block<blocks;block++){
  if(block==blocks/2)epoch++;
  for(unsigned c=0;c<2;c++)for(unsigned i=0;i<480;i++)in[c*480+i]=float(.04*sin((block*480+i)*6.283185307179586*(c?443:701)/48000.));
  auto before=std::chrono::steady_clock::now();int available=process(ctx,in.data(),v.data(),480,epoch);auto end=std::chrono::steady_clock::now();maxCall=std::max(maxCall,std::chrono::duration<double,std::micro>(end-before).count());
  int local=block>=blocks/2?block-blocks/2:block;if(local<(int)(delay/480)&&available)throw std::runtime_error("Old epoch leaked");if(local>100){if(available)good++;else late++;}
  for(unsigned i=0;i<960;i++){if(!std::isfinite(v[i]))throw std::runtime_error("Nonfinite output");float r=in[i]-v[i];maxReconstruction=std::max(maxReconstruction,std::abs((double)(r+v[i])-in[i]));}
  auto target=start+std::chrono::milliseconds((block+1)*10);auto remaining=std::chrono::duration_cast<std::chrono::nanoseconds>(target-std::chrono::steady_clock::now()).count();if(remaining>0){LARGE_INTEGER due;due.QuadPart=-std::max<int64_t>(1,remaining/100);SetWaitableTimer(timer,&due,0,nullptr,nullptr,FALSE);WaitForSingleObject(timer,1000);}
 }
 char report[2048]{};stats(ctx,report,sizeof(report));close(ctx);CloseHandle(timer);FreeLibrary(module);
 std::cout<<"{\"blocks\":"<<blocks<<",\"steadyAvailable\":"<<good<<",\"steadyFallback\":"<<late<<",\"maxSubmitUs\":"<<maxCall<<",\"srcImpulsePeak\":"<<peak<<",\"srcExpectedDelay\":"<<d<<",\"maxMixtureReconstructionError\":"<<maxReconstruction<<",\"epochIsolation\":true,\"native\":"<<report<<"}\n";return late?3:0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
