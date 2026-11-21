/* SPDX-License-Identifier: Apache-2.0
 * NativeSpatialPipe AES67 output (branch 6) -- RTP/L24 sender.
 */
#define _WIN32_WINNT 0x0A00
#include "aes67_tx.h"

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002UL
#endif

#define AES67_RTP_HEADER 12u
#define AES67_DEFAULT_PORT 5004u

struct nsp_aes67_tx {
    nsp_aes67_config cfg;
    uint32_t channels;
    uint32_t frames;
    uint32_t period_ms;
    uint32_t capacity_frames;
    SOCKET sock;
    HANDLE thread;
    HANDLE timer;
    volatile LONG stop_flag;
    volatile LONG thread_exit;
    CRITICAL_SECTION lock;
    float *ring;
    uint32_t head;
    uint32_t count;
    float *pop;
    uint8_t *packet;
    uint16_t seq;
    uint32_t timestamp;
    nsp_aes67_stats stats;
    char error[256];
    int started;
    int ws_started;
    struct sockaddr_in dest;
};

static void set_error(nsp_aes67_tx *tx, const char *msg) {
    size_t i;
    if (!tx) return;
    for (i = 0; i + 1 < sizeof(tx->error) && msg[i]; ++i) tx->error[i] = msg[i];
    tx->error[i] = '\0';
}

static int is_multicast_ipv4(uint32_t be_addr) {
    return (ntohl(be_addr) & 0xF0000000UL) == 0xE0000000UL;
}

const char *nsp_aes67_abi_version(void) { return "nsp_aes67/1"; }

const char *nsp_aes67_last_error(nsp_aes67_tx *tx) { return tx ? tx->error : ""; }

/* ---- RTP / L24 encoding ------------------------------------------------- */

static void put_be16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)(v & 0xFF);
}

static void put_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)((v >> 16) & 0xFF);
    p[2] = (uint8_t)((v >> 8) & 0xFF);
    p[3] = (uint8_t)(v & 0xFF);
}

static void put_l24(uint8_t *p, float v) {
    int32_t s;
    if (!(v == v)) v = 0.0f; /* NaN */
    if (v > 1.0f) v = 1.0f;
    if (v < -1.0f) v = -1.0f;
    s = (int32_t)(v >= 0.0f ? v * 8388607.0f + 0.5f : v * 8388607.0f - 0.5f);
    if (s > 8388607) s = 8388607;
    if (s < -8388608) s = -8388608;
    p[0] = (uint8_t)((uint32_t)s >> 16);
    p[1] = (uint8_t)(((uint32_t)s >> 8) & 0xFF);
    p[2] = (uint8_t)((uint32_t)s & 0xFF);
}

/* Pop exactly tx->frames frames from the ring into tx->pop. Caller holds lock.
 * Returns 1 when a full packet was available, 0 otherwise. */
static int pop_packet_locked(nsp_aes67_tx *tx) {
    if (tx->count < tx->frames) return 0;
    {
        uint32_t ch = tx->channels;
        uint32_t first = tx->frames;
        uint32_t left = tx->frames;
        uint32_t offset = 0;
        while (left > 0) {
            uint32_t run = (tx->capacity_frames - tx->head) < left ? (tx->capacity_frames - tx->head) : left;
            if (run > first) run = first;
            memcpy(tx->pop + (size_t)offset * ch, tx->ring + (size_t)tx->head * ch,
                   (size_t)run * ch * sizeof(float));
            tx->head = (tx->head + run) % tx->capacity_frames;
            tx->count -= run;
            offset += run;
            left -= run;
        }
    }
    return 1;
}

static size_t build_packet(nsp_aes67_tx *tx, const float *pcm, int silent, uint8_t *out) {
    uint32_t ch = tx->channels;
    uint32_t i;
    size_t payload = (size_t)tx->frames * ch * 3u;
    out[0] = 0x80; /* V=2,P=0,X=0,CC=0 */
    out[1] = (uint8_t)(tx->cfg.payload_type & 0x7F); /* M=0 */
    put_be16(out + 2, tx->seq);
    put_be32(out + 4, tx->timestamp);
    put_be32(out + 8, tx->cfg.ssrc);
    for (i = 0; i < (size_t)tx->frames * ch; ++i) {
        put_l24(out + AES67_RTP_HEADER + (size_t)i * 3u, silent ? 0.0f : pcm[i]);
    }
    return AES67_RTP_HEADER + payload;
}

/* ---- sender thread ------------------------------------------------------ */

