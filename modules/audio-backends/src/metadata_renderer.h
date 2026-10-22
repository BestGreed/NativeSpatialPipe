// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "speaker_layout.h"
#include <array>
#include <cstdint>
#include <span>
#include <vector>

namespace spatial {
// Stable ABI shared by C++, Cinema and the ADM adapter. Cartesian coordinates:
// X right [-1,1], Y front [-1,1], Z up [0,1]. Events precede their sample.
enum MetadataFlags : std::uint32_t {
    MetadataBegin=1, MetadataEnd=2, MetadataPosition=4, MetadataGain=8,
    MetadataInactive=16, MetadataBed=32
};
struct MetadataEvent {
    std::uint32_t offset{}, object{}, flags{};
    std::int32_t bed{-1}; // input semantic identity in Canonical916Layout; -1 = point object
    float x{}, y{1}, z{}, gain{1};
    std::uint32_t rampSamples{};
};
static_assert(sizeof(MetadataEvent)==36);

SpeakerLayout Canonical916Layout();

// Bounded streaming adapter for the independently reconstructed offline model.
// Exactly Period() frames of lookahead are needed to select the LAST metadata
// event in each period. Process emits equally many frames, initially silence;
// Flush emits the final Period() frames. Remove that prefix for offline output.
// Call on the decode worker, never the hardware callback. No disk/DLL loading
// or reference audio access occurs during Process.
class MetadataRenderer {
public:
    MetadataRenderer(const SpeakerLayout& layout, unsigned rate, unsigned objects);
    unsigned Period() const { return period_; }
    unsigned Channels() const { return channels_; }
    unsigned Objects() const { return objects_; }
    void Process(std::span<const float> interleaved, unsigned frames,
                 std::span<const MetadataEvent> events, std::span<float> output);
    unsigned Flush(std::span<float> output);
    void Reset();
private:
    struct Frame {
        float pcm{};
        std::array<float,3> position{};
        std::uint64_t generation{};
        int bed{-1};
    };
    struct Lane {
        bool live{}, inactive{};
        std::uint64_t generation{}, renderedGeneration{}, gainStart{};
        int bed{-1};
        std::array<float,3> position{0,1,0}, smoothed{};
        double initial{1}, target{1};
        unsigned ramp{};
        std::array<float,16> old{}, delta{};
        std::array<unsigned,16> remaining{};
        std::array<float,512> cache{};
        std::vector<Frame> frames;
    };
    double Amplitude(const Lane& lane) const;
    void Apply(const MetadataEvent& event);
    void RenderPeriod(unsigned frames);
    void GainBlock(Lane& lane, const std::array<float,16>& target, bool changed);
    SpeakerLayout layout_;
    std::array<std::array<float,16>,16> bedGains_{};
    std::vector<Lane> lanes_;
    std::vector<float> ready_;
    unsigned period_, channels_, objects_, fill_{}, usedLanes_{};
    std::uint64_t position_{};
    float oldCoefficient_, newCoefficient_;
    bool finished_{};
};
}
