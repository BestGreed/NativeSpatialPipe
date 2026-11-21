/* SPDX-License-Identifier: Apache-2.0
 * NativeSpatialPipe AES67 output (branch 6) -- CLI test sender.
 *
 * Generates a per-channel sine tone (channel c amplitude scales with c so the
 * receiver can verify L24 interleaving order), submits it through the AES67
 * sender C ABI, optionally writes an SDP file, and prints statistics.
 */
#define _WIN32_WINNT 0x0A00
#include "aes67_tx.h"
#include "aes67_sdp.h"
#include "aes67_layouts.h"

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002UL
#endif

#define SEND_PREBUFFER_PACKETS 12u /* 12 ms of headroom so catch-up bursts never starve at start */

static volatile LONG g_ctrl_stop = 0;

static BOOL WINAPI ctrl_handler(DWORD type) {
    (void)type;
    InterlockedExchange(&g_ctrl_stop, 1);
    return TRUE;
}

static void usage(void) {
    printf(
        "usage: aes67-send --dest <ip:port> [options]\n"
        "  --dest <ip:port>       destination (unicast 127.0.0.1:5004 or multicast 239.69.1.1:5004)\n"
        "  --channels <n>         channel count 1..16 (default: layout channels, else 12)\n"
        "  --layout <id>          layout id from contracts/output-layouts.json (default 7.1.4)\n"
        "  --packet-ms <1|2|4|10> packet time in ms (default 1 => 48 frames)\n"
        "  --frames <48|96|192|480>  packet frames (overrides --packet-ms)\n"
        "  --seconds <s>          duration in seconds (default 2)\n"
        "  --tone <hz>            sine frequency (default 1000)\n"
        "  --level <0..1>         peak level of the loudest channel (default 0.5)\n"
        "  --uniform              same amplitude on all channels (default: ramp by channel)\n"
        "  --ssrc <hex|dec>       RTP SSRC (default 0x4E535036)\n"
        "  --pt <n>               payload type (default 96)\n"
        "  --ttl <n>              multicast TTL (default 32)\n"
        "  --src-ip <ip>          multicast interface source IP\n"
        "  --sdp <path>           write SDP file\n"
        "  --gmid <XX-XX-..>      PTP grandmaster id for SDP ts-refclk\n"
        "  --ptp-domain <n>       PTP domain for SDP (default 0)\n"
        "  --stats-json <path>    write machine-readable statistics\n"
        "  --quiet                suppress the human summary\n"
        "Exit code: 0 ok, 2 argument error, 3 runtime error.\n");
}

typedef struct {
    char dest_ip[64];
    unsigned port;
    unsigned channels;
    unsigned frames;
    double seconds;
    double tone;
    double level;
    int uniform;
    unsigned ssrc;
    unsigned pt;
    unsigned ttl;
    char src_ip[64];
    char sdp_path[512];
    char stats_json[512];
    char gmid[64];
    unsigned ptp_domain;
    const char *layout_id;
    int quiet;
} options;

static int parse_dest(const char *value, char *ip, size_t ip_cap, unsigned *port) {
    const char *colon = strrchr(value, ':');
    size_t len;
    if (!colon) return 0;
    len = (size_t)(colon - value);
    if (len == 0 || len >= ip_cap) return 0;
    memcpy(ip, value, len);
    ip[len] = '\0';
    *port = (unsigned)atoi(colon + 1);
    return *port > 0 && *port < 65536u;
}

static unsigned parse_uint(const char *value, unsigned fallback) {
    char *end = NULL;
    unsigned long v;
    if (!value || !*value) return fallback;
    v = strtoul(value, &end, 0);
    if (end == value) return fallback;
    return (unsigned)v;
}

static const nsp_aes67_layout *find_layout(const char *id) {
    uint16_t i;
    if (!id) return NULL;
    for (i = 0; i < nsp_aes67_layout_count; ++i) {
        if (strcmp(nsp_aes67_layouts[i].id, id) == 0) return &nsp_aes67_layouts[i];
    }
    return NULL;
}