typedef HANDLE (WINAPI *av_set_mm_thread_characteristics_fn)(LPCSTR, LPDWORD);
typedef BOOL (WINAPI *av_set_mm_thread_priority_fn)(HANDLE, int);

static void send_one_packet(nsp_aes67_tx *tx) {
    int have;
    int silent = 0;
    size_t len;
    EnterCriticalSection(&tx->lock);
    have = pop_packet_locked(tx);
    LeaveCriticalSection(&tx->lock);
    if (!have) {
        silent = 1;
        tx->stats.silent_packets++;
    }
    len = build_packet(tx, tx->pop, silent, tx->packet);
    {
        int sent = sendto(tx->sock, (const char *)tx->packet, (int)len, 0,
                          (const struct sockaddr *)&tx->dest, (int)sizeof(tx->dest));
        if (sent != (int)len) tx->stats.send_errors++;
        else tx->stats.bytes_sent += (uint64_t)len;
    }
    tx->stats.packets_sent++;
    tx->stats.frames_sent += tx->frames;
    tx->stats.last_seq = tx->seq;
    tx->stats.last_timestamp = tx->timestamp;
    tx->seq++;
    tx->timestamp += tx->frames;
}

static DWORD WINAPI sender_thread(LPVOID arg) {
    nsp_aes67_tx *tx = (nsp_aes67_tx *)arg;
    LARGE_INTEGER due, freq, deadline, now;
    LONGLONG period_ticks;
    HANDLE mm_task = NULL;
    HMODULE avrt = LoadLibraryA("avrt.dll");

    if (avrt) {
        av_set_mm_thread_characteristics_fn set_fn =
            (av_set_mm_thread_characteristics_fn)(void *)GetProcAddress(avrt, "AvSetMmThreadCharacteristicsA");
        av_set_mm_thread_priority_fn pri_fn =
            (av_set_mm_thread_priority_fn)(void *)GetProcAddress(avrt, "AvSetMmThreadPriority");
        if (set_fn) {
            DWORD task_index = 0;
            mm_task = set_fn("Pro Audio", &task_index);
            if (mm_task && pri_fn) pri_fn(mm_task, 2 /* AVRT_PRIORITY_HIGH */);
        }
    }

    /* Absolute QPC timeline: each period is due at start + n*period. When the
     * OS delays a wakeup (CPU contention), queued periods are sent as a bounded
     * burst so the average packet rate and RTP timeline stay correct instead of
     * drifting. Bursts are bounded; anything beyond is a resync + late_ticks. */
    QueryPerformanceFrequency(&freq);
    period_ticks = (LONGLONG)((double)freq.QuadPart * (double)tx->period_ms / 1000.0);
    QueryPerformanceCounter(&deadline);
    deadline.QuadPart += period_ticks;
    while (InterlockedCompareExchange(&tx->stop_flag, 0, 0) == 0) {
        QueryPerformanceCounter(&now);
        if (deadline.QuadPart > now.QuadPart) {
            LONGLONG remain_100ns = ((deadline.QuadPart - now.QuadPart) * 10000000LL) / freq.QuadPart + 1;
            due.QuadPart = -remain_100ns;
            if (!SetWaitableTimer(tx->timer, &due, 0, NULL, NULL, FALSE)) {
                set_error(tx, "SetWaitableTimer failed");
                break;
            }
            if (WaitForSingleObject(tx->timer, 250) == WAIT_FAILED) {
                set_error(tx, "WaitForSingleObject failed");
                break;
            }
            continue;
        }
        {
            int burst = 0;
            while (deadline.QuadPart <= now.QuadPart && burst < 64) {
                send_one_packet(tx);
                deadline.QuadPart += period_ticks;
                burst++;
            }
            if (deadline.QuadPart <= now.QuadPart) {
                deadline.QuadPart = now.QuadPart + period_ticks;
                tx->stats.late_ticks++;
            }
        }
    }
    CancelWaitableTimer(tx->timer);
    if (mm_task) {
        typedef BOOL (WINAPI *av_revert_fn)(HANDLE);
        av_revert_fn revert_fn = (av_revert_fn)(void *)GetProcAddress(avrt, "AvRevertMmThreadCharacteristics");
        if (revert_fn) revert_fn(mm_task);
    }
    if (avrt) FreeLibrary(avrt);
    InterlockedExchange(&tx->thread_exit, 1);
    return 0;
}

/* ---- public API --------------------------------------------------------- */

