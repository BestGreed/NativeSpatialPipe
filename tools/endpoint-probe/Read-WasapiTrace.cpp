// SPDX-License-Identifier: Apache-2.0
// Snapshot the diagnostic WASAPI mapping without touching the playback queues.
#include "../../modules/audio-backends/src/wasapi_trace.h"
#include <cstdio>
#include <cstdlib>

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "Usage: Read-WasapiTrace <scheduler-pid> > trace.csv\n");
        return 1;
    }
    char* end = nullptr;
    const auto pid = std::strtoul(argv[1], &end, 10);
    if (!pid || *end) return 1;
    wchar_t name[64]{};
    swprintf(name, 64, L"Local\\NSP-WasapiTrace-%lu", pid);
    HANDLE mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, name);
    if (!mapping) {
        std::fprintf(stderr, "OpenFileMappingW: %lu\n", GetLastError());
        return 2;
    }
    const auto* ring = static_cast<const wasapi_trace::Ring*>(
        MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, sizeof(wasapi_trace::Ring)));
    if (!ring || ring->signature != wasapi_trace::magic || ring->length != wasapi_trace::capacity || !ring->qpc_hz) {
        if (ring) UnmapViewOfFile(ring);
        CloseHandle(mapping);
        return 3;
    }
    const auto last = ring->cursor.load(std::memory_order_acquire);
    const auto start = last > ring->length ? last - ring->length + 1 : 1;
    std::puts("ordinal,qpc,qpc_hz,event,tid,cpu,sequence,a,b");
    for (auto ordinal = start; ordinal <= last; ++ordinal) {
        const auto& record = ring->records[(ordinal - 1) % ring->length];
        if (record.committed.load(std::memory_order_acquire) != ordinal) continue;
        const auto qpc = record.qpc.load(std::memory_order_relaxed);
        const auto kind = record.kind.load(std::memory_order_relaxed);
        const auto tid = record.tid.load(std::memory_order_relaxed);
        const auto cpu = record.cpu.load(std::memory_order_relaxed);
        const auto sequence = record.sequence.load(std::memory_order_relaxed);
        const auto a = record.a.load(std::memory_order_relaxed);
        const auto b = record.b.load(std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (record.committed.load(std::memory_order_relaxed) != ordinal) continue;
        std::printf("%llu,%llu,%llu,%u,%u,%u,%llu,%llu,%llu\n",
            ordinal, qpc, ring->qpc_hz, kind, tid, cpu, sequence, a, b);
    }
    UnmapViewOfFile(ring);
    CloseHandle(mapping);
}
