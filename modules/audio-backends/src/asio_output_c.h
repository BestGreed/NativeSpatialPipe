// SPDX-License-Identifier: GPL-3.0-only
#ifndef NSP_ASIO_OUTPUT_C_H
#define NSP_ASIO_OUTPUT_C_H
#include <stdint.h>

#if defined(NSP_ASIO_STATIC)
#define NSP_ASIO_API
#elif defined(NSP_ASIO_BUILD)
#define NSP_ASIO_API __declspec(dllexport)
#else
#define NSP_ASIO_API __declspec(dllimport)
#endif
#ifdef __cplusplus
extern "C" {
#endif

#define NSP_ASIO_ABI_VERSION 1u
#define NSP_ASIO_CLOCK_SYNC 1u
enum {
    NSP_ASIO_OK=0, NSP_ASIO_INVALID=-22, NSP_ASIO_STATE=-16,
    NSP_ASIO_DEVICE_ERROR=-5, NSP_ASIO_NO_MEMORY=-12
};
typedef struct nsp_asio_context* nsp_asio_handle;
typedef struct nsp_asio_config {
    uint32_t struct_size, abi_version;
    const char* driver_id_utf8;
    uint32_t source_channels, output_channels;
    const uint32_t* source_channel_indices;
    const int32_t* physical_channels; /* NULL selects 0..output_channels-1. */
    int32_t device_buffer_frames; /* 0 follows the driver's preference. */
    double device_sample_rate; /* 0 follows the existing device rate policy. */
    uint32_t flags; /* NSP_ASIO_CLOCK_SYNC, or 0 for fixed-rate/offline use. */
} nsp_asio_config;
typedef struct nsp_asio_stats {
    uint32_t struct_size, abi_version;
    uint32_t started, primed, input_complete, faulted, stopped, drained;
    uint64_t submitted_frames, buffered_source_frames, required_write_frame;
    uint64_t callbacks, starvation_frames;
    uint32_t device_buffer_frames, minimum_prime_frames;
    double source_frame, device_sample_rate;
} nsp_asio_stats;

/* All handle operations are serialized on the thread that opened the handle.
 * They may allocate/block and MUST NOT run in an audio callback. Open owns a
 * matching STA COM initialization (an existing MTA thread is rejected).
 * The owner services driver messages. No C++ exception crosses this boundary.
 * Pointers must be valid for the stated lengths; stale handles are invalid.
 */
NSP_ASIO_API uint32_t nsp_asio_abi_version(void);
/* *out must be NULL; configuration arrays/strings are copied before return. */
NSP_ASIO_API int32_t nsp_asio_open(const nsp_asio_config* config, nsp_asio_handle* out);
NSP_ASIO_API int32_t nsp_asio_start(nsp_asio_handle handle);
/* 48 kHz interleaved f32, copied before return. first_frame starts at 0 and
 * must equal the preceding accepted end frame. Rejected input is not consumed.
 */
NSP_ASIO_API int32_t nsp_asio_submit(nsp_asio_handle handle, uint64_t first_frame,
                                  const float* samples, uint32_t frames);
/* The caller selects a bounded prebuffer using minimum_prime_frames. */
NSP_ASIO_API int32_t nsp_asio_prime(nsp_asio_handle handle);
/* Worker call with zero event-wait timeout; pumps existing driver messages,
 * fills and discards consumed queue storage. Driver calls/message dispatch may
 * still block: this is NOT a realtime-safe or bounded-duration function.
 * It does not implement a scheduling thread.
 */
NSP_ASIO_API int32_t nsp_asio_service(nsp_asio_handle handle);
NSP_ASIO_API int32_t nsp_asio_finish_input(nsp_asio_handle handle);
/* Additive ABI v1 entry: silent callbacks retain source/ring during bounded
 * host rebuffering. No driver reopen or second Prime. Resume before EOF drain.
 */
NSP_ASIO_API int32_t nsp_asio_hold(nsp_asio_handle handle, uint32_t enabled);
NSP_ASIO_API int32_t nsp_asio_get_stats(nsp_asio_handle handle, nsp_asio_stats* stats);
NSP_ASIO_API int32_t nsp_asio_stop(nsp_asio_handle handle);
/* Stop first if started. Failure retains the handle; success clears *handle. */
NSP_ASIO_API int32_t nsp_asio_close(nsp_asio_handle* handle);
/* Calling-thread UTF-8 error; returns required bytes including NUL. Copy-out
 * is truncated/NUL-terminated when capacity>0. No module allocation is exposed.
 */
NSP_ASIO_API uint32_t nsp_asio_last_error(char* destination, uint32_t capacity);
#ifdef __cplusplus
}
#endif
#endif
