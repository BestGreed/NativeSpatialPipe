// SPDX-License-Identifier: GPL-3.0-only
// Opt-in test instrumentation. No input capture in the production device DLL.
#include "audio_platform.h"
#include "iasiodrv.h"
#include <array>
#include <vector>
#include <atomic>
#include <fstream>
#include <filesystem>
namespace {
std::array<ASIOBufferInfo,12> inputs{};
std::array<ASIOBufferInfo,12> outputs{};
std::vector<ASIOBufferInfo> duplex;
std::vector<float> recording;
std::vector<float> sent;
std::atomic<std::uint64_t> written{0}, overflow{0};
std::atomic<std::uint64_t> sentFrames{0};
std::atomic<DWORD> callbackThread{0};
std::atomic<int> callbackPriority{THREAD_PRIORITY_ERROR_RETURN};
long period{};
double rate{};
constexpr std::uint64_t capacityFrames=48000*15;
}
namespace spatial {
ASIOError CreateAsioReturnBuffers(IASIO* driver, std::vector<ASIOBufferInfo>& output,
                                 long frames, ASIOCallbacks* callbacks) {
    long in{},out{};
    if(driver->getChannels(&in,&out)!=ASE_OK||in<36||output.size()!=12)
        throw std::runtime_error("Network return test requires 12 outputs and inputs 25..36");
    if(driver->getSampleRate(&rate)!=ASE_OK||rate!=48000)
        throw std::runtime_error("Network return test requires existing 48 kHz; rate is not changed");
    duplex=output;
    for(long channel=24;channel<36;++channel){
        ASIOChannelInfo info{};info.channel=channel;info.isInput=ASIOTrue;
        if(driver->getChannelInfo(&info)!=ASE_OK||info.type!=ASIOSTInt24LSB)
            throw std::runtime_error("Network return test only decodes declared Int24LSB inputs");
        ASIOBufferInfo buffer{};buffer.isInput=ASIOTrue;buffer.channelNum=channel;
        duplex.push_back(buffer);
    }
    for(auto b:output){
        ASIOChannelInfo info{};info.channel=b.channelNum;
        if(driver->getChannelInfo(&info)!=ASE_OK||info.type!=ASIOSTInt24LSB)
            throw std::runtime_error("Network test only decodes Int24LSB output buffers");
    }
    recording.assign(capacityFrames*12,0);sent.assign(capacityFrames*12,0);
    period=frames;written=0;sentFrames=0;overflow=0;callbackThread=0;
    auto result=driver->createBuffers(duplex.data(),static_cast<long>(duplex.size()),frames,callbacks);
    if(result==ASE_OK||result==ASE_SUCCESS){
        std::copy_n(duplex.begin(),12,output.begin());
        std::copy_n(duplex.begin(),12,outputs.begin());
        std::copy_n(duplex.begin()+12,12,inputs.begin());
        for(const auto& b:inputs)if(!b.buffers[0]||!b.buffers[1]){
            driver->disposeBuffers();
            throw std::runtime_error("Network return driver returned null input memory");
        }
    }
    return result;
}
void CopyBuffers(const std::array<ASIOBufferInfo,12>& buffers,std::vector<float>& target,
                 std::atomic<std::uint64_t>& counter,long index) noexcept {
    const auto start=counter.load(std::memory_order_relaxed);
    if(start+period>capacityFrames){++overflow;return;}
    for(long f=0;f<period;++f)for(unsigned ch=0;ch<12;++ch){
        const auto* p=static_cast<const unsigned char*>(buffers[ch].buffers[index])+f*3;
        std::int32_t value=p[0]|(p[1]<<8)|(p[2]<<16);
        if(value&0x800000)value-=0x1000000;
        target[(start+f)*12+ch]=static_cast<float>(value/8388608.0);
    }
    counter.store(start+period,std::memory_order_release);
}
void CaptureAsioReturn(long index) noexcept {
    if(callbackThread.load(std::memory_order_relaxed)==0){
        callbackPriority=GetThreadPriority(GetCurrentThread());callbackThread=GetCurrentThreadId();
    }
    CopyBuffers(inputs,recording,written,index);
}
void CaptureAsioOutput(long index) noexcept {CopyBuffers(outputs,sent,sentFrames,index);}
}
extern "C" __declspec(dllexport) int nsp_test_return_write(const char* directory){
    // Called only after device stop has joined callbacks.
    const auto frames=written.load(std::memory_order_acquire);
    auto root=std::filesystem::u8path(directory);
    std::ofstream pcm(root/"return-25-36.f32",std::ios::binary);
    pcm.write(reinterpret_cast<const char*>(recording.data()),frames*12*sizeof(float));
    std::ofstream tx(root/"sent-1-12.f32",std::ios::binary);
    const auto txFrames=sentFrames.load(std::memory_order_acquire);
    tx.write(reinterpret_cast<const char*>(sent.data()),txFrames*12*sizeof(float));
    std::ofstream meta(root/"return.json");
    meta<<"{\"frames\":"<<frames<<",\"channels\":12,\"sampleRate\":"<<rate
        <<",\"bufferFrames\":"<<period<<",\"overflowCallbacks\":"<<overflow.load()
        <<",\"firstInput\":25,\"lastInput\":36,\"sampleType\":17,\"sentFrames\":"<<txFrames
        <<",\"callbackThreadId\":"<<callbackThread.load()<<",\"callbackWin32Priority\":"<<callbackPriority.load()<<"}";
    return pcm&&tx&&meta&&frames&&txFrames==frames&&overflow==0?0:1;
}
