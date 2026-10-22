// SPDX-License-Identifier: GPL-3.0-only
#include "asio_backend.h"
#include "speaker_layout.h"
#include <cmath>
#include <stdexcept>

namespace spatial {
AsioDeviceConfig AsioRenderer::LegacyConfig(const SpeakerLayout& layout) {
    if (layout.outputs.size() != 1)
        throw std::invalid_argument("ASIO requires one output route");
    const auto& route = layout.outputs[0];
    return {route.name, route.endpointFilter, layout.speakers.size(),
        route.speakerIndices, route.physicalChannels, route.asioBufferFrames, route.asioSampleRate};
}
AsioRenderer::LegacyProcessing AsioRenderer::LegacySettings(const SpeakerLayout& layout, double gain) {
    if (layout.outputs.size() != 1)
        throw std::invalid_argument("ASIO requires one output route");
    LegacyProcessing result;
    result.gain = gain;
    result.delayMilliseconds = layout.outputs[0].delayMilliseconds;
    for (auto index : layout.outputs[0].speakerIndices)
        result.channelGains.push_back(std::pow(10.0, layout.speakers.at(index).trimDb / 20));
    return result;
}
AsioRenderer::AsioRenderer(const SpeakerLayout& layout, double gain, RendererLatencyMode mode)
    : AsioRenderer(LegacyConfig(layout), LegacySettings(layout, gain)) {
    // The device implementation did not use this legacy parameter.
    (void)mode;
}
}
