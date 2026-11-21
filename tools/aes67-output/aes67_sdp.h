/* SPDX-License-Identifier: Apache-2.0
 * NativeSpatialPipe AES67 output (branch 6) -- SDP generation.
 */
#ifndef NSP_AES67_SDP_H
#define NSP_AES67_SDP_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct nsp_aes67_sdp_params {
    uint32_t struct_size;         /* = sizeof(nsp_aes67_sdp_params) */
    const char *session_name;     /* s= ; NULL => default */
    const char *dest_ip;          /* c= address; multicast keeps /ttl */
    uint16_t port;                /* m=audio port; 0 => 5004 */
    uint16_t channels;            /* 1..16 */
    uint16_t frames_per_packet;   /* 48 / 96 / 192 / 480 */
    uint8_t ttl;                  /* multicast TTL for c= line */
    uint8_t payload_type;         /* default 96 */
    uint32_t ssrc;                /* o= session id and RTP SSRC hint */
    const char *origin_addr;      /* o=/c= origin; NULL => 0.0.0.0 */
    const char *ptp_gmid;         /* e.g. 00-11-22-FF-FE-33-44-55; NULL/empty => localmac */
    const char *local_mac;        /* localmac fallback; NULL => 02-4E-53-50-00-01 */
    uint16_t ptp_domain;          /* default 0 */
    const char *layout_id;        /* e.g. "7.1.4"; optional */
    const char *smpte_order;      /* informative SMPTE2110 channel-order token; optional */
    const char *const *channel_labels; /* optional label array */
    uint16_t channel_label_count;
} nsp_aes67_sdp_params;

/* Builds the SDP text into out. Returns the number of bytes written (without
 * the terminating NUL), or 0 when the buffer is too small / params invalid.
 * The caller owns the buffer; 8 KiB is plenty for 16 channels. */
size_t nsp_aes67_build_sdp(const nsp_aes67_sdp_params *params, char *out, size_t out_cap);

#ifdef __cplusplus
}
#endif
#endif /* NSP_AES67_SDP_H */

