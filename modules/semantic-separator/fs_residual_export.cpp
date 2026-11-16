// Offline source-aligned direct48 diagnostic. No audio device or timing claim.
#define NOMINMAX
#include "vocal_model_openvino.h"
#include <iostream>
int wmain(int argc,wchar_t**argv){try{
 if(argc!=4)return 2;VocalModel model(argv[1]);
 std::ifstream input(argv[2],std::ios::binary|std::ios::ate);if(!input)throw std::runtime_error("Missing captured PCM");
 size_t size=size_t(input.tellg())/sizeof(float);if(!size||size%960)throw std::runtime_error("Invalid captured packet length");
 std::vector<float>raw(size);input.seekg(0);input.read((char*)raw.data(),size*sizeof(float));
 size_t frames=size/2;std::vector<float>out(frames*4);std::array<float,256>chunk{};
 for(unsigned h=0;h<4;h++)model.process(chunk.data());model.reset();
 for(size_t h=0;h<(frames+127)/128+1;h++){
  for(unsigned c=0;c<2;c++)for(unsigned i=0;i<128;i++){size_t f=h*128+i;chunk[c*128+i]=f<frames?raw[(f/480)*960+c*480+f%480]:0.f;}
  auto*v=model.process(chunk.data());if(!h)continue;
  for(unsigned c=0;c<2;c++)for(unsigned i=0;i<128;i++){size_t f=(h-1)*128+i;if(f>=frames)continue;
   if(!std::isfinite(v[c*128+i]))throw std::runtime_error("Nonfinite vocal");
   out[c*frames+f]=v[c*128+i];out[(c+2)*frames+f]=raw[(f/480)*960+c*480+f%480]-v[c*128+i];}
 }
 std::ofstream file(argv[3],std::ios::binary);file.write((char*)out.data(),out.size()*sizeof(float));if(!file)throw std::runtime_error("Export write failed");
 std::cout<<"{\"frames\":"<<frames<<",\"layout\":\"vocalL,vocalR,residualL,residualR planar\",\"experimentalDirect48\":true}\n";
 return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
