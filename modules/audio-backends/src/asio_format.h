// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <cstddef>
#include <cstdint>
#include <atomic>
#include <vector>

namespace spatial {
// ASIO PCM formats, including endian and 32-bit containers with fewer valid bits.
int AsioSampleBytes(long type);
void WriteAsioSample(void* destination, long type, double sample, bool preserveFloat=false) noexcept;
long SelectAsioBuffer(long minimum, long maximum, long preferred, long granularity,
                      long requested);

// One producer (decode thread), one consumer (driver callback). No callback locks
// or allocations. The release/acquire pair publishes complete multichannel frames.
class AsioPcmRing {
public:
    AsioPcmRing(std::size_t frames, std::size_t channels);
    std::uint64_t Available() const noexcept;
    std::uint64_t Free() const noexcept;
    bool Push(const float* frame, double sourcePosition) noexcept;
    bool Pop(float* frame, double& sourcePosition) noexcept;
private:
    std::size_t capacity_, channels_;
    std::vector<float> samples_;
    std::vector<double> positions_;
    alignas(64) std::atomic<std::uint64_t> write_{0};
    alignas(64) std::atomic<std::uint64_t> read_{0};
};
}
