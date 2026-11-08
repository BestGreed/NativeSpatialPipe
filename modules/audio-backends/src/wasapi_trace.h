// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <windows.h>
#include <atomic>
#include <cstdint>
#include <cwchar>
#include <new>

// Diagnostic builds only. Publish metadata to a bounded ring, with no PCM,
// disk writes, allocations or waiting on the audio threads.
namespace wasapi_trace {
constexpr uint32_t magic = 0x3154504e;
constexpr size_t capacity = 65536;
enum Kind : uint32_t {
    pcm_submit = 1, render_begin, render_end, writer_begin, dsp_end,
    sink_publish, device_request, device_write, ingress_drop, render_tick,
    scheduling,
};
struct Record {
    std::atomic<uint64_t> committed{}, qpc{}, sequence{}, a{}, b{};
    std::atomic<uint32_t> kind{}, tid{}, cpu{};
};
struct Ring {
    uint32_t signature = magic;
    uint32_t length = capacity;
    uint64_t qpc_hz{};
    std::atomic<uint64_t> cursor{};
    Record records[capacity];
};
static_assert(std::atomic<uint64_t>::is_always_lock_free);

struct Trace {
    HANDLE mapping{};
    Ring* ring{};

    void open() {
        wchar_t name[64]{};
        swprintf(name, 64, L"Local\\NSP-WasapiTrace-%lu", GetCurrentProcessId());
        mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(Ring), name);
        if (!mapping || GetLastError() == ERROR_ALREADY_EXISTS) return;
        void* view = MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, sizeof(Ring));
        if (!view) return;
        ring = new (view) Ring{};
        LARGE_INTEGER frequency{};
        QueryPerformanceFrequency(&frequency);
        ring->qpc_hz = frequency.QuadPart;
    }
    ~Trace() {
        if (ring) UnmapViewOfFile(ring);
        if (mapping) CloseHandle(mapping);
    }
    void mark(uint32_t kind, uint64_t sequence, uint64_t a, uint64_t b) const {
        if (!ring) return;
        LARGE_INTEGER now{};
        QueryPerformanceCounter(&now);
        uint64_t ordinal = ring->cursor.fetch_add(1, std::memory_order_relaxed) + 1;
        auto& event = ring->records[(ordinal - 1) % capacity];
        event.committed.store(0, std::memory_order_seq_cst);
        event.qpc.store(now.QuadPart, std::memory_order_relaxed);
        event.kind.store(kind, std::memory_order_relaxed);
        event.tid.store(GetCurrentThreadId(), std::memory_order_relaxed);
        event.cpu.store(GetCurrentProcessorNumber(), std::memory_order_relaxed);
        event.sequence.store(sequence, std::memory_order_relaxed);
        event.a.store(a, std::memory_order_relaxed);
        event.b.store(b, std::memory_order_relaxed);
        event.committed.store(ordinal, std::memory_order_release);
    }
};
}
