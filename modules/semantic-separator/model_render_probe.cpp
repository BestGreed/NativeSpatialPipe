#include "vocal_model.h"
#include <avrt.h>
#include <chrono>
#include <fstream>
#include <filesystem>
#include <iostream>
int wmain(int argc,wchar_t**argv){try{
 if(argc!=4)return 2;VocalModel model(argv[1]);std::ifstream input(std::filesystem::path(argv[2]),std::ios::binary);std::ofstream output(std::filesystem::path(argv[3]),std::ios::binary);if(!input||!output)throw std::runtime_error("Probe audio files");
 DWORD index=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Audio",&index);if(task)AvSetMmThreadPriority(task,AVRT_PRIORITY_HIGH);
 std::array<float,256> audio{};std::vector<double> times;uint64_t hops=0;
 while(input.read(reinterpret_cast<char*>(audio.data()),sizeof(audio))){auto t=std::chrono::steady_clock::now();const float* result=model.process(audio.data());times.push_back(std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-t).count());output.write(reinterpret_cast<const char*>(result),sizeof(audio));hops++;}
 if(task)AvRevertMmThreadCharacteristics(task);if(hops<101)throw std::runtime_error("Short model probe");times.erase(times.begin(),times.begin()+100);std::sort(times.begin(),times.end());double sum=0;unsigned late=0;for(auto v:times){sum+=v;if(v>128e6/44100)late++;}
 std::cout<<"{\"backend\":\""<<VocalModel::backend()<<"\",\"hops\":"<<hops<<",\"meanUs\":"<<sum/times.size()<<",\"p99Us\":"<<times[size_t(times.size()*.99)]<<",\"maxUs\":"<<times.back()<<",\"overBudget\":"<<late<<",\"mmcss\":"<<(task?"true":"false")<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
