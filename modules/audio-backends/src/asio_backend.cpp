// SPDX-License-Identifier: GPL-3.0-only
#include "json_text.h"
#include "asio_backend.h"
#include "diagnostics.h"
#include "asio_format.h"
#include "clock_sync.h"
#include "audio_gap_ramp.h"
#include "sinc_resampler.h"
#include "audio_platform.h"
#include "iasiodrv.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <iostream>
#include <set>
#include <stdexcept>

namespace spatial {
#ifdef NSP_ASIO_NETWORK_RETURN_TEST
ASIOError CreateAsioReturnBuffers(IASIO*, std::vector<ASIOBufferInfo>&, long, ASIOCallbacks*);
void CaptureAsioReturn(long) noexcept;
void CaptureAsioOutput(long) noexcept;
#endif
#ifdef SPATIAL_AUDIO_ASIO_TESTING
std::uint64_t TestAsioClockNs();
#endif
#ifdef SPATIAL_AUDIO_ASIO_TESTING
IASIO* MakeTestAsioDriver();
#endif

namespace {
void Check(ASIOError result, const char* operation) {
    if (result != ASE_OK && result != ASE_SUCCESS)
        throw std::runtime_error(std::string("ASIO ") + operation + " failed (" + std::to_string(result) + ").");
}
std::wstring Wide(const char* text) {
    int n = MultiByteToWideChar(CP_ACP, 0, text, -1, nullptr, 0);
    if (n <= 1) return {};
    std::wstring result(n, L'\0');
    MultiByteToWideChar(CP_ACP, 0, text, -1, result.data(), n);
    result.pop_back();
    return result;
}
std::wstring ReadRegistry(HKEY key, const wchar_t* name) {
    wchar_t text[4096]{};
    DWORD size = sizeof(text);
    if (RegGetValueW(key, nullptr, name, RRF_RT_REG_SZ, nullptr, text, &size) != ERROR_SUCCESS) return {};
    return text;
}
class Driver {
public:
    ComPtr<IASIO> api;
    std::wstring name;
    Driver(const std::wstring& id) {
#ifdef SPATIAL_AUDIO_ASIO_TESTING
        api.Attach(MakeTestAsioDriver());
        name = L"Deterministic ASIO test driver";
#else
        const auto drivers = EnumerateAsioDrivers();
        const auto found = std::find_if(drivers.begin(), drivers.end(), [&](const auto& d) {
            return Lowercase(d.id) == Lowercase(id) || Lowercase(d.name) == Lowercase(id);
        });
        if (found == drivers.end()) throw std::runtime_error("ASIO：未找到已注册的 64 位驱动。");
        CLSID clsid{};
        ThrowIfFailed(CLSIDFromString(found->id.substr(5).c_str(), &clsid), "Parse ASIO CLSID");
        ThrowIfFailed(CoCreateInstance(clsid, nullptr, CLSCTX_INPROC_SERVER, clsid,
            reinterpret_cast<void**>(api.GetAddressOf())), "Load ASIO driver (64-bit)");
        name = found->name;
#endif
        if (!api->init(GetDesktopWindow())) {
            char error[256]{};
            api->getErrorMessage(error);
            throw std::runtime_error(std::string("ASIO 初始化失败：") + error);
        }
    }
};
bool ValidRate(double rate) { return std::isfinite(rate) && rate >= 8000 && rate <= 384000; }
}
bool IsAsioEndpoint(std::wstring_view id) { return id.size() > 5 && Lowercase(std::wstring(id.substr(0,5))) == L"asio:"; }
std::vector<AsioDriverDescriptor> EnumerateAsioDrivers() {
    HKEY root{};
    std::vector<AsioDriverDescriptor> result;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\ASIO", 0, KEY_READ | KEY_WOW64_64KEY, &root) != ERROR_SUCCESS) return result;
    for (DWORD index = 0;; ++index) {
        wchar_t name[512]{};
        DWORD length = 512;
        auto status = RegEnumKeyExW(root, index, name, &length, nullptr, nullptr, nullptr, nullptr);
        if (status == ERROR_NO_MORE_ITEMS) break;
        if (status != ERROR_SUCCESS) continue;
        HKEY key{};
        if (RegOpenKeyExW(root, name, 0, KEY_READ, &key) != ERROR_SUCCESS) continue;
        std::wstring id = ReadRegistry(key, L"CLSID"), description = ReadRegistry(key, L"Description");
        RegCloseKey(key);
        CLSID clsid{};
        if (FAILED(CLSIDFromString(id.c_str(), &clsid))) continue;
        wchar_t canonical[40]{};
        StringFromGUID2(clsid, canonical, 40);
        const std::wstring stable = L"asio:" + std::wstring(canonical);
        if (std::none_of(result.begin(), result.end(), [&](const auto& d) { return d.id == stable; }))
            result.push_back({stable, description.empty() ? name : description});
    }
    RegCloseKey(root);
    return result;
}
void PrintAsioDriversJson() {
    for (const auto& d : EnumerateAsioDrivers()) {
        // Enumeration never opens a driver or changes a running application's clock.
        std::cout << "{\"id\":" << JsonString(d.id) << ",\"name\":" << JsonString(d.name) << "}\n";
    }
}
void ProbeAsioDriverJson(const std::wstring& id) {
    Driver driver(id);
    long inputs{}, outputs{}, minimum{}, maximum{}, preferred{}, granularity{};
    double rate{};
    Check(driver.api->getChannels(&inputs, &outputs), "getChannels");
    Check(driver.api->getBufferSize(&minimum, &maximum, &preferred, &granularity), "getBufferSize");
    Check(driver.api->getSampleRate(&rate), "getSampleRate");
    if (outputs < 0 || outputs > 256 || !ValidRate(rate)) throw std::runtime_error("ASIO: invalid driver capabilities.");
    std::cout << "{\"name\":" << JsonString(driver.name) << ",\"inputChannels\":" << inputs
        << ",\"outputChannels\":" << outputs << ",\"sampleRate\":" << rate
        << ",\"minimumBuffer\":" << minimum << ",\"maximumBuffer\":" << maximum
        << ",\"preferredBuffer\":" << preferred << ",\"granularity\":" << granularity
        << ",\"supportedRates\":[";
    bool first = true;
    for (double candidate : {32000.,44100.,48000.,88200.,96000.,176400.,192000.}) {
        if (driver.api->canSampleRate(candidate) == ASE_OK) {
            if (!first) std::cout << ',';
            first = false;
            std::cout << candidate;
        }
    }
    std::cout << "],\"channels\":[";
    for (long i = 0; i < outputs; ++i) {
        ASIOChannelInfo channel{};
        channel.channel = i;
        Check(driver.api->getChannelInfo(&channel), "getChannelInfo");
        if (i) std::cout << ',';
        std::cout << "{\"number\":" << i + 1 << ",\"name\":" << JsonString(Wide(channel.name))
                  << ",\"sampleType\":" << channel.type << '}';
    }
    std::cout << "]}\n";
}
void ShowAsioControlPanel(const std::wstring& id) { Driver driver(id); Check(driver.api->controlPanel(), "controlPanel"); }

struct AsioRenderer::Impl {
    AsioDeviceConfig config;
    double gain, targetGain, rate = 48000, source = 0;
    std::unique_ptr<Driver> driver;
    std::vector<ASIOBufferInfo> buffers;
    std::vector<long> types;
    std::vector<int> bytes;
    std::vector<double> gains;
    std::vector<float> producerFrame, callbackFrame;
    std::array<float,32> sincFrame{};
    ClockRateServo clockServo;
    AudioGapRamp gapRamp;
    bool clockSync=false,inputComplete=false;
    std::uint64_t primeRendered{},seenClockSequence{};
    std::atomic<std::uint64_t> clockSequence{0},clockNs{0};
    std::atomic<double> clockConsumed{0};
    std::atomic<double> callbackStep{1};
    std::unique_ptr<AsioPcmRing> ring;
    WinHandle wake{CreateEventW(nullptr, FALSE, FALSE, nullptr)};
    ASIOCallbacks callbacks{};
    long bufferFrames{}, minimum{}, preferred{}, outputLatency{};
    std::atomic<bool> active{false}, countStarvation{false}, hold{false};
    std::atomic<unsigned> pending{0};
    std::atomic<std::uint64_t> starved{0}, rendered{0};
    std::atomic<double> consumed{0};
    Diagnostics* diagnostics{};
    std::atomic<std::uint64_t> callbackCount{0},maxGapNs{0},maxWorkNs{0},resets{0},resyncs{0},rateChanges{0},latencyChanges{0};
    std::uint64_t previousBeginNs{},clippedOutputSamples{};
    bool started = false, created = false, outputReady = false, feeding = false;
    std::chrono::steady_clock::time_point lastCallback = std::chrono::steady_clock::now();
    std::uint64_t previousRendered = 0;
    // ASIO callbacks have no context argument. Exactly one driver per process.
    static inline std::atomic<Impl*> instance{nullptr};

