// SPDX-License-Identifier: Apache-2.0
// Owned fake driver only. Tick models a hardware callback on the test thread.
#define MakeTestAsioDriver UnusedTestAsioDriver
#include "asio_test_driver.h"
#undef MakeTestAsioDriver
#include <mutex>
#ifdef NSP_ASIO_AUTOMATIC_FIXTURE
#include "audio_scheduling.h"
#include <atomic>
#include <thread>
#include <chrono>
#include <fstream>
#include <filesystem>
#endif
namespace {
std::mutex fixtureMutex;
std::vector<float> recording;
class Recorder final : public FakeDriver {
#ifdef NSP_ASIO_AUTOMATIC_FIXTURE
    std::atomic<bool> finished{false};
    std::thread ticker;
#endif
public:
    ASIOError start() override {
        ASIOError result;{std::lock_guard<std::mutex> lock(fixtureMutex);result=FakeDriver::start();}
#ifdef NSP_ASIO_AUTOMATIC_FIXTURE
        if(result==ASE_OK)ticker=std::thread([this]{
            spatial::AudioScheduling schedule;
            auto next=std::chrono::steady_clock::now();
            const auto period=std::chrono::nanoseconds(static_cast<long long>(memory[0].size()*1e9/driverRate));
            while(!finished.load()){
                next+=period;std::this_thread::sleep_until(next);
                std::lock_guard<std::mutex> lock(fixtureMutex);
                if(finished.load()||!running)break;
                Tick();
                if(recording.size()<48000*12*30)for(std::size_t f=0;f<memory[0].size();++f)
                    for(std::size_t ch=0;ch<buffers.size();++ch)recording.push_back(memory[ch*2][f]);
            }
        });
#endif
        return result;
    }
    ASIOError stop() override {
#ifdef NSP_ASIO_AUTOMATIC_FIXTURE
        finished.store(true);if(ticker.joinable())ticker.join();
#endif
        std::lock_guard<std::mutex> lock(fixtureMutex);const auto result=FakeDriver::stop();
#ifdef NSP_ASIO_AUTOMATIC_FIXTURE
        wchar_t path[32768]{};
        if(GetEnvironmentVariableW(L"NSP_TEST_CALLBACK_PATH",path,32768)){
            std::ofstream file(std::filesystem::path(path),std::ios::binary);
            file.write(reinterpret_cast<const char*>(recording.data()),recording.size()*sizeof(float));
        }
#endif
        return result;
    }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto n=--refs;
        if(!n){std::lock_guard<std::mutex> lock(fixtureMutex);current=nullptr;++releases;delete this;}
        return n;
    }
};
}
namespace spatial { IASIO* MakeTestAsioDriver(){std::lock_guard<std::mutex> lock(fixtureMutex);++opens;return current=new Recorder;} }
extern "C" __declspec(dllexport) void nsp_test_tick(unsigned count){
    std::lock_guard<std::mutex> lock(fixtureMutex);
    if(!current||!current->running)return;
    for(unsigned i=0;i<count;++i){
        current->Tick();
        for(std::size_t f=0;f<current->memory[0].size();++f)
            for(std::size_t ch=0;ch<current->buffers.size();++ch)recording.push_back(current->memory[ch*2][f]);
    }
}
extern "C" __declspec(dllexport) std::uint64_t nsp_test_copy(float* out,std::uint64_t capacity){
    std::lock_guard<std::mutex> lock(fixtureMutex);
    if(out&&capacity>=recording.size())std::copy(recording.begin(),recording.end(),out);
    return recording.size();
}
extern "C" __declspec(dllexport) void nsp_test_reset(){
    std::lock_guard<std::mutex> lock(fixtureMutex);
    if(current)current->callbacks.asioMessage(kAsioResetRequest,0,nullptr,nullptr);
}
