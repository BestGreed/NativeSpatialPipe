// SPDX-License-Identifier: GPL-3.0-only
#include "asio_format.h"
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <stdexcept>

namespace spatial {
int AsioSampleBytes(long type) {
    const long base = type >= 16 && type <= 27 ? type - 16 : type;
    switch (base) {
    case 0: return 2;
    case 1: return 3;
    case 2: case 3: case 8: case 9: case 10: case 11: return 4;
    case 4: return 8;
    default: throw std::runtime_error("ASIO：不支持此采样格式（DSD 不是 PCM）。");
    }
}
void WriteAsioSample(void* destination, long type, double value, bool preserveFloat) noexcept {
    if (!std::isfinite(value)) value = 0;
    const bool little = type >= 16;
    const long base = little ? type - 16 : type;
    if(!preserveFloat||(base!=3&&base!=4))value=std::clamp(value,-1.0,1.0);
    int bytes = 4;
    std::uint64_t bits = 0;
    if (base == 3) bits = std::bit_cast<std::uint32_t>(static_cast<float>(value));
    else if (base == 4) { bytes = 8; bits = std::bit_cast<std::uint64_t>(value); }
    else {
        int valid = 32;
        switch (base) {
        case 0: valid = 16; bytes = 2; break;
        case 1: valid = 24; bytes = 3; break;
        case 2: break;
        case 8: valid = 16; break;
        case 9: valid = 18; break;
        case 10: valid = 20; break;
        case 11: valid = 24; break;
        default: return;
        }
        const double scale = std::ldexp(1.0, valid - 1);
        bits = static_cast<std::uint64_t>(static_cast<std::int64_t>(
            std::clamp(std::round(value * scale), -scale, scale - 1)));
    }
    auto* target = static_cast<unsigned char*>(destination);
    for (int i = 0; i < bytes; ++i) {
        target[little ? i : bytes - 1 - i] = static_cast<unsigned char>(bits >> (8 * i));
    }
}
long SelectAsioBuffer(long minimum, long maximum, long preferred, long granularity,
                      long requested) {
    if (minimum <= 0 || maximum < minimum || maximum > 262144 || granularity < -1)
        throw std::runtime_error("ASIO：驱动返回的缓冲区范围无效。");
    auto valid = [&](long n) {
        return n >= minimum && n <= maximum &&
            (granularity == -1 ? (n & (n - 1)) == 0 :
             granularity > 0 ? (n - minimum) % granularity == 0 : n == preferred);
    };
    if (requested > 0) {
        if (!valid(requested)) throw std::runtime_error("ASIO：驱动不支持所选缓冲区大小。");
        return requested;
    }
    if (valid(preferred)) return preferred;
    for (long n = minimum; n <= maximum; ++n) if (valid(n)) return n;
    throw std::runtime_error("ASIO：没有有效的缓冲区大小。");
}
AsioPcmRing::AsioPcmRing(std::size_t frames, std::size_t channels)
    : capacity_(frames), channels_(channels), samples_(frames * channels), positions_(frames) {
    if (!frames || !channels || channels > 256) throw std::invalid_argument("Invalid ASIO ring size");
}
std::uint64_t AsioPcmRing::Available() const noexcept {
    const auto read = read_.load(std::memory_order_acquire);
    return write_.load(std::memory_order_acquire) - read;
}
std::uint64_t AsioPcmRing::Free() const noexcept { return capacity_ - Available(); }
bool AsioPcmRing::Push(const float* frame, double position) noexcept {
    const auto w = write_.load(std::memory_order_relaxed);
    if (w - read_.load(std::memory_order_acquire) == capacity_) return false;
    std::memcpy(samples_.data() + (w % capacity_) * channels_, frame, channels_ * sizeof(float));
    positions_[w % capacity_] = position;
    write_.store(w + 1, std::memory_order_release);
    return true;
}
bool AsioPcmRing::Pop(float* frame, double& position) noexcept {
    const auto r = read_.load(std::memory_order_relaxed);
    if (r == write_.load(std::memory_order_acquire)) return false;
    std::memcpy(frame, samples_.data() + (r % capacity_) * channels_, channels_ * sizeof(float));
    position = positions_[r % capacity_];
    read_.store(r + 1, std::memory_order_release);
    return true;
}
}
