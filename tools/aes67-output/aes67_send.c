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

/* --wav test source: a 48 kHz PCM WAV is looped as the packet source. Mono is
 * duplicated to every channel except the layout's LFE; a WAV whose channel
 * count equals the output channel count is passed through unchanged. This
 * keeps the listening test on the real AES67/L24 path while making the content
 * a musical phrase instead of the per-channel ramp. */
static float *g_src = NULL;
static size_t g_src_frames = 0;
static unsigned g_src_channels = 0;
static size_t g_src_pos = 0;

static unsigned rd_u16(const unsigned char *p) { return (unsigned)p[0] | ((unsigned)p[1] << 8); }
static unsigned rd_u32(const unsigned char *p) {
    return (unsigned)p[0] | ((unsigned)p[1] << 8) | ((unsigned)p[2] << 16) | ((unsigned)p[3] << 24);
}

static int load_wav(const char *path, char *err, size_t err_cap) {
    FILE *f;
    unsigned char *buf = NULL;
    long size;
    unsigned channels = 0, rate = 0, bits = 0, fmt = 0, off = 12, bps = 0;
    const unsigned char *data = NULL;
    unsigned data_bytes = 0, i;
    size_t frames;
    f = fopen(path, "rb");
    if (!f) { snprintf(err, err_cap, "cannot open %s", path); return 0; }
    fseek(f, 0, SEEK_END);
    size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size < 44 || size > (long)(256 * 1024 * 1024)) { snprintf(err, err_cap, "bad WAV size"); fclose(f); return 0; }
    buf = (unsigned char *)malloc((size_t)size);
    if (!buf || fread(buf, 1, (size_t)size, f) != (size_t)size) { snprintf(err, err_cap, "cannot read WAV"); free(buf); fclose(f); return 0; }
    fclose(f);
    if (memcmp(buf, "RIFF", 4) || memcmp(buf + 8, "WAVE", 4)) { snprintf(err, err_cap, "not a RIFF/WAVE file"); free(buf); return 0; }
    while (off + 8 <= (unsigned)size) {
        unsigned id = off, len = rd_u32(buf + off + 4);
        off += 8;
        if (len > (unsigned)size - off) break;
        if (!memcmp(buf + id, "fmt ", 4) && len >= 16) {
            fmt = rd_u16(buf + off);
            channels = rd_u16(buf + off + 2);
            rate = rd_u32(buf + off + 4);
            bits = rd_u16(buf + off + 14);
            if (fmt == 0xFFFEu && len >= 26) fmt = rd_u16(buf + off + 24);
        } else if (!memcmp(buf + id, "data", 4)) {
            data = buf + off;
            data_bytes = len;
        }
        off += len + (len & 1u);
    }
    if (!data || !channels || !rate) { snprintf(err, err_cap, "missing fmt/data chunk"); free(buf); return 0; }
    if (rate != 48000u) { snprintf(err, err_cap, "WAV must be 48000 Hz (got %u)", rate); free(buf); return 0; }
    if (channels < 1 || channels > 16) { snprintf(err, err_cap, "WAV channels must be 1..16"); free(buf); return 0; }
    if (fmt == 1 && bits == 16) bps = 2;
    else if (fmt == 1 && bits == 24) bps = 3;
    else if (fmt == 1 && bits == 32) bps = 4;
    else if (fmt == 3 && bits == 32) bps = 4;
    else { snprintf(err, err_cap, "unsupported WAV format (fmt=%u bits=%u)", fmt, bits); free(buf); return 0; }
    frames = data_bytes / ((size_t)channels * bps);
    if (frames < 48) { snprintf(err, err_cap, "WAV too short"); free(buf); return 0; }
    g_src = (float *)malloc(frames * channels * sizeof(float));
    if (!g_src) { snprintf(err, err_cap, "out of memory for WAV"); free(buf); return 0; }
    for (i = 0; i < (unsigned)(frames * channels); ++i) {
        const unsigned char *s = data + (size_t)i * bps;
        float v;
        if (fmt == 3) { float t; memcpy(&t, s, 4); v = t; }
        else if (bits == 16) v = (float)(short)((unsigned)s[0] | ((unsigned)s[1] << 8)) / 32768.0f;
        else if (bits == 24) { int t = (int)((unsigned)s[0] | ((unsigned)s[1] << 8) | ((unsigned)s[2] << 16)); if (t & 0x800000) t -= 1 << 24; v = (float)t / 8388608.0f; }
        else { int t = (int)rd_u32(s); v = (float)t / 2147483648.0f; }
        g_src[i] = v;
    }
    g_src_frames = frames;
    g_src_channels = channels;
    g_src_pos = 0;
    free(buf);
    return 1;
}