nsp_aes67_tx *nsp_aes67_create(const nsp_aes67_config *config, char *err, size_t err_cap) {
    nsp_aes67_tx *tx;
    WSADATA wsa;
    DWORD sock_buf = 4u * 1024u * 1024u;
    DWORD loop = 1;
    uint32_t cap_packets;

    if (err && err_cap) err[0] = '\0';
    if (!config || config->struct_size != sizeof(nsp_aes67_config)) {
        if (err && err_cap) snprintf(err, err_cap, "invalid config struct_size");
        return NULL;
    }
    if (config->channels < 1 || config->channels > NSP_AES67_MAX_CHANNELS ||
        (config->frames_per_packet != 48 && config->frames_per_packet != 96 &&
         config->frames_per_packet != 192 && config->frames_per_packet != 480) ||
        config->payload_type < 96 || config->payload_type > 127 || config->dest_ip[0] == '\0') {
        if (err && err_cap) snprintf(err, err_cap, "invalid channels/frames/PT/dest");
        return NULL;
    }
    tx = (nsp_aes67_tx *)calloc(1, sizeof(*tx));
    if (!tx) {
        if (err && err_cap) snprintf(err, err_cap, "out of memory");
        return NULL;
    }
    tx->cfg = *config;
    tx->cfg.dest_ip[sizeof(tx->cfg.dest_ip) - 1] = '\0';
    tx->cfg.src_interface_ip[sizeof(tx->cfg.src_interface_ip) - 1] = '\0';
    tx->channels = config->channels;
    tx->frames = config->frames_per_packet;
    if (tx->frames % 48u != 0u) {
        set_error(tx, "frames_per_packet must be a multiple of 48");
        goto fail;
    }
    tx->period_ms = tx->frames / 48u; /* 48 frames = 1 ms at 48 kHz */
    cap_packets = config->ring_packets ? config->ring_packets : 8u;
    if (cap_packets < 2u) cap_packets = 2u;
    if (cap_packets > 1024u) cap_packets = 1024u;
    tx->capacity_frames = cap_packets * tx->frames;
    tx->ring = (float *)calloc((size_t)tx->capacity_frames * tx->channels, sizeof(float));
    tx->pop = (float *)calloc((size_t)tx->frames * tx->channels, sizeof(float));
    tx->packet = (uint8_t *)calloc(AES67_RTP_HEADER + (size_t)tx->frames * tx->channels * 3u, 1);
    if (!tx->ring || !tx->pop || !tx->packet) {
        set_error(tx, "out of memory");
        goto fail;
    }
    tx->cfg.ssrc = config->ssrc ? config->ssrc : 0x4E535036u; /* "NSP6" */
    tx->seq = 0;
    tx->timestamp = 0;
    tx->stats.struct_size = sizeof(tx->stats);
    tx->stats.channels = tx->channels;
    tx->stats.frames_per_packet = tx->frames;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        set_error(tx, "WSAStartup failed");
        goto fail;
    }
    tx->ws_started = 1;
    tx->sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (tx->sock == INVALID_SOCKET) {
        set_error(tx, "socket() failed");
        goto fail;
    }
    setsockopt(tx->sock, SOL_SOCKET, SO_SNDBUF, (const char *)&sock_buf, sizeof(sock_buf));
    memset(&tx->dest, 0, sizeof(tx->dest));
    tx->dest.sin_family = AF_INET;
    tx->dest.sin_port = htons(config->port ? config->port : AES67_DEFAULT_PORT);
    tx->dest.sin_addr.s_addr = inet_addr(tx->cfg.dest_ip);
    if (tx->dest.sin_addr.s_addr == INADDR_NONE) {
        set_error(tx, "dest_ip is not a valid IPv4 address");
        goto fail;
    }
    if (is_multicast_ipv4(tx->dest.sin_addr.s_addr)) {
        DWORD ttl = config->ttl ? config->ttl : 32u;
        if (ttl > 255u) ttl = 255u;
        setsockopt(tx->sock, IPPROTO_IP, IP_MULTICAST_TTL, (const char *)&ttl, sizeof(ttl));
        setsockopt(tx->sock, IPPROTO_IP, IP_MULTICAST_LOOP, (const char *)&loop, sizeof(loop));
        if (tx->cfg.src_interface_ip[0] != '\0') {
            struct in_addr iface;
            iface.s_addr = inet_addr(tx->cfg.src_interface_ip);
            if (iface.s_addr == INADDR_NONE) {
                set_error(tx, "src_interface_ip is not a valid IPv4 address");
                goto fail;
            }
            setsockopt(tx->sock, IPPROTO_IP, IP_MULTICAST_IF, (const char *)&iface, sizeof(iface));
        }
    }
    InitializeCriticalSection(&tx->lock);
    tx->timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!tx->timer) tx->timer = CreateWaitableTimerW(NULL, FALSE, NULL);
    if (!tx->timer) {
        set_error(tx, "CreateWaitableTimerEx failed");
        goto fail;
    }
    if (err && err_cap) snprintf(err, err_cap, "%s", "ok");
    return tx;

