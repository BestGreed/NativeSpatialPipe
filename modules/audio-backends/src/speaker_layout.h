// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <array>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>
namespace spatial {
enum class SpatialRenderMode { Native, HomeTheaterSource };
inline const char* RenderModeName(SpatialRenderMode mode) { return mode==SpatialRenderMode::HomeTheaterSource ? "home-theater-source" : "native"; }
// Stable internal identities also accept the public L/R/C, LW/RW, LS/RS,
// LRS/RRS and LTF/RTF/LTM/RTM/LTR/RTR notation.
std::wstring SpeakerKey(std::wstring_view name);
struct SpeakerDefinition { std::wstring name; double azimuthDegrees{},elevationDegrees{},trimDb{}; };
struct OutputRouteDefinition {
    std::wstring name,endpointFilter;
    std::vector<std::size_t> speakerIndices;
    double delayMilliseconds{};
    std::vector<long> physicalChannels;
    long asioBufferFrames{};
    double asioSampleRate{48000};
};
struct SpeakerLayout {
    SpatialRenderMode renderMode{SpatialRenderMode::Native};
    std::wstring name;
    std::vector<SpeakerDefinition> speakers;
    std::vector<OutputRouteDefinition> outputs;
    std::size_t masterOutput{};
    std::optional<std::size_t> FindSpeaker(std::wstring_view name) const;
};
SpeakerLayout LoadSpeakerLayout(const std::filesystem::path& path);
std::vector<double> PanAdmOarPoint(const SpeakerLayout&,double x,double y,double z,double anchorTolerance=0);
}