static void render_block(float *dst, unsigned frames, unsigned channels, int lfe, double *phase,
                         double tone_hz, int uniform, double level) {
    unsigned f, ch;
    if (!g_src) {
        double inc = 2.0 * 3.14159265358979323846 * tone_hz / 48000.0;
        for (f = 0; f < frames; ++f) {
            double s = sin(*phase);
            for (ch = 0; ch < channels; ++ch) {
                double amp = uniform ? 1.0 : (double)(ch + 1) / (double)channels;
                dst[(size_t)f * channels + ch] = (float)(level * amp * s);
            }
            *phase += inc;
            if (*phase >= 2.0 * 3.14159265358979323846) *phase -= 2.0 * 3.14159265358979323846;
        }
        return;
    }
    for (f = 0; f < frames; ++f) {
        const float *src = &g_src[g_src_pos * g_src_channels];
        for (ch = 0; ch < channels; ++ch) {
            float v = 0.0f;
            if ((int)ch != lfe) {
                if (g_src_channels == 1) v = src[0];
                else if (g_src_channels == channels) v = src[ch];
                else v = src[ch < g_src_channels ? ch : 0];
            }
            dst[(size_t)f * channels + ch] = v;
        }
        if (++g_src_pos >= g_src_frames) g_src_pos = 0;
    }
}

static volatile LONG g_ctrl_stop = 0;

static BOOL WINAPI ctrl_handler(DWORD type) {
    (void)type;
    InterlockedExchange(&g_ctrl_stop, 1);
    return TRUE;
}

static void usage(void) {
    printf(
        "usage: aes67-send --dest <ip:port> [options]\n"
        "  --dest <ip:port>       destination (unicast 127.0.0.1:5004 or multicast 239.1.1.1:5004)\n"
        "  --channels <n>         channel count 1..16 (default: layout channels, else 12)\n"
        "  --layout <id>          layout id from contracts/output-layouts.json (default 7.1.4)\n"
        "  --packet-ms <1|2|4|10> packet time in ms (default 1 => 48 frames)\n"
        "  --frames <48|96|192|480>  packet frames (overrides --packet-ms)\n"
        "  --seconds <s>          duration in seconds (default 2)\n"
        "  --wav <path>           48 kHz PCM WAV looped as the source (mono is copied to every\n"
        "                         channel except LFE; overrides --tone; default --seconds = WAV length)\n"
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
    char wav_path[512];
    char gmid[64];
    unsigned ptp_domain;
    const char *layout_id;
    int seconds_given;
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
    int lfe_index = -1;
    unsigned ch;
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
        } else if (strcmp(arg, "--seconds") == 0 && next) { o.seconds = atof(next); o.seconds_given = 1; a++;
        } else if (strcmp(arg, "--wav") == 0 && next) { snprintf(o.wav_path, sizeof(o.wav_path), "%s", next); a++;
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
    if (o.wav_path[0]) {
        if (!load_wav(o.wav_path, err, sizeof(err))) {
            fprintf(stderr, "error: %s\n", err);
            return 2;
        }
        if (g_src_channels != 1 && g_src_channels != o.channels) {
            fprintf(stderr, "error: --wav has %u channels; need mono or exactly --channels %u\n",
                    g_src_channels, o.channels);
            return 2;
        }
        if (!o.seconds_given) o.seconds = (double)g_src_frames / 48000.0;
        for (ch = 0; ch < layout->channels; ++ch)
            if (strcmp(layout->labels[ch], "LFE") == 0) { lfe_index = (int)ch; break; }
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

    if (g_src && !o.quiet)
        printf("wav: frames=%llu channels=%u lfe=%d first=%.5f,%.5f,%.5f,%.5f\n",
               (unsigned long long)g_src_frames, g_src_channels, lfe_index,
               g_src[0], g_src[1], g_src[2], g_src[3]);
    packets = (uint32_t)(o.seconds * 48000.0 / (double)o.frames + 0.5);
    if (packets == 0) packets = 1;

    /* Prebuffer, then start the sender. */
    prebuffer = packets < SEND_PREBUFFER_PACKETS ? packets : SEND_PREBUFFER_PACKETS;
    for (i = 0; i < prebuffer; ++i) {
        render_block(block, o.frames, o.channels, lfe_index, &phase, o.tone, o.uniform, o.level);
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
                render_block(block, o.frames, o.channels, lfe_index, &phase, o.tone, o.uniform, o.level);
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
        printf("aes67-send: dest=%s layout=%s channels=%u pt=%u ssrc=0x%08X packet=%uframes/%.0fms source=%s%s\n",
               dest_label, layout->id, o.channels, o.pt, o.ssrc, o.frames,
               (double)o.frames / 48.0,
               o.wav_path[0] ? o.wav_path : "sine",
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
    free(g_src);
    g_src = NULL;
    return 0;
}
