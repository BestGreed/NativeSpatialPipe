/* SPDX-License-Identifier: Apache-2.0
 * NativeSpatialPipe AES67 output (branch 6) -- SDP generation.
 */
#define _WIN32_WINNT 0x0A00
#include "aes67_sdp.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    char *out;
    size_t cap;
    size_t used;
    int overflow;
} sdp_writer;

static void w_app(sdp_writer *w, const char *fmt, ...) {
    va_list args;
    int n;
    if (w->overflow) return;
    va_start(args, fmt);
    n = vsnprintf(w->out + w->used, w->cap - w->used, fmt, args);
    va_end(args);
    if (n < 0 || (size_t)n >= w->cap - w->used) {
        w->overflow = 1;
        return;
    }
    w->used += (size_t)n;
}

static int first_octet(const char *ip, unsigned *value) {
    unsigned a = 0, b = 0, c = 0, d = 0;
    char tail = 0;
    if (!ip) return 0;
    if (sscanf(ip, "%u.%u.%u.%u%c", &a, &b, &c, &d, &tail) != 4) return 0;
    if (a > 255 || b > 255 || c > 255 || d > 255) return 0;
    *value = a;
    return 1;
}

size_t nsp_aes67_build_sdp(const nsp_aes67_sdp_params *p, char *out, size_t out_cap) {
    sdp_writer w;
    unsigned octet = 0;
    int multicast = 0;
    const char *dest;
    const char *origin;
    const char *name;
    const char *gmid;
    const char *mac;
    uint16_t port;
    uint16_t pt;
    uint16_t channels;
    uint16_t frames;
    uint16_t labels;
    uint32_t ssrc;

    if (!p || !out || out_cap == 0 || p->struct_size != sizeof(nsp_aes67_sdp_params)) return 0;
    if (p->channels < 1 || p->channels > 16) return 0;
    if (p->frames_per_packet != 48 && p->frames_per_packet != 96 &&
        p->frames_per_packet != 192 && p->frames_per_packet != 480) return 0;
    w.out = out;
    w.cap = out_cap;
    w.used = 0;
    w.overflow = 0;
    out[0] = '\0';

    dest = p->dest_ip ? p->dest_ip : "127.0.0.1";
    origin = (p->origin_addr && p->origin_addr[0]) ? p->origin_addr : "0.0.0.0";
    name = (p->session_name && p->session_name[0]) ? p->session_name : "NativeSpatialPipe AES67 output";
    gmid = (p->ptp_gmid && p->ptp_gmid[0]) ? p->ptp_gmid : NULL;
    mac = (p->local_mac && p->local_mac[0]) ? p->local_mac : "02-4E-53-50-00-01";
    port = p->port ? p->port : 5004;
    pt = p->payload_type ? p->payload_type : 96;
    channels = p->channels;
    frames = p->frames_per_packet;
    labels = p->channel_labels ? p->channel_label_count : 0;
    ssrc = p->ssrc ? p->ssrc : 0x4E535036u;
    if (first_octet(dest, &octet) && octet >= 224 && octet <= 239) multicast = 1;

    w_app(&w, "v=0\r\n");
    w_app(&w, "o=- %u %u IN IP4 %s\r\n", ssrc, ssrc, origin);
    w_app(&w, "s=%s\r\n", name);
    if (multicast) {
        unsigned ttl = p->ttl ? p->ttl : 32u;
        w_app(&w, "c=IN IP4 %s/%u\r\n", dest, ttl);
    } else {
        w_app(&w, "c=IN IP4 %s\r\n", dest);
    }
    w_app(&w, "t=0 0\r\n");
    w_app(&w, "m=audio %u RTP/AVP %u\r\n", port, pt);
    w_app(&w, "a=rtpmap:%u L24/48000/%u\r\n", pt, channels);
    w_app(&w, "a=ptime:%u\r\n", frames / 48u);
    w_app(&w, "a=sendonly\r\n");
    if (gmid) {
        w_app(&w, "a=ts-refclk:ptp=IEEE1588-2008:%s:%u\r\n", gmid, p->ptp_domain);
    } else {
        w_app(&w, "a=ts-refclk:localmac=%s\r\n", mac);
    }
    w_app(&w, "a=mediaclk:direct=0\r\n");
    if (p->smpte_order && p->smpte_order[0]) {
        /* Informative only: AES67 does not normatively define channel-order. */
        w_app(&w, "a=fmtp:%u channel-order=%s\r\n", pt, p->smpte_order);
    }
    if (p->layout_id && p->layout_id[0]) {
        w_app(&w, "a=comment:layout=%s\r\n", p->layout_id);
    }
    if (labels > 0 && labels <= 16) {
        uint16_t i;
        w_app(&w, "a=comment:channel-labels=");
        for (i = 0; i < labels; ++i) {
            w_app(&w, "%s%s", i ? "," : "", p->channel_labels[i] ? p->channel_labels[i] : "?");
        }
        w_app(&w, "\r\n");
    }
    if (w.overflow) {
        out[0] = '\0';
        return 0;
    }
    return w.used;
}

