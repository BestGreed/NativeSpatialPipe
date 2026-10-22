// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <windows.h>
#include <array>
#include <atomic>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace spatial {
// SAD1, little endian. Each record can be decoded independently after a crash.
struct DiagnosticHeader {
    std::uint32_t magic{0x31444153}, kind{}, bytes{}, reserved{};
    std::uint64_t qpcNs{}, ordinal{};
};
static_assert(sizeof(DiagnosticHeader)==32);
struct DiagnosticPart { const void* data; std::size_t bytes; };
struct CaptureDiagnostic { std::uint64_t readBeginNs{}, decodeEndNs{}; std::uint32_t queuedPackets{}, reserved{}; };
struct RenderDiagnostic { std::uint64_t firstFrame{}; std::uint32_t channels{}, frames{}; };
struct AsioDiagnostic {
    std::uint64_t beginNs{}, endNs{}, previousBeginNs{};
    double sourceFrame{}, sampleRate{};
    // stateFlags: bit 0 reset pending, bit 1 latency update, bit 8 live starvation accounting.
    std::uint32_t frames{}, channels{}, missing{}, queuedBefore{}, queuedAfter{}, index{}, stateFlags{}, timeFlags{};
    std::uint64_t samplePosition{}, systemTime{};
    std::array<std::int32_t,32> sampleTypes{};
};
static_assert(sizeof(AsioDiagnostic)==216);
// One producer per ring. No locks, allocations, waits or file I/O in TryWrite.
// Drop DIAGNOSTICS on overload, never block or discard audio to save a log.
class DiagnosticRing {
    std::vector<std::uint8_t> data_;
    alignas(64) std::atomic<std::uint64_t> read_{0};
    alignas(64) std::atomic<std::uint64_t> write_{0};
    std::uint64_t ordinal_{};
    void CopyIn(std::uint64_t offset,const void* data,std::size_t bytes) noexcept;
    void CopyOut(std::uint64_t offset,void* data,std::size_t bytes) const noexcept;
public:
    std::atomic<std::uint64_t> dropped{0}, highWater{0};
    explicit DiagnosticRing(std::size_t capacity):data_(capacity){}
    bool TryWrite(std::uint32_t kind,std::uint64_t stamp,std::span<const DiagnosticPart> parts) noexcept;
    bool Pop(std::vector<std::uint8_t>& record);
    std::uint64_t Used() const noexcept { return write_.load()-read_.load(); }
};
class Diagnostics {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    enum Stream { Capture=0, Render=1, Asio=2, Events=3, Ordinary=4 };
    Diagnostics(const std::filesystem::path& logs,const std::filesystem::path& profile,bool enabled=true,bool audio=true);
    ~Diagnostics();
    static std::uint64_t NowNs() noexcept;
    // Presentation timestamps may lead the current callback. Preserve the sign
    // and subtract integers first so long uptime cannot erase short intervals.
    static constexpr double DeltaMilliseconds(std::uint64_t end, std::uint64_t begin) noexcept {
        return end>=begin ? double(end-begin)/1e6 : -double(begin-end)/1e6;
    }
    const std::filesystem::path& Directory() const;
    bool Enabled() const noexcept;
    bool AudioEnabled() const noexcept;
    void Record(Stream stream,std::uint32_t kind,std::uint64_t stamp,std::span<const DiagnosticPart> parts) noexcept;
    void Event(const std::string& json); // engine worker only
    void Mark(bool manual); // worker, first automatic + first manual incident
    std::uint64_t Dropped() const noexcept;
    bool Failed() const noexcept;
    void Finish(bool success);
};
}
