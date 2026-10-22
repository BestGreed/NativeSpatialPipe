// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "speaker_layout.h"
#include "metadata_renderer.h"
#include <cstdint>
#include <map>
#include <memory>
#include <span>
#include <vector>

namespace spatial {
// NSP1 is a local, versioned transport. The payload retains the original properties.
// All fields are little endian; Windows x64 is the only supported capture platform.
struct NativePacketHeader {
    std::uint32_t magic{0x3150534e}, bytes{};
    std::uint64_t stream{}, sequence{}, captureNs{};
    std::uint32_t sampleRate{48000}, objects{}, frames{}, flags{};
};
static_assert(sizeof(NativePacketHeader) == 48);
struct NativeProperty {
    std::uint32_t candidateId{}, frames{}, type{}, flags{};
    float x{}, y{}, z{}, gain{};
};
static_assert(sizeof(NativeProperty) == 32);
inline constexpr std::uint32_t NativeStateCheckpoint=1;
struct NativePacket {
    NativePacketHeader header;
    std::vector<NativeProperty> properties;
    std::vector<float> planar;
    // Optional trailer: witnessed metadata, never a replacement for raw evidence.
    std::vector<NativeProperty> checkpoint;
    // Local receiver telemetry; not part of the NSP1 wire format.
    std::uint64_t receivedNs{}, enqueuedNs{};
    std::uint32_t receiverDepth{};
};
void ValidateNativeHeader(const NativePacketHeader& header);
NativePacket DecodeNativePacket(const NativePacketHeader& header, std::span<const std::uint8_t> bytes);
struct NativeSpatialStats {
    std::uint64_t packets{}, frames{}, creates{}, retires{}, updates{}, gaps{};
    std::uint64_t unknownStateBlocks{}, unknownTypes{}, unknownFlagBlocks{}, nonzeroDynamicBlocks{};
    std::uint64_t lateFrames{}, silentFrames{}, clippedSamples{}, expiredStreams{};
    std::uint64_t identityCollisionPackets{}, ambiguousObjects{}, silentCollisionBlocks{};
    std::uint64_t collisionSnapshotBlocks{}, ambiguousStateBlocks{};
    std::uint64_t checkpointRestores{};
    std::uint64_t parkedObjectBlocks{}, parkedObjectRestarts{};
    std::uint64_t clockDiscontinuities{};
    double lastClockJumpMs{};
    double clockMaxAbsPpm{},clockMaxAbsPhaseMs{};
};
class NativeSpatialScene {
public:
    explicit NativeSpatialScene(const SpeakerLayout& layout);
    std::vector<float> Render(const NativePacket& packet);
    std::vector<float> FlushStream(std::uint64_t stream);
    std::vector<float> BreakRendering(std::uint64_t stream);
    void RetireStream(std::uint64_t stream);
    void Reset();
    const NativeSpatialStats& Stats() const { return stats_; }
private:
    struct Slot {
        std::uint32_t candidateId{}, type{};
        unsigned lane{};
        bool positionKnown{}, gainKnown{}, renderStarted{}, parked{};
        float x{}, y{}, z{}, gain{};
    };
    struct Stream {
        bool seen{};
        std::uint64_t sequence{};
        // candidateId is lossy, not globally unique. Include object type.
        // Bit 63 denotes a packet-local collision lane, never retained as state.
        // Ordinary identities remain independent of packet slot order.
        std::map<std::uint64_t, Slot> slots;
        std::unique_ptr<MetadataRenderer> renderer;
    };
    SpeakerLayout layout_;
    std::map<std::uint64_t, Stream> streams_;
    NativeSpatialStats stats_;
};
}