fail:
    if (tx) {
        if (tx->sock != INVALID_SOCKET && tx->sock != 0) closesocket(tx->sock);
        if (tx->ws_started) WSACleanup();
        if (err && err_cap) snprintf(err, err_cap, "%s", tx->error);
        free(tx->ring);
        free(tx->pop);
        free(tx->packet);
        free(tx);
    }
    return NULL;
}

int nsp_aes67_start(nsp_aes67_tx *tx) {
    if (!tx) return NSP_AES67_ERR_ARG;
    if (tx->started) return NSP_AES67_ERR_STATE;
    if (!tx->timer) return NSP_AES67_ERR_STATE;
    InterlockedExchange(&tx->stop_flag, 0);
    InterlockedExchange(&tx->thread_exit, 0);
    tx->stats.start_ms = GetTickCount64();
    tx->thread = CreateThread(NULL, 0, sender_thread, tx, 0, NULL);
    if (!tx->thread) {
        set_error(tx, "CreateThread failed");
        return NSP_AES67_ERR_MEMORY;
    }
    tx->started = 1;
    return NSP_AES67_OK;
}

int nsp_aes67_submit(nsp_aes67_tx *tx, const float *interleaved_f32, uint32_t frames) {
    uint32_t ch;
    uint32_t dropped = 0;
    if (!tx || !interleaved_f32) return NSP_AES67_ERR_ARG;
    if (frames == 0) return NSP_AES67_OK;
    if (frames > tx->capacity_frames) return NSP_AES67_ERR_ARG;
    ch = tx->channels;
    tx->stats.submit_calls++;
    EnterCriticalSection(&tx->lock);
    /* Keep the newest audio: drop oldest frames when the ring is full. */
    while (tx->count + frames > tx->capacity_frames) {
        uint32_t drop = tx->frames;
        if (tx->count < drop) drop = tx->count;
        tx->head = (tx->head + drop) % tx->capacity_frames;
        tx->count -= drop;
        dropped += drop;
    }
    {
        uint32_t left = frames;
        uint32_t offset = 0;
        while (left > 0) {
            uint32_t tail = (tx->head + tx->count) % tx->capacity_frames;
            uint32_t run = tx->capacity_frames - tail;
            if (run > left) run = left;
            memcpy(tx->ring + (size_t)tail * ch, interleaved_f32 + (size_t)offset * ch,
                   (size_t)run * ch * sizeof(float));
            tx->count += run;
            offset += run;
            left -= run;
        }
    }
    LeaveCriticalSection(&tx->lock);
    tx->stats.ring_dropped_frames += dropped;
    tx->stats.frames_submitted += frames;
    return NSP_AES67_OK;
}

int nsp_aes67_get_stats(nsp_aes67_tx *tx, nsp_aes67_stats *out) {
    if (!tx || !out || out->struct_size != sizeof(nsp_aes67_stats)) return NSP_AES67_ERR_ARG;
    *out = tx->stats;
    return NSP_AES67_OK;
}

int nsp_aes67_stop(nsp_aes67_tx *tx) {
    if (!tx) return NSP_AES67_ERR_ARG;
    if (!tx->started) return NSP_AES67_OK;
    InterlockedExchange(&tx->stop_flag, 1);
    if (tx->timer) CancelWaitableTimer(tx->timer);
    if (tx->thread) {
        WaitForSingleObject(tx->thread, 5000);
        CloseHandle(tx->thread);
        tx->thread = NULL;
    }
    tx->stats.stop_ms = GetTickCount64();
    tx->started = 0;
    /* wait for the thread to observe the flag before releasing storage */
    {
        int spins = 0;
        while (InterlockedCompareExchange(&tx->thread_exit, 1, 1) == 0 && spins < 500) {
            Sleep(10);
            spins++;
        }
    }
    return NSP_AES67_OK;
}

void nsp_aes67_destroy(nsp_aes67_tx *tx) {
    if (!tx) return;
    if (tx->started) nsp_aes67_stop(tx);
    if (tx->timer) {
        CloseHandle(tx->timer);
        tx->timer = NULL;
    }
    if (tx->sock != INVALID_SOCKET && tx->sock != 0) closesocket(tx->sock);
    if (tx->ws_started) WSACleanup();
    DeleteCriticalSection(&tx->lock);
    free(tx->ring);
    free(tx->pop);
    free(tx->packet);
    free(tx);
}

