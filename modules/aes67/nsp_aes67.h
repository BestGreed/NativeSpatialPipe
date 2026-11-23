#ifndef NSP_AES67_H
#define NSP_AES67_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct NspAes67 NspAes67;
typedef void (*NspAes67Release)(void *token);

enum {
    NSP_AES67_OK = 0, NSP_AES67_INVALID = -22, NSP_AES67_STATE = -2,
    NSP_AES67_IO = -3, NSP_AES67_TIMESTAMP_UNAVAILABLE = -4,
    NSP_AES67_LOCK_TIMEOUT = -5, NSP_AES67_JOIN_TIMEOUT = -6,
    NSP_AES67_QUEUE_FULL = -7, NSP_AES67_WORKER_FAILURE = -8,
    NSP_AES67_BUFFER_TOO_SMALL = -9
};
enum { NSP_AES67_AUTO = 0, NSP_AES67_SOFTWARE = 1, NSP_AES67_HARDWARE = 2 };
enum {
    NSP_AES67_STARTED = 1, NSP_AES67_SENDER_EXITED = 2,
    NSP_AES67_STOP_COMPLETE = 3, NSP_AES67_PTP_LOST = 4
};
typedef struct NspAes67Flow {
    uint8_t dest_ip[4], src_interface_ip[4];
    uint16_t port;
    uint8_t ttl, channels;
    uint32_t ssrc, channel_offset;
    uint8_t payload_type, source_filter, reserved[2];
} NspAes67Flow;
typedef struct NspAes67Config {
    uint32_t struct_size, channels, sample_rate, buffer_ms, prebuffer_ms;
    uint32_t timestamping, lock_timeout_ms, loss_timeout_ms, ptp_domain;
    uint8_t ptp_interface_ip[4], ptp_multicast_ip[4], ptp_master_ip[4], ptp_gmid[8];
    uint16_t ptp_event_port, ptp_general_port, ptp_local_event_port, ptp_local_general_port;
    uint8_t sap, default_ttl;
    uint16_t default_port;
    uint8_t default_dest_ip[4], default_src_interface_ip[4];
    uint32_t ssrc_base, flow_count;
    const NspAes67Flow *flows;
    const char *const *channel_labels;
    const char *session_name;
    const uint32_t *channel_map;
    uint32_t input_channels;
    uint8_t default_payload_type, default_source_filter, reserved[2];
} NspAes67Config;
typedef struct NspAes67Event { uint32_t kind; int32_t code, detail; } NspAes67Event;
typedef struct NspAes67Counters {
    uint64_t packets, silent, dropped, late, errors, bytes;
    uint32_t locked, timestamping;
    double offset_ns, rate_ppm, fit_rms_ns;
} NspAes67Counters;

int nsp_aes67_create(NspAes67 **out);
int nsp_aes67_configure(NspAes67 *session, const NspAes67Config *config);
int nsp_aes67_start(NspAes67 *session);
int nsp_aes67_stop(NspAes67 *session, uint32_t join_timeout_ms);
int nsp_aes67_poll_lifecycle_event(NspAes67 *session, NspAes67Event *out);
int nsp_aes67_counters(NspAes67 *session, NspAes67Counters *out);
int nsp_aes67_sdp(NspAes67 *session, uint32_t flow_index, char *out, size_t capacity);
int nsp_aes67_close(NspAes67 *session, uint32_t join_timeout_ms);
int nsp_aes67_submit_leased(void *context, uint64_t first_frame, const float *pcm,
    uint32_t frames, uint32_t channels, uint32_t tail, void *token, NspAes67Release release_fn);

#ifdef __cplusplus
}
#endif
#endif