    Impl(const AsioDeviceConfig& value, const LegacyProcessing& processing)
        : config(value), gain(processing.gain), targetGain(processing.gain) {
        if (!IsAsioEndpoint(config.endpointFilter))
            throw std::runtime_error("所有输出声道必须使用同一个 ASIO 驱动。");
        if (!std::isfinite(gain) || gain < 0 || gain > 1) throw std::runtime_error("ASIO：输出增益无效。");
        if (!config.sourceChannels || config.sourceChannels > 32 ||
            config.sourceChannelIndices.empty() || config.sourceChannelIndices.size() > 32 ||
            (!config.physicalChannels.empty() && config.physicalChannels.size() != config.sourceChannelIndices.size()) ||
            std::any_of(config.sourceChannelIndices.begin(), config.sourceChannelIndices.end(),
                [&](auto index) { return index >= config.sourceChannels; })) {
            throw std::invalid_argument("Invalid ASIO PCM channel configuration");
        }
        gains = processing.channelGains.empty() ? std::vector<double>(config.sourceChannelIndices.size(), 1.0) : processing.channelGains;
        source = -processing.delayMilliseconds * 48;
        consumed.store(source);
        Impl* expected = nullptr;
        if (!instance.compare_exchange_strong(expected, this)) throw std::runtime_error("ASIO：当前进程已打开一个驱动。");
        try { Open(); } catch (...) { Close(); instance.store(nullptr); throw; }
    }
    ~Impl() { Close(); instance.store(nullptr, std::memory_order_release); }
    void Open() {
        const auto& route = config;
        driver = std::make_unique<Driver>(route.endpointFilter);
        long inputs{}, outputs{}, maximum{}, granularity{};
        Check(driver->api->getChannels(&inputs, &outputs), "getChannels");
        if (outputs <= 0 || outputs > 256) throw std::runtime_error("ASIO：驱动返回的输出声道数无效。");
        const std::size_t channels = route.sourceChannelIndices.size();
        if (channels == 0 || channels > 32 || channels > static_cast<std::size_t>(outputs))
            throw std::runtime_error("ASIO：驱动输出声道数不足以容纳此布局。");
        buffers.assign(channels, {}); types.resize(channels); bytes.resize(channels); gains.resize(channels);
        producerFrame.resize(channels); callbackFrame.resize(channels);
        std::set<long> unique;
        for (std::size_t i = 0; i < channels; ++i) {
            const long physical = route.physicalChannels.empty() ? static_cast<long>(i) : route.physicalChannels.at(i);
            if (physical < 0 || physical >= outputs || !unique.insert(physical).second)
                throw std::runtime_error("ASIO：物理输出通道超出范围或重复。");
            buffers[i].isInput = ASIOFalse;
            buffers[i].channelNum = physical;
            ASIOChannelInfo info{}; info.channel = physical;
            Check(driver->api->getChannelInfo(&info), "getChannelInfo");
            types[i] = info.type; bytes[i] = AsioSampleBytes(info.type);
        }
        Check(driver->api->getSampleRate(&rate), "getSampleRate");
        const double requested = route.asioSampleRate;
        if (requested > 0) {
            if (!ValidRate(requested)) throw std::runtime_error("ASIO：请求的采样率无效。");
            Check(driver->api->canSampleRate(requested), "canSampleRate");
            if (std::abs(rate - requested) > .1) Check(driver->api->setSampleRate(requested), "setSampleRate");
            Check(driver->api->getSampleRate(&rate), "getSampleRate");
            if (std::abs(rate - requested) > .1) throw std::runtime_error("ASIO：驱动未接受请求的采样率。");
        } else if (std::abs(rate - 48000) > .1 && driver->api->canSampleRate(48000) == ASE_OK) {
            Check(driver->api->setSampleRate(48000), "setSampleRate 48000");
            Check(driver->api->getSampleRate(&rate), "getSampleRate");
        }
        if (!ValidRate(rate)) throw std::runtime_error("ASIO：驱动返回的采样率无效。");
        Check(driver->api->getBufferSize(&minimum, &maximum, &preferred, &granularity), "getBufferSize");
        bufferFrames = SelectAsioBuffer(minimum, maximum, preferred, granularity, route.asioBufferFrames);
        gapRamp.Reset(rate);
        // A zero size follows the driver's control panel; explicit sizes are validated.
        ring = std::make_unique<AsioPcmRing>(static_cast<std::size_t>(bufferFrames) * 4, channels);
        callbacks = {BufferSwitch, RateChanged, Message, TimeSwitch};
#ifdef NSP_ASIO_NETWORK_RETURN_TEST
        Check(CreateAsioReturnBuffers(driver->api.Get(), buffers, bufferFrames, &callbacks), "createBuffers duplex test");
#else
        Check(driver->api->createBuffers(buffers.data(), static_cast<long>(channels), bufferFrames, &callbacks), "createBuffers");
#endif
        created = true;
        for (std::size_t c = 0; c < channels; ++c) for (int half = 0; half < 2; ++half) {
            if (!buffers[c].buffers[half]) throw std::runtime_error("ASIO：驱动返回了空输出缓冲区。");
            std::memset(buffers[c].buffers[half], 0, static_cast<std::size_t>(bufferFrames) * bytes[c]);
        }
        long inputLatency{};
        Check(driver->api->getLatencies(&inputLatency, &outputLatency), "getLatencies");
        outputReady = driver->api->outputReady() == ASE_OK;
        pending.store(0);
    }
    void Close() noexcept {
        active.store(false, std::memory_order_release);
        if (driver) {
            if (started) driver->api->stop();
            started = false;
            if (created) driver->api->disposeBuffers();
            created = false;
            driver.reset();
        }
    }
    static void BufferSwitch(long index, ASIOBool) noexcept {
        auto* self = instance.load(std::memory_order_acquire);
        if (self) self->Render(index);
    }
    static ASIOTime* TimeSwitch(ASIOTime* time, long index, ASIOBool direct) noexcept {
        (void)direct;
        if(auto* self=instance.load(std::memory_order_acquire))self->Render(index,time);
        return time;
    }
    static void RateChanged(ASIOSampleRate) noexcept {
        if (auto* self = instance.load(std::memory_order_acquire)) { ++self->rateChanges;self->pending.fetch_or(1); SetEvent(self->wake.Get()); }
    }
    static long Message(long selector, long value, void*, double*) noexcept {
        if (selector == kAsioSelectorSupported) return value == kAsioResetRequest ||
            value == kAsioBufferSizeChange || value == kAsioResyncRequest ||
            value == kAsioLatenciesChanged || value == kAsioEngineVersion || value == kAsioSupportsTimeInfo;
        if (selector == kAsioEngineVersion) return 2;
        if (selector == kAsioSupportsTimeInfo) return 1;
        auto* self = instance.load(std::memory_order_acquire);
        if (!self) return 0;
        if (selector == kAsioResetRequest || selector == kAsioBufferSizeChange || selector == kAsioResyncRequest) {
            if(selector==kAsioResyncRequest)++self->resyncs;else ++self->resets;
            self->pending.fetch_or(1); SetEvent(self->wake.Get()); return 1;
        }
        if (selector == kAsioLatenciesChanged) { ++self->latencyChanges;self->pending.fetch_or(2); return 1; }
        return 0;
    }
    void Render(long index,ASIOTime* time=nullptr) noexcept {
        if (!active.load(std::memory_order_acquire) || index < 0 || index > 1) return;
#ifdef NSP_ASIO_NETWORK_RETURN_TEST
        CaptureAsioReturn(index);
#endif
        const auto begin=Diagnostics::NowNs();
        const auto queuedBefore=ring->Available();
        const auto channels = buffers.size();
        std::uint64_t missing = 0;
        double position = consumed.load(std::memory_order_relaxed);
        const bool held=hold.load(std::memory_order_acquire);
        const bool silence = held||(pending.load(std::memory_order_relaxed) & 1) != 0;
        for (long f = 0; f < bufferFrames; ++f) {
            double nextPosition=position;
            const bool popped=!silence&&ring->Pop(callbackFrame.data(),nextPosition);
            const bool have=popped&&(!clockSync||nextPosition>position);
            if(have)position=nextPosition;
            else if(clockSync&&config.advanceOnStarvation&&!held&&countStarvation.load(std::memory_order_relaxed))position+=callbackStep.load(std::memory_order_relaxed);
            if (!have) ++missing;
            const bool ramp=clockSync&&countStarvation.load(std::memory_order_relaxed)&&!silence;
            if(ramp)gapRamp.Process(callbackFrame,have);
            for (std::size_t c = 0; c < channels; ++c) {
                auto* dest = static_cast<unsigned char*>(buffers[c].buffers[index]) + static_cast<std::size_t>(f) * bytes[c];
                WriteAsioSample(dest, types[c], (have||ramp) ? callbackFrame[c] : 0, !config.legacyOutputClamp);
            }
        }
#ifdef NSP_ASIO_NETWORK_RETURN_TEST
        CaptureAsioOutput(index);
#endif
        consumed.store(position, std::memory_order_release);
        clockSequence.fetch_add(1,std::memory_order_acq_rel);
        auto clockStamp=begin;
#ifdef SPATIAL_AUDIO_ASIO_TESTING
        if(const auto simulated=TestAsioClockNs())clockStamp=simulated;
#endif
        clockNs.store(clockStamp,std::memory_order_relaxed);clockConsumed.store(position,std::memory_order_relaxed);
        clockSequence.fetch_add(1,std::memory_order_release);
        if (!held&&countStarvation.load(std::memory_order_relaxed)) starved.fetch_add(missing, std::memory_order_relaxed);
        rendered.fetch_add(bufferFrames, std::memory_order_relaxed);
        if (outputReady) driver->api->outputReady();
        const auto end=Diagnostics::NowNs();
        ++callbackCount;
        if(previousBeginNs&&begin-previousBeginNs>maxGapNs.load(std::memory_order_relaxed))maxGapNs.store(begin-previousBeginNs,std::memory_order_relaxed);
        if(end-begin>maxWorkNs.load(std::memory_order_relaxed))maxWorkNs.store(end-begin,std::memory_order_relaxed);
        if(diagnostics&&diagnostics->AudioEnabled()){
            AsioDiagnostic info{};info.beginNs=begin;info.endNs=end;info.previousBeginNs=previousBeginNs;
            info.sourceFrame=position;info.sampleRate=rate;info.frames=bufferFrames;info.channels=static_cast<std::uint32_t>(channels);
            info.missing=static_cast<std::uint32_t>(missing);info.queuedBefore=static_cast<std::uint32_t>(queuedBefore);info.queuedAfter=static_cast<std::uint32_t>(ring->Available());
            info.index=static_cast<std::uint32_t>(index);info.stateFlags=pending.load(std::memory_order_relaxed)|(countStarvation.load(std::memory_order_relaxed)?256U:0U);
            if(time){info.timeFlags=time->timeInfo.flags;info.samplePosition=(static_cast<std::uint64_t>(time->timeInfo.samplePosition.hi)<<32)|time->timeInfo.samplePosition.lo;info.systemTime=(static_cast<std::uint64_t>(time->timeInfo.systemTime.hi)<<32)|time->timeInfo.systemTime.lo;}
            std::array<DiagnosticPart,33> parts{};parts[0]={&info,sizeof(info)};
            for(std::size_t c=0;c<channels;++c){info.sampleTypes[c]=types[c];parts[c+1]={buffers[c].buffers[index],static_cast<std::size_t>(bufferFrames)*bytes[c]};}
            diagnostics->Record(Diagnostics::Asio,3,begin,{parts.data(),channels+1});
        }
        previousBeginNs=begin;
        const auto complete=Diagnostics::NowNs();if(complete-begin>maxWorkNs.load(std::memory_order_relaxed))maxWorkNs.store(complete-begin,std::memory_order_relaxed);
        SetEvent(wake.Get());
    }
    unsigned SoftwarePeriods() const {return clockSync?3U:2U;}
    void Fill(const InterleavedPcmQueue& queue) {
        if (queue.Channels() != config.sourceChannels)
            throw std::invalid_argument("ASIO PCM queue channel count mismatch");
        const auto& route = config;
        if(clockSync&&feeding&&!hold.load()&&rendered.load(std::memory_order_acquire)>primeRendered){
            for(unsigned attempt=0;attempt<3;++attempt){
                const auto sequence=clockSequence.load(std::memory_order_acquire);
                if(sequence&1)continue;
                const auto stamp=clockNs.load(std::memory_order_relaxed);const auto frame=clockConsumed.load(std::memory_order_relaxed);
                if(sequence!=clockSequence.load(std::memory_order_acquire))continue;
                if(sequence!=seenClockSequence){clockServo.Observe(stamp,frame);seenClockSequence=sequence;}break;
            }
        }
        const double step=48000./rate*(clockSync?clockServo.Ratio():1.);
        callbackStep.store(step,std::memory_order_relaxed);
        // Missing physical output has already elapsed. Keep the virtual sample
        // clock advancing through that silence; never replay it as stale audio
        // or ask the rate servo to erase a large dropout by speeding up.
        if(clockSync)source=std::max(source,consumed.load(std::memory_order_acquire));
        // HQ input rendering and output sinc share this worker. Three prepared
        // periods cover observed clustered callbacks while that work runs.
        // This fixed headroom stays inside the selected capture delay.
        const auto target = static_cast<std::uint64_t>(bufferFrames) * SoftwarePeriods();
        const auto available = ring->Available();
        auto frames = available < target ? target - available : 0;
        while (frames--) {
            if (source >= static_cast<double>(queue.EndFrame())) break;
            if(clockSync&&!inputComplete&&source+HighQualitySinc::Radius(step)>=double(queue.EndFrame()))break;
            gain += std::clamp(targetGain-gain,-1.0/(rate*.01),1.0/(rate*.01));
            if(clockSync)HighQualitySinc::Sample(source,step,queue.Channels(),[&](std::int64_t f){return queue.Frame(f);},{sincFrame.data(),queue.Channels()});
            for (std::size_t c = 0; c < route.sourceChannelIndices.size(); ++c) {
                double sample = 0;
                if(clockSync)sample=sincFrame[route.sourceChannelIndices[c]];
                else if (source >= 0) {
                    const auto a = static_cast<std::uint64_t>(source);
                    const auto b = std::min(a + 1, queue.EndFrame() - 1);
                    const double fraction = source - std::floor(source);
                    sample = queue.Sample(a, route.sourceChannelIndices[c]) * (1 - fraction) + queue.Sample(b, route.sourceChannelIndices[c]) * fraction;
                }
                const auto value=sample*gains[c]*gain;
                if(config.legacyOutputClamp&&std::abs(value)>1)++clippedOutputSamples;
                producerFrame[c] = static_cast<float>(config.legacyOutputClamp?std::clamp(value, -1.0, 1.0):value);
            }
            const double next = source + step;
            if (!ring->Push(producerFrame.data(), next)) break;
            source = next;
        }
    }
    void Start() {
        if (started) return;
        const auto begin=Diagnostics::NowNs();
        if(diagnostics)diagnostics->Event("{\"event\":\"asio_start_begin\"}");
        active.store(true, std::memory_order_release);
        auto result = driver->api->start();
        if (result != ASE_OK && result != ASE_SUCCESS) { active.store(false); Check(result, "start"); }
        started = true;
        previousRendered = rendered.load();
        lastCallback = std::chrono::steady_clock::now();
        if(diagnostics)diagnostics->Event("{\"event\":\"asio_start_end\",\"durationMs\":"+std::to_string((Diagnostics::NowNs()-begin)/1e6)+"}");
    }
};
AsioRenderer::AsioRenderer(const AsioDeviceConfig& config)
    : AsioRenderer(config, LegacyProcessing{}) {}
AsioRenderer::AsioRenderer(const AsioDeviceConfig& config, const LegacyProcessing& processing)
    : impl_(std::make_unique<Impl>(config, processing)) {}
AsioRenderer::~AsioRenderer() = default;
void AsioRenderer::Prime(const InterleavedPcmQueue& queue) {
    // Start may have been called with an empty ring to finish a blocking
    // driver handshake before capture. Arm PCM on the worker, without another
    // driver start/stop. Idle callbacks write zero and do not consume source.
    if (impl_->feeding) throw std::runtime_error("ASIO: playback is already primed.");
    impl_->Fill(queue);
    impl_->primeRendered=impl_->rendered.load();impl_->clockServo.Reset();
    impl_->feeding=true;
}
void AsioRenderer::Start() { impl_->Start(); }
void AsioRenderer::SetDiagnostics(Diagnostics* diagnostics) {
    impl_->diagnostics=diagnostics;
    if(diagnostics){std::ostringstream out;out<<"{\"event\":\"asio_open\",\"driver\":"<<JsonString(impl_->driver->name)<<",\"sampleRate\":"<<impl_->rate<<",\"bufferFrames\":"<<impl_->bufferFrames<<",\"outputLatencyFrames\":"<<impl_->outputLatency<<",\"outputReady\":"<<(impl_->outputReady?"true":"false")<<",\"channels\":[";
        for(std::size_t c=0;c<impl_->buffers.size();++c){if(c)out<<',';out<<"{\"physicalZeroBased\":"<<impl_->buffers[c].channelNum<<",\"sampleType\":"<<impl_->types[c]<<'}';}out<<"]}";diagnostics->Event(out.str());}
}
void AsioRenderer::EnableClockSync(bool enabled) {
    if(impl_->feeding)throw std::runtime_error("Clock sync must be selected before playback");
    impl_->clockSync=enabled;
}
void AsioRenderer::SetInputComplete(){impl_->inputComplete=true;}
void AsioRenderer::SetGain(double gain) {
    if(!std::isfinite(gain) || gain<0 || gain>1) throw std::invalid_argument("Invalid output gain");
    impl_->targetGain=gain;
}
void AsioRenderer::Service(const InterleavedPcmQueue& queue, bool count, DWORD timeout,HANDLE captureWake) {
    auto& p = *impl_;
    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&message); DispatchMessageW(&message);
    }
    p.countStarvation.store(count);
    const unsigned requests = p.pending.exchange(0);
    if (requests & 1) {
        if (!p.config.reopenOnReset)
            throw std::runtime_error("ASIO device reset requested; stop and restart explicitly");
        if(p.diagnostics)p.diagnostics->Event("{\"event\":\"asio_reset_begin\"}");
        // Stop joins the callback before replacing any callback-owned storage.
        p.Close();
        p.source = p.consumed.load();
        p.clockServo.Reset();p.primeRendered=p.rendered.load();
        p.Open();SetDiagnostics(p.diagnostics);if(p.feeding)p.Fill(queue);p.Start();
        if(p.diagnostics)p.diagnostics->Event("{\"event\":\"asio_reset_end\"}");
    } else if (requests & 2) {
        long input{};
        Check(p.driver->api->getLatencies(&input, &p.outputLatency), "getLatencies");
    }
    if(p.feeding)p.Fill(queue);
    if(captureWake) { const HANDLE events[]{p.wake.Get(),captureWake};WaitForMultipleObjects(2,events,FALSE,timeout); }
    else WaitForSingleObject(p.wake.Get(), timeout);
    if(p.feeding)p.Fill(queue);
    const auto rendered = p.rendered.load();
    if (rendered != p.previousRendered) { p.previousRendered = rendered; p.lastCallback = std::chrono::steady_clock::now(); }
    if (std::chrono::steady_clock::now() - p.lastCallback > std::chrono::seconds(3))
        throw std::runtime_error("ASIO：驱动停止提供回调。请检查设备连接并重新启动音频桥。");
}
void AsioRenderer::DiscardConsumed(InterleavedPcmQueue& queue) const { queue.DiscardBefore(std::max(0.,impl_->consumed.load()-(impl_->clockSync?HighQualitySinc::Radius(48000./impl_->rate*1.002)+2:0))); }
std::uint64_t AsioRenderer::BufferedFrames(const InterleavedPcmQueue& queue) const {return queue.FramesAvailable(impl_->consumed.load());}
std::uint64_t AsioRenderer::MinimumFramesAvailable(const InterleavedPcmQueue& queue) const { return queue.FramesAvailable(impl_->source); }
std::uint32_t AsioRenderer::MaximumBufferFrames() const { return static_cast<std::uint32_t>(std::ceil(impl_->bufferFrames * impl_->SoftwarePeriods() * 48000.0 / impl_->rate*(impl_->clockSync?1.002:1.)))+(impl_->clockSync?HighQualitySinc::Radius(48000./impl_->rate*1.002)+2:0); }
double AsioRenderer::PeriodMilliseconds() const {return impl_->bufferFrames*1000.0/impl_->rate;}
std::uint64_t AsioRenderer::RequiredWriteFrame() const {
    if(!impl_->started||!impl_->feeding)return 0;
    return static_cast<std::uint64_t>(std::max(0.,std::ceil(impl_->consumed.load(std::memory_order_acquire))))+MaximumBufferFrames();
}
bool AsioRenderer::IsDrained(const InterleavedPcmQueue& queue) const { return queue.FramesAvailable(impl_->source) == 0 && impl_->ring->Available() == 0; }
bool AsioRenderer::IsStarted() const { return impl_->started; }
void AsioRenderer::Pause() {
    auto& p=*impl_;
    if (!p.started) return;
    p.active.store(false,std::memory_order_release);
    Check(p.driver->api->stop(),"pause");
    p.started=false;
}
void AsioRenderer::Hold(bool enabled) {
    if(!impl_->started||!impl_->feeding)throw std::runtime_error("Hold requires active primed playback");
    if(!enabled){impl_->clockServo.Reset();impl_->primeRendered=impl_->rendered.load();}
    impl_->hold.store(enabled,std::memory_order_release);
}
void AsioRenderer::Stop() {
    auto& p = *impl_;
    if (!p.started) return;
    p.countStarvation.store(false);
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (p.ring->Available() && std::chrono::steady_clock::now() < deadline) WaitForSingleObject(p.wake.Get(), 10);
    Sleep(static_cast<DWORD>(std::clamp(p.outputLatency * 1000.0 / p.rate, 0.0, 1000.0)));
    p.active.store(false);
    Check(p.driver->api->stop(), "stop");
    p.started = false;
}
std::vector<EndpointRenderStats> AsioRenderer::Stats() const {
    const auto& p = *impl_;
    EndpointRenderStats stats;
    stats.routeName = p.config.name;
    stats.endpointName = L"ASIO: " + p.driver->name + L" (" + std::to_wstring(static_cast<int>(p.rate)) + L" Hz)";
    stats.isMaster = true;
    stats.bufferFrames = p.bufferFrames;
    stats.bufferMilliseconds = p.bufferFrames * 1000.0 / p.rate;
    stats.driverLatencyMilliseconds = p.outputLatency * 1000.0 / p.rate;
    stats.streamLatencyMilliseconds = (p.outputLatency + p.bufferFrames * p.SoftwarePeriods()) * 1000.0 / p.rate;
    stats.defaultPeriodFrames = p.preferred; stats.minimumPeriodFrames = p.minimum; stats.selectedPeriodFrames = p.bufferFrames;
    stats.starvationFrames = p.starved.load(); stats.clockSeconds = p.rendered.load() / p.rate;
    stats.callbacks=p.callbackCount.load();stats.callbackMaxGapNs=p.maxGapNs.load();stats.callbackMaxWorkNs=p.maxWorkNs.load();
    stats.resets=p.resets.load();stats.resyncs=p.resyncs.load();stats.rateChanges=p.rateChanges.load();stats.latencyChanges=p.latencyChanges.load();stats.clippedOutputSamples=p.clippedOutputSamples;
    stats.asioRingFrames=static_cast<std::uint32_t>(p.ring->Available());stats.sourceFrame=p.source;stats.sampleRate=p.rate;
    if(p.clockSync){stats.resampleAdjustmentPpm=p.clockServo.Ppm();stats.phaseErrorMilliseconds=p.clockServo.PhaseMs();stats.clockDiscontinuities=p.clockServo.Resets();stats.resamplerLookaheadMilliseconds=HighQualitySinc::Radius(48000./p.rate*1.002)/48.;}
    return {stats};
}
}
