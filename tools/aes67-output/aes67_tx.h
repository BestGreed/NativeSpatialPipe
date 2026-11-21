/* SPDX-License-Identifier: Apache-2.0
 * NativeSpatialPipe AES67 output (branch 6) -- sender C ABI.
 *
 * Wire format: RTP/AVP, payload type 96 (L24), 48000 Hz, L24 big-endian
 * interleaved PCM. One RTP packet carries frames_per_packet frames.
 * The submit entry point accepts interleaved float32 in [-1, 1] and converts
 * to L24 internally; int32 input is intentionally not offered (see README).
 */
#ifndef NSP_AES67_TX_H
#define NSP_AES67_TX_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NSP_AES67_ABI_VERSION 1u
#define NSP_AES67_MAX_CHANNELS 16u
#define NSP_AES67_SAMPLE_RATE 48000u
#define NSP_AES67_MAX_FRAMES_PER_PACKET 480u

/* Return codes. */
enum {
    NSP_AES67_OK = 0,
    NSP_AES67_ERR_ARG = -1,
    NSP_AES67_ERR_STATE = -2,
    NSP_AES67_ERR_NETWORK = -3,
    NSP_AES67_ERR_MEMORY = -4
};

typedef struct nsp_aes67_config {
    uint32_t struct_size;        /* = sizeof(nsp_aes67_config) */
    char dest_ip[64];            /* IPv4 unicast or multicast 239.x */
    uint16_t port;               /* default 5004 */
    uint16_t channels;           /* 1..16, must match layout */
    uint16_t frames_per_packet;  /* 48 / 96 / 192 / 480; default 48 (1 ms) */
    uint8_t ttl;                 /* multicast TTL, 1..255; default 32 */
    uint8_t payload_type;        /* default 96 */
    uint32_t ssrc;               /* RTP SSRC; 0 => derived */
    char src_interface_ip[64];   /* optional IP_MULTICAST_IF; empty = OS default */
    uint16_t ptp_domain;         /* SDP only; not used by the sender */
    uint16_t ring_packets;       /* bounded submit ring; 0 => 8 packets */
} nsp_aes67_config;

typedef struct nsp_aes67_stats {
    uint32_t struct_size;
    uint32_t last_seq;           /* seq of the last transmitted packet */
    uint32_t last_timestamp;     /* RTP timestamp of the last packet */
    uint32_t channels;
    uint32_t frames_per_packet;
    uint64_t packets_sent;       /* all packets including silence */
    uint64_t silent_packets;     /* underrun packets sent as silence */
    uint64_t frames_sent;        /* frames transmitted (incl. silence) */
    uint64_t frames_submitted;   /* frames accepted by submit() */
    uint64_t bytes_sent;         /* UDP payload bytes = 12 + frames*ch*3 */
    uint64_t submit_calls;
    uint64_t ring_dropped_frames;/* oldest buffered data discarded (ring full) */
    uint64_t send_errors;
    uint64_t late_ticks;         /* pacing deadlines missed by > 1.5 periods */
    uint64_t start_ms;           /* GetTickCount64 at start() */
    uint64_t stop_ms;
} nsp_aes67_stats;

typedef struct nsp_aes67_tx nsp_aes67_tx;

/* Diagnostics: writes a human-readable message into err (always NUL terminated). */
const char *nsp_aes67_abi_version(void);

/* Creates the socket and allocates the bounded ring. Returns NULL on failure. */
nsp_aes67_tx *nsp_aes67_create(const nsp_aes67_config *config, char *err, size_t err_cap);

/* Starts the paced sender thread. May be called after submit() to prebuffer. */
int nsp_aes67_start(nsp_aes67_tx *tx);

/* Non-blocking enqueue of interleaved float32 frames (any frame count <= ring).
 * Returns NSP_AES67_OK or a negative code. Never blocks on the network. */
int nsp_aes67_submit(nsp_aes67_tx *tx, const float *interleaved_f32, uint32_t frames);

int nsp_aes67_get_stats(nsp_aes67_tx *tx, nsp_aes67_stats *out);

/* Stops the thread and closes the socket. Idempotent. */
int nsp_aes67_stop(nsp_aes67_tx *tx);

void nsp_aes67_destroy(nsp_aes67_tx *tx);

/* Last error message for this handle ("" when none). */
const char *nsp_aes67_last_error(nsp_aes67_tx *tx);

#ifdef __cplusplus
}
#endif
#endif /* NSP_AES67_TX_H */

