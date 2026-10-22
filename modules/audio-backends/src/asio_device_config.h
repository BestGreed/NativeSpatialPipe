// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <cstddef>
#include <string>
#include <vector>

namespace spatial {
// Device configuration only. Input is interleaved 48 kHz PCM; source indices
// refer to PCM channels, never to a speaker geometry or object identity.
struct AsioDeviceConfig {
    std::wstring name, endpointFilter;
    std::size_t sourceChannels{};
    std::vector<std::size_t> sourceChannelIndices;
    std::vector<long> physicalChannels;
    long asioBufferFrames{};
    double asioSampleRate{48000};
    bool reopenOnReset{true}; // Legacy policy; NSP's C entry explicitly disables it.
    bool legacyOutputClamp{true}; // NSP common output owns limiting; old entry keeps its sound.
    bool advanceOnStarvation{true}; // Legacy skip policy; NSP preserves source during rebuffer.
};
}
