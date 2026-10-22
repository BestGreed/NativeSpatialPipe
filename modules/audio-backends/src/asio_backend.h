// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include "audio_queue.h"
#include "asio_device_config.h"
#include <memory>
#include <string_view>

namespace spatial {
class Diagnostics;
struct SpeakerLayout;
struct AsioDriverDescriptor { std::wstring id, name; };
std::vector<AsioDriverDescriptor> EnumerateAsioDrivers();
void PrintAsioDriversJson();
void ProbeAsioDriverJson(const std::wstring& id);
void ShowAsioControlPanel(const std::wstring& id);
bool IsAsioEndpoint(std::wstring_view id);

class AsioRenderer {
public:
    explicit AsioRenderer(const AsioDeviceConfig& config);
    // Legacy engine adapter; implemented separately from the device module.
    AsioRenderer(const SpeakerLayout& layout, double gain, RendererLatencyMode latency);
    ~AsioRenderer();
    void Prime(const InterleavedPcmQueue& queue); // Once; also arms an already-running silent driver.
    void Start(); // Before Prime, callbacks emit silence without advancing source time.
    void Service(const InterleavedPcmQueue& queue, bool countStarvation, DWORD timeout,HANDLE captureWake=nullptr);
    std::uint64_t BufferedFrames(const InterleavedPcmQueue& queue) const;
    void DiscardConsumed(InterleavedPcmQueue& queue) const;
    std::uint64_t MinimumFramesAvailable(const InterleavedPcmQueue& queue) const;
    std::uint32_t MaximumBufferFrames() const;
    double PeriodMilliseconds() const;
    std::uint64_t RequiredWriteFrame() const;
    bool IsDrained(const InterleavedPcmQueue& queue) const;
    bool IsStarted() const;
    void Stop();
    void Pause();
    void Hold(bool enabled); // Silent callbacks without consuming queued source; worker only.
    void SetGain(double gain); // Worker thread only; short ramp in producer.
    void SetDiagnostics(Diagnostics* diagnostics); // before Start, lifetime exceeds driver
    void EnableClockSync(bool enabled=true); // before Prime; live path only
    void SetInputComplete(); // allow the sinc tail to use zero padding at EOF
    std::vector<EndpointRenderStats> Stats() const;
private:
    // Transitional compatibility only. The NSP device entry uses neutral
    // values; shared output processing will own calibration in a later step.
    struct LegacyProcessing {
        double gain{1}, delayMilliseconds{};
        std::vector<double> channelGains;
    };
    AsioRenderer(const AsioDeviceConfig& config, const LegacyProcessing& processing);
    static AsioDeviceConfig LegacyConfig(const SpeakerLayout& layout);
    static LegacyProcessing LegacySettings(const SpeakerLayout& layout, double gain);
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
