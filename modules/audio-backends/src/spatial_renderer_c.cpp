// SPDX-License-Identifier: Apache-2.0
#include "spatial_renderer_c.h"
#include "native_scene.h"
#include <optional>
#include <cstring>
#include <stdexcept>

namespace {
thread_local std::string error;
spatial::SpeakerLayout Layout() {
    const auto canonical=spatial::Canonical916Layout();
    spatial::SpeakerLayout result; result.name=L"NSP OLR order 7.1.4";
    for(const auto i:{0,1,2,3,6,7,4,5,10,11,14,15})result.speakers.push_back(canonical.speakers[i]);
    return result;
}
struct Renderer {
    spatial::NativeSpatialScene scene{Layout()};
    std::optional<uint64_t> last;
    bool sealed{},failed{};
};
}
uint32_t nsp_spatial_abi(void){return 1;}
void* nsp_spatial_create(void){
    try { error.clear();return new Renderer; }
    catch(const std::exception& e){error=e.what();return nullptr;}
    catch(...){error="Spatial renderer creation failed";return nullptr;}
}
int nsp_spatial_process(void* handle,uint64_t sequence,uint32_t objects,uint32_t frames,
    const void* properties,const void* planar,float* output){
    auto* r=static_cast<Renderer*>(handle);
    try {
        error.clear();
        if(!r||r->failed||r->sealed||!properties||!planar||!output||objects<1||objects>160||frames<1||frames>4096)
            throw std::runtime_error("Invalid spatial renderer input/state");
        // NSP remains strict: never enter the legacy scene's gap recovery path.
        if(r->last && (*r->last==UINT64_MAX || sequence!=*r->last+1))
            throw std::runtime_error("Sequence discontinuity: rebuild native pipeline");
        spatial::NativePacketHeader h;h.stream=1;h.sequence=sequence;h.captureNs=1;
        h.objects=objects;h.frames=frames;h.bytes=objects*(32+frames*4);
        std::vector<uint8_t> bytes(h.bytes);
        std::memcpy(bytes.data(),properties,objects*32);
        std::memcpy(bytes.data()+objects*32,planar,objects*frames*4);
        const auto packet=spatial::DecodeNativePacket(h,bytes);
        const auto pcm=r->scene.Render(packet);
        std::memcpy(output,pcm.data(),pcm.size()*sizeof(float));r->last=sequence;
        return 0;
    }catch(const std::exception& e){if(r)r->failed=true;error=e.what();return -1;}
    catch(...){if(r)r->failed=true;error="Spatial renderer failed";return -1;}
}
int nsp_spatial_flush(void* handle,float* output,uint32_t capacity){
    auto* r=static_cast<Renderer*>(handle);
    try {
        error.clear();
        if(!r||r->sealed||r->failed||!output||capacity<512)throw std::runtime_error("Invalid spatial flush/state");
        const auto tail=r->scene.FlushStream(1);r->sealed=true;
        if(!tail.empty())std::memcpy(output,tail.data(),tail.size()*sizeof(float));return static_cast<int>(tail.size()/12);
    }catch(const std::exception& e){if(r)r->failed=true;error=e.what();return -1;}
    catch(...){if(r)r->failed=true;error="Spatial flush failed";return -1;}
}
void nsp_spatial_destroy(void* p){delete static_cast<Renderer*>(p);}
const char* nsp_spatial_error(void){return error.c_str();}
