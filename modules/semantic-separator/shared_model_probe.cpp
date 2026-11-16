#define NOMINMAX
#define VocalModel LegacyVocalModel
#include "vocal_model_openvino.h"
#undef VocalModel
#define VocalModel SharedVocalModel
#include "vocal_model_shared_npu.h"
#undef VocalModel
#include <iostream>
#include <fstream>
int wmain(int argc,wchar_t**argv){try{
 if(argc<3)return 2;LegacyVocalModel original(argv[1]);SharedVocalModel shared(argv[1]);
 std::ifstream file(argv[2],std::ios::binary|std::ios::ate);if(!file)throw std::runtime_error("PCM input missing");auto bytes=file.tellg();std::vector<float>music((size_t)bytes/4);file.seekg(0);file.read((char*)music.data(),bytes);if(music.size()<960)throw std::runtime_error("PCM input too short");
 std::array<float,256>in{},heldCopy{};const float*held=nullptr;unsigned lease=0;double maxError=0;uint64_t samples=0;
 for(unsigned hop=0;hop<512;hop++){
  if(hop==256){original.reset();shared.reset();}
  for(unsigned c=0;c<2;c++)for(unsigned i=0;i<128;i++){uint64_t frame=(hop*128+i)%(music.size()/960*480);in[c*128+i]=music[(frame/480)*960+c*480+frame%480];}
  const float*a=original.process(in.data()),*b=shared.process(in.data());
  for(unsigned i=0;i<256;i++){if(!std::isfinite(a[i])||!std::isfinite(b[i]))throw std::runtime_error("Nonfinite memory parity");maxError=std::max(maxError,std::abs(double(a[i])-b[i]));samples++;}
  if(!hop){held=b;std::copy_n(b,256,heldCopy.begin());lease=shared.leaseLastOutput();}
  for(unsigned i=0;i<256;i++)if(held[i]!=heldCopy[i])throw std::runtime_error("Output lease overwritten during reuse");
 }
 shared.releaseOutput(lease);
 std::cout<<"{\"comparedSamples\":"<<samples<<",\"maxDifference\":"<<maxError<<",\"retainedOutputSurvived512HopsAndReset\":true,\"allocation\":\"NPU context create_host_tensor\",\"passed\":"<<(maxError<=1e-5?"true":"false")<<"}\n";return maxError<=1e-5?0:3;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
