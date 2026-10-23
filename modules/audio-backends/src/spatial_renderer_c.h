// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdint.h>
#if defined(NSP_SPATIAL_BUILD)
#define NSP_SPATIAL_API __declspec(dllexport)
#else
#define NSP_SPATIAL_API __declspec(dllimport)
#endif
#ifdef __cplusplus
extern "C" {
#endif
// LIRA: Lazy Immersive Rendering Architecture.
// ABI 1. Serialized owner calls. All buffers borrowed only until return.
// 48k float32, 512-frame inherent delay/tail. Legacy create uses 7.1.4;
// create_layout selects the shared NSP output preset and its channel order.
// Input: objects*32 property bytes, objects*frames planar float samples.
// Output storage: frames*12 floats. Flush storage: capacity*12 floats.
// No retained input pointers or checkpoints. Windows x64, little endian.
// No capture, session, clock matching, calibration or device responsibilities.
NSP_SPATIAL_API uint32_t nsp_spatial_abi(void);
NSP_SPATIAL_API void* nsp_spatial_create(void);
// Additive output presets: 512,514,712,714,716,914,916. Legacy create is 714.
NSP_SPATIAL_API void* nsp_spatial_create_layout(uint32_t layout);
NSP_SPATIAL_API int nsp_spatial_process(void*, uint64_t sequence, uint32_t objects,
    uint32_t frames, const void* properties, const void* planar, float* output);
// Capacity in frames (>=512). Returns 512 once, 0 before any input, -1 on error.
NSP_SPATIAL_API int nsp_spatial_flush(void*, float* output, uint32_t capacity);
NSP_SPATIAL_API void nsp_spatial_destroy(void*);
// Thread-local UTF-8 diagnostic, valid until the next module call on this thread.
NSP_SPATIAL_API const char* nsp_spatial_error(void);
#ifdef __cplusplus
}
#endif
