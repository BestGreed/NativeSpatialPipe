// SPDX-License-Identifier: GPL-3.0-only
#include "asio_output_c.h"
#include "asio_backend.h"
#include "audio_platform.h"
#include "audio_scheduling.h"
#include <cmath>
#include <cstring>
#include <limits>
#include <new>
#include <atomic>
#ifdef NSP_ASIO_NETWORK_RETURN_TEST
static std::atomic<std::uint32_t> ownerScheduling{0};
extern "C" __declspec(dllexport) std::uint32_t nsp_test_owner_mmcss(){return ownerScheduling.load();}
#endif

struct nsp_asio_context {
    // Apartment-registered ASIO drivers (including DVS) expose a private IASIO
    // interface without a COM marshaler. Activate directly on the owner STA.
    spatial::ComApartment apartment{COINIT_APARTMENTTHREADED};
    DWORD thread{GetCurrentThreadId()};
    spatial::InterleavedPcmQueue queue;
    spatial::AsioRenderer device;
    std::unique_ptr<spatial::AudioScheduling> scheduling;
    bool primed{}, complete{}, faulted{}, stopped{};
    explicit nsp_asio_context(const spatial::AsioDeviceConfig& config, bool clockSync)
        : queue(config.sourceChannels), device(config) { device.EnableClockSync(clockSync); }
};
namespace {
thread_local char errorText[1024]{};
int32_t Fail(int32_t code, const char* message) noexcept {
    const auto size=std::min(std::strlen(message),sizeof(errorText)-1);
    std::memcpy(errorText,message,size);errorText[size]=0;return code;
}
template<class F> int32_t Guard(F&& action) noexcept {
    errorText[0]=0;
    try { return action(); }
    catch(const std::bad_alloc&) { return Fail(NSP_ASIO_NO_MEMORY,"ASIO module allocation failed"); }
    catch(const std::exception& e) { return Fail(NSP_ASIO_DEVICE_ERROR,e.what()); }
    catch(...) { return Fail(NSP_ASIO_DEVICE_ERROR,"Unknown ASIO module failure"); }
}
int32_t Check(nsp_asio_handle h, bool allowFault=false) noexcept {
    if(!h)return Fail(NSP_ASIO_INVALID,"Null ASIO handle");
    if(h->thread!=GetCurrentThreadId())return Fail(NSP_ASIO_STATE,"ASIO handle used on a different thread");
    if(!allowFault&&(h->faulted||h->stopped))return Fail(NSP_ASIO_STATE,"ASIO session is faulted or stopped");
    return NSP_ASIO_OK;
}
template<class F> int32_t DeviceCall(nsp_asio_handle h,F&& action) {
    // Driver failures latch. Only observation and shutdown remain available.
    try { action();return NSP_ASIO_OK; }
    catch(...) { h->faulted=true;throw; }
}
std::wstring DriverId(const char* text) {
    if(!text)throw std::invalid_argument("Null ASIO driver id");
    std::size_t length=0;while(length<4096&&text[length])++length;
    if(!length||length==4096)throw std::invalid_argument("Invalid ASIO driver id length");
    const int count=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text,static_cast<int>(length),nullptr,0);
    if(!count)throw std::invalid_argument("Invalid UTF-8 ASIO driver id");
    std::wstring value(count,L'\0');
    if(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text,static_cast<int>(length),value.data(),count)!=count)
        throw std::invalid_argument("Invalid UTF-8 ASIO driver id");
    return value;
}
}
uint32_t nsp_asio_abi_version(void){return NSP_ASIO_ABI_VERSION;}
int32_t nsp_asio_open(const nsp_asio_config* c,nsp_asio_handle* out){
    return Guard([&]() -> int32_t {
        if(!out)return Fail(NSP_ASIO_INVALID,"Null output handle");
        if(*out)return Fail(NSP_ASIO_STATE,"Output handle must be null");
        *out=nullptr;
        if(!c||c->struct_size!=sizeof(*c)||c->abi_version!=NSP_ASIO_ABI_VERSION||
           !c->source_channels||c->source_channels>32||!c->output_channels||c->output_channels>32||
           !c->source_channel_indices||!c->driver_id_utf8||(c->flags&~NSP_ASIO_CLOCK_SYNC)||
           c->device_buffer_frames<0||!std::isfinite(c->device_sample_rate)||
           (c->device_sample_rate!=0&&(c->device_sample_rate<8000||c->device_sample_rate>384000)))
            return Fail(NSP_ASIO_INVALID,"Invalid ASIO config size, version or fields");
        spatial::AsioDeviceConfig config;
        try { config.endpointFilter=DriverId(c->driver_id_utf8); }
        catch(const std::invalid_argument& e){return Fail(NSP_ASIO_INVALID,e.what());}
        if(!spatial::IsAsioEndpoint(config.endpointFilter))return Fail(NSP_ASIO_INVALID,"Expected asio: driver id");
        config.name=L"NSP ASIO";config.sourceChannels=c->source_channels;
        for(uint32_t i=0;i<c->output_channels;++i){
            if(c->source_channel_indices[i]>=c->source_channels)return Fail(NSP_ASIO_INVALID,"Invalid source channel");
            config.sourceChannelIndices.push_back(c->source_channel_indices[i]);
            if(c->physical_channels){
                if(c->physical_channels[i]<0||c->physical_channels[i]>255||
                   std::find(config.physicalChannels.begin(),config.physicalChannels.end(),c->physical_channels[i])!=config.physicalChannels.end())
                    return Fail(NSP_ASIO_INVALID,"Invalid or duplicate physical channel");
                config.physicalChannels.push_back(c->physical_channels[i]);
            }
        }
        config.asioBufferFrames=c->device_buffer_frames;config.asioSampleRate=c->device_sample_rate;
        config.reopenOnReset=false;
        config.legacyOutputClamp=false;
        config.advanceOnStarvation=false;
        *out=new nsp_asio_context(config,(c->flags&NSP_ASIO_CLOCK_SYNC)!=0);
        return NSP_ASIO_OK;
    });
}
int32_t nsp_asio_start(nsp_asio_handle h){return Guard([&]{
    if(auto e=Check(h))return e;
    return DeviceCall(h,[&]{
        if(!h->device.IsStarted()) {
            auto scheduling=std::make_unique<spatial::AudioScheduling>();
            h->device.Start();
#ifdef NSP_ASIO_NETWORK_RETURN_TEST
            ownerScheduling=(scheduling->Enabled()?1u:0u)|(scheduling->PrioritySet()?2u:0u)|(scheduling->TimerEnabled()?4u:0u);
#endif
            h->scheduling=std::move(scheduling);
        }
    });
});}
int32_t nsp_asio_submit(nsp_asio_handle h,uint64_t first,const float* samples,uint32_t frames){return Guard([&]() -> int32_t {
    if(auto e=Check(h))return e;
    if(h->complete)return Fail(NSP_ASIO_STATE,"Input already complete");
    if(!samples||!frames||frames>192000||first!=h->queue.EndFrame()||first>UINT64_MAX-frames)
        return Fail(NSP_ASIO_INVALID,"Invalid PCM length or non-contiguous frame index");
    const auto count=static_cast<std::size_t>(frames)*h->queue.Channels();
    if(!std::all_of(samples,samples+count,[](float v){return std::isfinite(v);}))
        return Fail(NSP_ASIO_INVALID,"Nonfinite PCM input");
    return DeviceCall(h,[&]{h->queue.AppendFloat(std::vector<float>(samples,samples+count));});
});}
int32_t nsp_asio_prime(nsp_asio_handle h){return Guard([&]() -> int32_t {
    if(auto e=Check(h))return e;
    if(!h->device.IsStarted()||h->primed||!h->queue.EndFrame())return Fail(NSP_ASIO_STATE,"Prime requires started device, PCM and an unprimed session");
    return DeviceCall(h,[&]{h->device.Prime(h->queue);h->primed=true;});
});}
int32_t nsp_asio_service(nsp_asio_handle h){return Guard([&]{
    if(auto e=Check(h))return e;
    if(!h->device.IsStarted())return Fail(NSP_ASIO_STATE,"Start device before service");
    return DeviceCall(h,[&]{h->device.Service(h->queue,h->primed&&!h->complete,0);h->device.DiscardConsumed(h->queue);});
});}
int32_t nsp_asio_finish_input(nsp_asio_handle h){return Guard([&]{
    if(auto e=Check(h))return e;
    h->device.SetInputComplete();h->complete=true;return int32_t(NSP_ASIO_OK);
});}
int32_t nsp_asio_hold(nsp_asio_handle h,uint32_t enabled){return Guard([&]() -> int32_t {
    if(auto e=Check(h))return e;
    if(enabled>1||!h->primed)return Fail(NSP_ASIO_STATE,"Hold requires primed playback and a boolean value");
    return DeviceCall(h,[&]{h->device.Hold(enabled!=0);});
});}
int32_t nsp_asio_get_stats(nsp_asio_handle h,nsp_asio_stats* out){return Guard([&]() -> int32_t {
    if(auto e=Check(h,true))return e;
    if(!out||out->struct_size!=sizeof(*out))return Fail(NSP_ASIO_INVALID,"Invalid stats size");
    const auto s=h->device.Stats().at(0);
    nsp_asio_stats result{};result.struct_size=sizeof(result);result.abi_version=NSP_ASIO_ABI_VERSION;
    result.started=h->device.IsStarted();result.primed=h->primed;result.input_complete=h->complete;
    result.faulted=h->faulted;result.stopped=h->stopped;result.drained=h->device.IsDrained(h->queue);
    result.submitted_frames=h->queue.EndFrame();result.buffered_source_frames=h->device.BufferedFrames(h->queue);
    result.required_write_frame=h->device.RequiredWriteFrame();result.callbacks=s.callbacks;result.starvation_frames=s.starvationFrames;
    result.device_buffer_frames=s.bufferFrames;result.minimum_prime_frames=h->device.MaximumBufferFrames();
    result.source_frame=s.sourceFrame;result.device_sample_rate=s.sampleRate;*out=result;return NSP_ASIO_OK;
});}
int32_t nsp_asio_stop(nsp_asio_handle h){return Guard([&]{
    if(auto e=Check(h,true))return e;
    return DeviceCall(h,[&]{h->device.Stop();h->stopped=true;h->scheduling.reset();});
});}
int32_t nsp_asio_close(nsp_asio_handle* h){return Guard([&]() -> int32_t {
    if(!h)return Fail(NSP_ASIO_INVALID,"Null handle pointer");
    if(!*h)return NSP_ASIO_OK;
    if(auto e=Check(*h,true))return e;
    if((*h)->device.IsStarted())return Fail(NSP_ASIO_STATE,"Stop device before close");
    delete *h;*h=nullptr;return NSP_ASIO_OK;
});}
uint32_t nsp_asio_last_error(char* out,uint32_t capacity){
    const auto length=std::strlen(errorText);
    if(out&&capacity){const auto count=std::min(length,static_cast<std::size_t>(capacity-1));std::memcpy(out,errorText,count);out[count]=0;}
    return static_cast<uint32_t>(length+1);
}
