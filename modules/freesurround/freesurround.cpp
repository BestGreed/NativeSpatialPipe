// Local audition adapter, GPL-3.0-or-later, matching the vendored decoder.
#include <algorithm>
#include <array>
#include <mutex>
#include <cstdint>
#include "vendor/FreeSurroundDecoder.h"
#ifndef NSP_FS_WINDOW
#define NSP_FS_WINDOW 1024
#endif
constexpr unsigned N=NSP_FS_WINDOW;
#ifdef NSP_FS_HALF_HOP
constexpr unsigned H=N/2;
#else
constexpr unsigned H=N;
#endif
struct State {
 CFreeSurroundDecoder decoder{cs_7point1,N,48000};
 std::array<float,N> left{},right{}; unsigned at=0;
 State(){decoder.SetBassRedirection(false);decoder.SetCenterImage(0.7f);}
 void reset(){decoder.Flush();left.fill(0);right.fill(0);at=0;}
};
extern "C" __declspec(dllexport) void* nsp_fs_open(){try{return new State;}catch(...){return nullptr;}}
extern "C" __declspec(dllexport) void nsp_fs_close(void* p){delete static_cast<State*>(p);}
extern "C" __declspec(dllexport) void nsp_fs_reset(void* p){if(p)static_cast<State*>(p)->reset();}
extern "C" __declspec(dllexport) unsigned nsp_fs_delay(){return H+N/2;}
extern "C" __declspec(dllexport) int nsp_fs_process(void* p,const float* in,float* out,unsigned n){
 if(!p||!in||!out||!n||n>480)return 0;
 auto& s=*static_cast<State*>(p);std::fill(out,out+n*12,0.f);
 // Upstream order: FL C FR SL SR BL BR LFE; NSp: FL FR C LFE SL SR BL BR heights.
 constexpr unsigned map[]={0,2,1,4,5,6,7,3};
 for(unsigned i=0;i<n;++i){auto decoded=s.decoder.getOutputBuffers();
#ifdef NSP_FS_HALF_HOP
  constexpr unsigned outputOffset=N/2;
#else
  constexpr unsigned outputOffset=0;
#endif
  for(unsigned c=0;c<7;++c)out[map[c]*n+i]=decoded[c][s.at+outputOffset];
  s.left[s.at]=in[i];s.right[s.at]=in[n+i];
  if(++s.at==H){float* inputs[]={s.left.data(),s.right.data()};
#ifdef NSP_FS_HALF_HOP
   s.decoder.DecodeHalf(inputs);
#else
   s.decoder.Decode(inputs);
#endif
   s.at=0;}
 }return 1;
}

#ifdef NSP_FS_HALF_HOP
// Experimental push/drain ABI: publish a finalized half-window immediately.
// Frame numbers refer to source content, not the legacy delayed output clock.
struct MaturePacket {uint64_t first=0;std::array<float,12*H> pcm{};};
struct MatureState {
 State work;uint64_t consumed=0,read=0,write=0;
 std::array<MaturePacket,16> packets;
};
extern "C" __declspec(dllexport) void* nsp_fs_mature_open(){try{return new MatureState;}catch(...){return nullptr;}}
extern "C" __declspec(dllexport) void nsp_fs_mature_close(void*p){delete static_cast<MatureState*>(p);}
extern "C" __declspec(dllexport) void nsp_fs_mature_reset(void*p){if(p){auto&s=*static_cast<MatureState*>(p);s.work.reset();s.consumed=s.read=s.write=0;}}
extern "C" __declspec(dllexport) int nsp_fs_mature_push(void*p,const float*in,unsigned n){
 if(!p||!in||!n||n>480)return -1;auto&s=*static_cast<MatureState*>(p);auto&w=s.work;
 if(s.write-s.read+(w.at+n)/H>s.packets.size())return -2;
 constexpr unsigned map[]={0,2,1,4,5,6,7,3};
 for(unsigned i=0;i<n;i++){w.left[w.at]=in[i];w.right[w.at]=in[n+i];s.consumed++;
  if(++w.at==H){float*inputs[]={w.left.data(),w.right.data()};w.decoder.DecodeHalf(inputs);w.at=0;
   if(s.consumed>=2*H){auto&packet=s.packets[s.write%s.packets.size()];packet.first=s.consumed-2*H;packet.pcm.fill(0);
    auto decoded=w.decoder.getOutputBuffers();for(unsigned c=0;c<7;c++)std::copy_n(decoded[c]+H,H,packet.pcm.data()+map[c]*H);s.write++;
   }
  }
 }return 0;
}
extern "C" __declspec(dllexport) int nsp_fs_mature_pull(void*p,float*out,unsigned capacity,uint64_t*first,unsigned*frames){
 if(!p||!out||!first||!frames||capacity<H)return -1;auto&s=*static_cast<MatureState*>(p);*frames=0;if(s.read==s.write)return 0;
 auto&packet=s.packets[s.read%s.packets.size()];std::copy(packet.pcm.begin(),packet.pcm.end(),out);*first=packet.first;*frames=H;s.read++;return 1;
}
#endif
