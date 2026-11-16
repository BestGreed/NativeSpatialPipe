// Versioned, bounded native PCM transport. One producer, one consumer.
#pragma once
#include <windows.h>
#include <stdint.h>
#include "../browser-identity/registry.h"
constexpr uint32_t PCM_MAGIC=0x314d4350,PCM_SLOTS=16,PCM_MAX_CHANNELS=8,PCM_MAX_FRAMES=4096;
struct PcmDescriptor {
 uint32_t size,sourcePid,hostPid,generation,sampleRate,channels,channelMask,frames;
 uint64_t streamId,firstSourceFrame,observedQpcNs,copyDoneQpcNs,windowsReadFrame;
 uint32_t flags,reserved; // bit0: copied while still unconsumed; no presentation timestamp claimed.
 BrowserIdentityTag browser; // flags bit1: fresh, structurally verified browser identity.
};
struct PcmSlot {volatile LONG ready;PcmDescriptor descriptor;float pcm[PCM_MAX_FRAMES*PCM_MAX_CHANNELS];};
struct PcmRing {uint32_t magic,version,slots,slotBytes;PcmSlot blocks[PCM_SLOTS];};
static inline uint64_t pcm_qpc_ns(){LARGE_INTEGER q,h;QueryPerformanceCounter(&q);QueryPerformanceFrequency(&h);return q.QuadPart/h.QuadPart*1000000000ULL+q.QuadPart%h.QuadPart*1000000000ULL/h.QuadPart;}