static void render_tone(float *dst, unsigned frames, unsigned channels, double *phase,
                        double tone_hz, int uniform, double level) {
    double inc = 2.0 * 3.14159265358979323846 * tone_hz / 48000.0;
    unsigned f, ch;
    for (f = 0; f < frames; ++f) {
        double s = sin(*phase);
        for (ch = 0; ch < channels; ++ch) {
            double amp = uniform ? 1.0 : (double)(ch + 1) / (double)channels;
            dst[(size_t)f * channels + ch] = (float)(level * amp * s);
        }
        *phase += inc;
        if (*phase >= 2.0 * 3.14159265358979323846) *phase -= 2.0 * 3.14159265358979323846;
    }
}



static int write_text(const char *path, const char *text) {
    FILE *f = fopen(path, "wb");
    size_t len = strlen(text);
    if (!f) return 0;
    if (fwrite(text, 1, len, f) != len) { fclose(f); return 0; }
    fclose(f);
    return 1;
}

int main(int argc, char **argv) {
    options o;
    const nsp_aes67_layout *layout = NULL;
    nsp_aes67_config cfg;
    nsp_aes67_tx *tx = NULL;
    nsp_aes67_stats stats;
    char err[256];
    HANDLE timer = NULL;
    LARGE_INTEGER due, qpc_freq, next_tick, now_qpc;
    LONGLONG period_ticks;
    float *block = NULL;
    double phase = 0.0;
    uint32_t packets, i;
    unsigned prebuffer;
    unsigned long long elapsed_ms, payload_bytes;
    int sdp_len = 0;
    char sdp[8192];
    char dest_label[96];
    size_t label_len;

    memset(&o, 0, sizeof(o));
    o.channels = 0;
    o.frames = 48;
    o.seconds = 2.0;
    o.tone = 1000.0;
    o.level = 0.5;
    o.ssrc = 0x4E535036u;
    o.pt = 96;
    o.ttl = 32;
    o.ptp_domain = 0;
    o.layout_id = nsp_aes67_default_layout;

    for (int a = 1; a < argc; ++a) {
        const char *arg = argv[a];
        const char *next = (a + 1 < argc) ? argv[a + 1] : NULL;
        if (strcmp(arg, "--dest") == 0 && next) {
            if (!parse_dest(next, o.dest_ip, sizeof(o.dest_ip), &o.port)) {
                fprintf(stderr, "error: bad --dest (want ip:port)\n");
                return 2;
            }
            a++;
        } else if (strcmp(arg, "--channels") == 0 && next) { o.channels = parse_uint(next, 12); a++;
        } else if (strcmp(arg, "--layout") == 0 && next) { o.layout_id = next; a++;
        } else if (strcmp(arg, "--packet-ms") == 0 && next) {
            unsigned ms = parse_uint(next, 1);
            if (ms != 1 && ms != 2 && ms != 4 && ms != 10) { fprintf(stderr, "error: --packet-ms must be 1/2/4/10\n"); return 2; }
            o.frames = ms * 48u;
            a++;
        } else if (strcmp(arg, "--frames") == 0 && next) { o.frames = parse_uint(next, 48); a++;
        } else if (strcmp(arg, "--seconds") == 0 && next) { o.seconds = atof(next); a++;
        } else if (strcmp(arg, "--tone") == 0 && next) { o.tone = atof(next); a++;
        } else if (strcmp(arg, "--level") == 0 && next) { o.level = atof(next); a++;
        } else if (strcmp(arg, "--uniform") == 0) { o.uniform = 1;
        } else if (strcmp(arg, "--ssrc") == 0 && next) { o.ssrc = parse_uint(next, 0x4E535036u); a++;
        } else if (strcmp(arg, "--pt") == 0 && next) { o.pt = parse_uint(next, 96); a++;
        } else if (strcmp(arg, "--ttl") == 0 && next) { o.ttl = parse_uint(next, 32); a++;
        } else if (strcmp(arg, "--src-ip") == 0 && next) { snprintf(o.src_ip, sizeof(o.src_ip), "%s", next); a++;
        } else if (strcmp(arg, "--sdp") == 0 && next) { snprintf(o.sdp_path, sizeof(o.sdp_path), "%s", next); a++;
        } else if (strcmp(arg, "--stats-json") == 0 && next) { snprintf(o.stats_json, sizeof(o.stats_json), "%s", next); a++;
        } else if (strcmp(arg, "--gmid") == 0 && next) { snprintf(o.gmid, sizeof(o.gmid), "%s", next); a++;
        } else if (strcmp(arg, "--ptp-domain") == 0 && next) { o.ptp_domain = parse_uint(next, 0); a++;
        } else if (strcmp(arg, "--quiet") == 0) { o.quiet = 1;
        } else if (strcmp(arg, "--help") == 0 || strcmp(arg, "-h") == 0) { usage(); return 0;
        } else {
            fprintf(stderr, "error: unknown argument %s\n", arg);
            usage();
            return 2;
        }
    }
    if (!o.dest_ip[0] || !o.port) {
        fprintf(stderr, "error: --dest is required\n");
        return 2;
    }
    if (o.frames != 48 && o.frames != 96 && o.frames != 192 && o.frames != 480) {
        fprintf(stderr, "error: frames must be 48/96/192/480\n");
        return 2;
    }
    layout = find_layout(o.layout_id);
    if (!layout) {
        fprintf(stderr, "error: unknown --layout %s (see contracts/output-layouts.json)\n", o.layout_id);
        return 2;
    }
    if (o.channels == 0) o.channels = layout->channels;
    if (o.channels < 1 || o.channels > NSP_AES67_MAX_CHANNELS) {
        fprintf(stderr, "error: --channels must be 1..16\n");
        return 2;
    }
    if (o.seconds <= 0.0 || o.seconds > 600.0) {
        fprintf(stderr, "error: --seconds must be in (0, 600]\n");
        return 2;
    }

    memset(&cfg, 0, sizeof(cfg));
    cfg.struct_size = sizeof(cfg);
    snprintf(cfg.dest_ip, sizeof(cfg.dest_ip), "%s", o.dest_ip);
    cfg.port = (uint16_t)o.port;
    cfg.channels = (uint16_t)o.channels;
    cfg.frames_per_packet = (uint16_t)o.frames;
    cfg.ttl = (uint8_t)(o.ttl > 255 ? 255 : o.ttl);
    cfg.payload_type = (uint8_t)(o.pt & 0x7F);
    cfg.ssrc = o.ssrc;
    cfg.ptp_domain = (uint16_t)o.ptp_domain;
    snprintf(cfg.src_interface_ip, sizeof(cfg.src_interface_ip), "%s", o.src_ip);
    cfg.ring_packets = 64; /* 64 ptime periods of headroom absorbs producer jitter/bursts */

    tx = nsp_aes67_create(&cfg, err, sizeof(err));
    if (!tx) {
        fprintf(stderr, "error: nsp_aes67_create failed: %s\n", err);
        return 3;
    }
    block = (float *)calloc((size_t)o.frames * o.channels, sizeof(float));
    if (!block) {
        fprintf(stderr, "error: out of memory\n");
        nsp_aes67_destroy(tx);
        return 3;
    }
    timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!timer) timer = CreateWaitableTimerW(NULL, FALSE, NULL);
    if (!timer) {
        fprintf(stderr, "error: CreateWaitableTimerEx failed\n");
        nsp_aes67_destroy(tx);
        free(block);
        return 3;
    }
    due.QuadPart = -(LONGLONG)(o.frames / 48u) * 10000LL;
    if (!SetWaitableTimer(timer, &due, 0, NULL, NULL, FALSE)) {
        fprintf(stderr, "error: SetWaitableTimer failed\n");
        CloseHandle(timer);
        nsp_aes67_destroy(tx);
        free(block);
        return 3;
    }
    SetConsoleCtrlHandler(ctrl_handler, TRUE);

    packets = (uint32_t)(o.seconds * 48000.0 / (double)o.frames + 0.5);
    if (packets == 0) packets = 1;

    /* Prebuffer, then start the sender. */
    prebuffer = packets < SEND_PREBUFFER_PACKETS ? packets : SEND_PREBUFFER_PACKETS;
    for (i = 0; i < prebuffer; ++i) {
        render_tone(block, o.frames, o.channels, &phase, o.tone, o.uniform, o.level);
        if (nsp_aes67_submit(tx, block, o.frames) != NSP_AES67_OK) {
            fprintf(stderr, "error: nsp_aes67_submit rejected prebuffer\n");
            return 3;
        }
    }
    if (nsp_aes67_start(tx) != NSP_AES67_OK) {
        fprintf(stderr, "error: nsp_aes67_start failed: %s\n", nsp_aes67_last_error(tx));
        return 3;
    }
    /* Produce on an absolute QPC timeline with bounded catch-up bursts, the
     * same model a renderer's 480-frame blocks would use against this sink. */
    QueryPerformanceFrequency(&qpc_freq);
    period_ticks = (LONGLONG)((double)qpc_freq.QuadPart * (double)(o.frames / 48u) / 1000.0);
    QueryPerformanceCounter(&next_tick);
    next_tick.QuadPart += period_ticks;
    while (i < packets && InterlockedCompareExchange(&g_ctrl_stop, 0, 0) == 0) {
        QueryPerformanceCounter(&now_qpc);
        if (next_tick.QuadPart > now_qpc.QuadPart) {
            LONGLONG remain_100ns = ((next_tick.QuadPart - now_qpc.QuadPart) * 10000000LL) / qpc_freq.QuadPart + 1;
            due.QuadPart = -remain_100ns;
            if (!SetWaitableTimer(timer, &due, 0, NULL, NULL, FALSE)) break;
            if (WaitForSingleObject(timer, 250) != WAIT_OBJECT_0) break;
            continue;
        }
        {
            int burst = 0;
            while (next_tick.QuadPart <= now_qpc.QuadPart && i < packets && burst < 32) {
                render_tone(block, o.frames, o.channels, &phase, o.tone, o.uniform, o.level);
                if (nsp_aes67_submit(tx, block, o.frames) != NSP_AES67_OK) break;
                next_tick.QuadPart += period_ticks;
                i++;
                burst++;
            }
            if (next_tick.QuadPart <= now_qpc.QuadPart) {
                next_tick.QuadPart = now_qpc.QuadPart + period_ticks;
            }
        }
    }
    /* Drain: let the sender consume every submitted frame (bounded wait).
     * Polling at 1 ms keeps the silent drain tail to at most a couple of
     * packets; the test treats silence as a failure only mid-stream. */
    for (i = 0; i < 500u; ++i) {
        nsp_aes67_stats probe;
        memset(&probe, 0, sizeof(probe));
        probe.struct_size = sizeof(probe);
        if (nsp_aes67_get_stats(tx, &probe) != NSP_AES67_OK) break;
        if (probe.frames_sent >= probe.frames_submitted) break;
        Sleep(1);
    }
    nsp_aes67_stop(tx);
    SetConsoleCtrlHandler(ctrl_handler, FALSE);
    CloseHandle(timer);

    memset(&stats, 0, sizeof(stats));
    stats.struct_size = sizeof(stats);
    nsp_aes67_get_stats(tx, &stats);
    elapsed_ms = stats.stop_ms > stats.start_ms ? stats.stop_ms - stats.start_ms : 1;
    payload_bytes = stats.packets_sent * ((unsigned long long)o.frames * o.channels * 3ull);
    snprintf(dest_label, sizeof(dest_label), "%s:%u", o.dest_ip, o.port);

    if (o.sdp_path[0]) {
        nsp_aes67_sdp_params sp;
        memset(&sp, 0, sizeof(sp));
        sp.struct_size = sizeof(sp);
        sp.session_name = "NativeSpatialPipe AES67 output";
        sp.dest_ip = o.dest_ip;
        sp.port = (uint16_t)o.port;
        sp.channels = (uint16_t)o.channels;
        sp.frames_per_packet = (uint16_t)o.frames;
        sp.ttl = (uint8_t)(o.ttl > 255 ? 255 : o.ttl);
        sp.payload_type = (uint8_t)(o.pt & 0x7F);
        sp.ssrc = o.ssrc;
        sp.origin_addr = o.src_ip;
        sp.ptp_gmid = o.gmid;
        sp.ptp_domain = (uint16_t)o.ptp_domain;
        sp.layout_id = layout->id;
        sp.smpte_order = layout->smpte_order;
        sp.channel_labels = (const char *const *)layout->labels;
        sp.channel_label_count = layout->channels;
        sdp_len = (int)nsp_aes67_build_sdp(&sp, sdp, sizeof(sdp));
        if (sdp_len <= 0) {
            fprintf(stderr, "error: SDP generation failed\n");
            nsp_aes67_destroy(tx);
            free(block);
            return 3;
        }
        if (!write_text(o.sdp_path, sdp)) {
            fprintf(stderr, "error: cannot write SDP %s\n", o.sdp_path);
            nsp_aes67_destroy(tx);
            free(block);
            return 3;
        }
    }
    if (o.stats_json[0]) {
        char json[1024];
        snprintf(json, sizeof(json),
                 "{\"dest\":\"%s\",\"port\":%u,\"layout\":\"%s\",\"channels\":%u,\"payloadType\":%u,"
                 "\"framesPerPacket\":%u,\"ptimeMs\":%.3f,\"ssrc\":%u,\"ssrcHex\":\"0x%08X\","
                 "\"packets\":%llu,\"framesSent\":%llu,\"framesSubmitted\":%llu,"
                 "\"bytesSent\":%llu,\"payloadBytes\":%llu,\"silentPackets\":%llu,"
                 "\"ringDroppedFrames\":%llu,\"sendErrors\":%llu,\"lateTicks\":%llu,"
                 "\"elapsedMs\":%llu,\"bitrateKbps\":%.1f,\"payloadKbps\":%.1f}",
                 o.dest_ip, o.port, layout->id, o.channels, o.pt, o.frames,
                 (double)o.frames / 48.0, o.ssrc, o.ssrc, stats.packets_sent, stats.frames_sent,
                 stats.frames_submitted, stats.bytes_sent, payload_bytes, stats.silent_packets,
                 stats.ring_dropped_frames, stats.send_errors, stats.late_ticks, elapsed_ms,
                 (double)stats.bytes_sent * 8.0 / (double)elapsed_ms,
                 (double)payload_bytes * 8.0 / (double)elapsed_ms);
        if (!write_text(o.stats_json, json)) {
            fprintf(stderr, "warning: cannot write stats JSON %s\n", o.stats_json);
        }
    }
    if (!o.quiet) {
        label_len = strlen(dest_label);
        printf("aes67-send: dest=%s layout=%s channels=%u pt=%u ssrc=0x%08X packet=%uframes/%.0fms tone=%.0fHz(%s)%s\n",
               dest_label, layout->id, o.channels, o.pt, o.ssrc, o.frames,
               (double)o.frames / 48.0, o.tone, o.uniform ? "uniform" : "channel-ramp",
               o.sdp_path[0] ? " sdp=written" : "");
        printf("packets=%llu frames=%llu bytes=%llu submitted=%llu silent=%llu dropped=%llu errors=%llu late=%llu elapsed_ms=%llu bitrate_kbps=%.1f payload_kbps=%.1f\n",
               stats.packets_sent, stats.frames_sent, stats.bytes_sent, stats.frames_submitted,
               stats.silent_packets, stats.ring_dropped_frames, stats.send_errors,
               stats.late_ticks, elapsed_ms, (double)stats.bytes_sent * 8.0 / (double)elapsed_ms,
               (double)payload_bytes * 8.0 / (double)elapsed_ms);
        (void)label_len;
    }
    nsp_aes67_destroy(tx);
    free(block);
    return 0;
}

