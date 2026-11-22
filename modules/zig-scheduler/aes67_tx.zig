//! AES67 RTP/L24 UDP sender.
//! The bounded float32 ring decouples the Rust output-pool owner from the
//! network: submit() copies and returns immediately, a dedicated thread paces
//! 1 ms packets on an absolute QPC timeline and sends L24 silence on underrun.
//! No DSP, layout or resampling happens here.
const std = @import("std");
const u = @import("common.zig");
const c = u.c;
const rtp = @import("aes67_rtp.zig");
const cfg = @import("aes67_config.zig");

const CREATE_WAITABLE_TIMER_HIGH_RESOLUTION: u32 = 0x00000002;
const SEND_BUFFER_BYTES: c_int = 4 * 1024 * 1024;
const MAX_CATCH_UP_BURST: u32 = 64;

/// Snapshot of sender counters. The owner thread only reads this struct.
pub const Stats = struct {
    last_seq: u32 = 0,
    last_timestamp: u32 = 0,
    channels: u32 = 0,
    frames_per_packet: u32 = 0,
    packets_sent: u64 = 0,
    silent_packets: u64 = 0,
    frames_sent: u64 = 0,
    frames_submitted: u64 = 0,
    bytes_sent: u64 = 0,
    submit_calls: u64 = 0,
    ring_dropped_frames: u64 = 0,
    send_errors: u64 = 0,
    late_ticks: u64 = 0,
    buffered_frames: u64 = 0,
    start_ms: u64 = 0,
    stop_ms: u64 = 0,
};

/// Bounded interleaved-f32 frame ring. Oldest frames are discarded on overflow
/// so latency stays bounded; dropping whole frames is counted by the sender.
pub const FrameRing = struct {
    allocator: std.mem.Allocator,
    pcm: []f32,
    channels: usize,
    capacity_frames: usize,
    head: usize = 0,
    count: usize = 0,

    pub fn init(allocator: std.mem.Allocator, capacity_frames: usize, channels: usize) !FrameRing {
        if (capacity_frames == 0 or channels == 0 or channels > cfg.max_channels) return error.InvalidAes67Ring;
        const pcm = try allocator.alloc(f32, capacity_frames * channels);
        return .{ .allocator = allocator, .pcm = pcm, .channels = channels, .capacity_frames = capacity_frames };
    }
    pub fn deinit(self: *FrameRing) void {
        self.allocator.free(self.pcm);
    }
    pub fn buffered(self: *const FrameRing) usize {
        return self.count;
    }
    /// Copies frames into the ring with the caller's channel map
    /// (transmitted channel k = source channel channel_map[k]). Returns the
    /// number of oldest frames discarded to make room.
    pub fn push(self: *FrameRing, source: []const f32, frames: usize, source_channels: usize, channel_map: []const u16) !usize {
        if (frames == 0) return 0;
        if (frames > self.capacity_frames) return error.Aes67RingFrames;
        if (channel_map.len != self.channels) return error.Aes67ChannelMap;
        if (source.len < frames * source_channels) return error.InvalidAes67Input;
        for (channel_map) |index| if (index >= source_channels) return error.Aes67ChannelMap;
        var dropped: usize = 0;
        while (self.count + frames > self.capacity_frames) {
            self.head = (self.head + 1) % self.capacity_frames;
            self.count -= 1;
            dropped += 1;
        }
        var frame: usize = 0;
        while (frame < frames) : (frame += 1) {
            const tail = (self.head + self.count) % self.capacity_frames;
            const destination = self.pcm[tail * self.channels ..][0..self.channels];
            const input = source[frame * source_channels ..];
            for (channel_map, 0..) |pool_channel, channel| destination[channel] = input[pool_channel];
            self.count += 1;
        }
        return dropped;
    }
    pub fn popInto(self: *FrameRing, destination: []f32, frames: usize) bool {
        if (frames == 0 or self.count < frames or destination.len < frames * self.channels) return false;
        var frame: usize = 0;
        while (frame < frames) : (frame += 1) {
            const source = self.pcm[self.head * self.channels ..][0..self.channels];
            @memcpy(destination[frame * self.channels ..][0..self.channels], source);
            self.head = (self.head + 1) % self.capacity_frames;
            self.count -= 1;
        }
        return true;
    }
};

/// Absolute-deadline pacing with bounded catch-up bursts. A deadline is only
/// advanced by whole periods, so OS wake-up jitter never accumulates into clock
/// drift; anything beyond the burst budget is a counted resynchronization.
pub const Pacer = struct {
    period: u64,
    /// Optional fraction of a tick that a period also advances. QPC frequency
    /// need not be a multiple of 1000; carrying the remainder keeps the average
    /// packet rate exact instead of drifting by the truncated fraction.
    remainder: u64 = 0,
    denominator: u64 = 1,
    pending_remainder: u64 = 0,
    deadline: u64,
    max_burst: u32 = MAX_CATCH_UP_BURST,
    late_ticks: u64 = 0,

    pub fn init(start: u64, period: u64) Pacer {
        return .{ .period = period, .deadline = start };
    }
    pub fn initWithFraction(start: u64, period: u64, remainder: u64, denominator: u64) Pacer {
        return .{ .period = period, .remainder = remainder, .denominator = denominator, .deadline = start };
    }
    fn advancePeriod(self: *Pacer) void {
        self.deadline += self.period;
        if (self.denominator <= 1 or self.remainder == 0) return;
        self.pending_remainder += self.remainder;
        while (self.pending_remainder >= self.denominator) {
            self.deadline += 1;
            self.pending_remainder -= self.denominator;
        }
    }
    /// Returns how many packet periods are due at now; advances the deadline.
    pub fn due(self: *Pacer, now: u64) u32 {
        if (now < self.deadline) return 0;
        var packets: u32 = 0;
        while (self.deadline <= now and packets < self.max_burst) : (packets += 1) self.advancePeriod();
        if (self.deadline <= now) {
            self.deadline = now + self.period;
            self.pending_remainder = 0;
            self.late_ticks += 1;
        }
        return packets;
    }
};

fn addressValue(address: [4]u8) u32 {
    // sockaddr_in stores the address in network byte order; on little-endian
    // hosts that is the little-endian encoding of the dotted octets.
    return @as(u32, address[0]) | (@as(u32, address[1]) << 8) | (@as(u32, address[2]) << 16) | (@as(u32, address[3]) << 24);
}

pub const Tx = struct {
    allocator: std.mem.Allocator,
    channels: u16,
    frames: u16,
    payload_type: u8,
    ssrc: u32,
    ring: FrameRing,
    pop: []f32,
    packet: []u8,
    dest: c.sockaddr_in,
    sock: c.SOCKET = c.INVALID_SOCKET,
    timer: c.HANDLE = null,
    thread: ?std.Thread = null,
    started: bool = false,
    ws_started: bool = false,
    stop_flag: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    mutex: std.Thread.Mutex = .{},
    timeline: rtp.Timeline = .{},
    stats: Stats = .{},

    pub fn create(allocator: std.mem.Allocator, prepared: cfg.Prepared) !*Tx {
        if (prepared.channels == 0 or prepared.channels > cfg.max_channels) return error.Aes67ChannelCount;
        if (prepared.frames_per_packet != rtp.frames_per_packet) return error.UnsupportedAes67PacketFrames;
        const self = try allocator.create(Tx);
        errdefer allocator.destroy(self);
        const capacity_frames = @as(usize, prepared.ring_packets) * prepared.frames_per_packet;
        var ring = try FrameRing.init(allocator, capacity_frames, prepared.channels);
        errdefer ring.deinit();
        const pop = try allocator.alloc(f32, @as(usize, prepared.frames_per_packet) * prepared.channels);
        errdefer allocator.free(pop);
        const packet = try allocator.alloc(u8, rtp.packetBytes(prepared.frames_per_packet, prepared.channels));
        errdefer allocator.free(packet);
        self.* = .{
            .allocator = allocator,
            .channels = prepared.channels,
            .frames = prepared.frames_per_packet,
            .payload_type = prepared.payload_type,
            .ssrc = prepared.ssrc,
            .ring = ring,
            .pop = pop,
            .packet = packet,
            .dest = std.mem.zeroes(c.sockaddr_in),
        };
        var ws: c.WSADATA = undefined;
        if (c.WSAStartup(0x0202, &ws) != 0) return error.Aes67WinsockStartup;
        self.ws_started = true;
        errdefer {
            if (self.ws_started) _ = c.WSACleanup();
        }
        self.sock = c.socket(c.AF_INET, c.SOCK_DGRAM, c.IPPROTO_UDP);
        if (self.sock == c.INVALID_SOCKET) return error.Aes67Socket;
        errdefer _ = c.closesocket(self.sock);
        var send_buffer: c_int = SEND_BUFFER_BYTES;
        _ = c.setsockopt(self.sock, c.SOL_SOCKET, c.SO_SNDBUF, @ptrCast(&send_buffer), @intCast(@sizeOf(@TypeOf(send_buffer))));
        if (prepared.src_interface_ip) |source| {
            var local = std.mem.zeroes(c.sockaddr_in);
            local.sin_family = @intCast(c.AF_INET);
            local.sin_port = 0;
            local.sin_addr.S_un.S_addr = addressValue(source);
            if (c.bind(self.sock, @ptrCast(&local), @sizeOf(c.sockaddr_in)) != 0) return error.Aes67Bind;
        }
        self.dest.sin_family = @intCast(c.AF_INET);
        self.dest.sin_port = c.htons(prepared.port);
        self.dest.sin_addr.S_un.S_addr = addressValue(prepared.dest_ip);
        self.stats.channels = prepared.channels;
        self.stats.frames_per_packet = prepared.frames_per_packet;
        return self;
    }

    pub fn start(self: *Tx) !void {
        if (self.started) return error.Aes67AlreadyStarted;
        self.timer = c.CreateWaitableTimerExW(null, null, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, c.TIMER_ALL_ACCESS);
        if (self.timer == null) self.timer = c.CreateWaitableTimerW(null, 0, null);
        if (self.timer == null) return error.Aes67Timer;
        self.stop_flag.store(false, .release);
        self.stats.start_ms = c.GetTickCount64();
        self.thread = std.Thread.spawn(.{}, run, .{self}) catch |err| {
            _ = c.CloseHandle(self.timer);
            self.timer = null;
            return err;
        };
        self.started = true;
    }

    pub fn submit(self: *Tx, pcm: []const f32, frames: u32, source_channels: u16, channel_map: []const u16) !void {
        if (frames == 0) return;
        if (source_channels != self.channels) return error.Aes67ChannelMap;
        if (pcm.len < @as(usize, frames) * source_channels) return error.InvalidAes67Input;
        self.mutex.lock();
        defer self.mutex.unlock();
        const dropped = self.ring.push(pcm, frames, source_channels, channel_map) catch |err| return err;
        self.stats.submit_calls += 1;
        self.stats.frames_submitted += frames;
        self.stats.ring_dropped_frames += dropped;
    }

    pub fn snapshot(self: *Tx) Stats {
        self.mutex.lock();
        defer self.mutex.unlock();
        var result = self.stats;
        result.buffered_frames = self.ring.buffered();
        return result;
    }

    pub fn stop(self: *Tx) void {
        if (!self.started) return;
        self.stop_flag.store(true, .release);
        if (self.timer) |timer| _ = c.CancelWaitableTimer(timer);
        if (self.thread) |thread| {
            thread.join();
            self.thread = null;
        }
        self.started = false;
        if (self.timer) |timer| {
            _ = c.CloseHandle(timer);
            self.timer = null;
        }
        self.stats.stop_ms = c.GetTickCount64();
    }

    pub fn destroy(self: *Tx) void {
        self.stop();
        if (self.sock != c.INVALID_SOCKET) {
            _ = c.closesocket(self.sock);
            self.sock = c.INVALID_SOCKET;
        }
        if (self.ws_started) _ = c.WSACleanup();
        self.ring.deinit();
        const allocator = self.allocator;
        allocator.free(self.pop);
        allocator.free(self.packet);
        allocator.destroy(self);
    }

    fn updateStats(self: *Tx, silent: bool, sent_bytes: isize, expected: usize) void {
        self.mutex.lock();
        defer self.mutex.unlock();
        self.stats.packets_sent += 1;
        self.stats.frames_sent += self.frames;
        if (silent) self.stats.silent_packets += 1;
        if (sent_bytes == @as(isize, @intCast(expected))) {
            self.stats.bytes_sent += expected;
        } else {
            self.stats.send_errors += 1;
        }
        self.stats.last_seq = self.timeline.seq;
        self.stats.last_timestamp = self.timeline.timestamp;
    }

    fn sendOne(self: *Tx) void {
        self.mutex.lock();
        const have = self.ring.popInto(self.pop, self.frames);
        self.mutex.unlock();
        const size = if (have)
            rtp.buildPacket(self.packet, self.pop, self.frames, self.channels, self.timeline.seq, self.timeline.timestamp, self.ssrc, self.payload_type) catch return
        else
            rtp.buildSilentPacket(self.packet, self.frames, self.channels, self.timeline.seq, self.timeline.timestamp, self.ssrc, self.payload_type) catch return;
        const sent = c.sendto(self.sock, self.packet.ptr, @intCast(size), 0, @ptrCast(&self.dest), @sizeOf(c.sockaddr_in));
        self.updateStats(!have, sent, size);
        self.timeline.advance(self.frames);
    }

    fn run(self: *Tx) void {
        var frequency: c.LARGE_INTEGER = undefined;
        _ = c.QueryPerformanceFrequency(&frequency);
        var now: c.LARGE_INTEGER = undefined;
        _ = c.QueryPerformanceCounter(&now);
        const tick_frequency: u64 = @intCast(frequency.QuadPart);
        // 48 frames at 48 kHz is exactly 1 ms; keep the period in tick units and
        // carry the frequency remainder so the average cadence stays exact.
        const period_ticks: u64 = tick_frequency / 1000;
        var pacer = Pacer.initWithFraction(@intCast(now.QuadPart), period_ticks, tick_frequency % 1000, 1000);
        while (!self.stop_flag.load(.acquire)) {
            _ = c.QueryPerformanceCounter(&now);
            const current: u64 = @intCast(now.QuadPart);
            if (current < pacer.deadline) {
                const remain_ticks = pacer.deadline - current;
                const remain_100ns = @divTrunc(@as(i128, remain_ticks) * 10_000_000, @as(i128, @intCast(tick_frequency))) + 1;
                var due = std.mem.zeroes(c.LARGE_INTEGER);
                due.QuadPart = @intCast(-@min(remain_100ns, @as(i128, std.math.maxInt(i64))));
                if (c.SetWaitableTimer(self.timer, &due, 0, null, null, 0) == 0) {
                    self.countFailure(error.Aes67TimerSet);
                    break;
                }
                if (c.WaitForSingleObject(self.timer, 250) == c.WAIT_FAILED) {
                    self.countFailure(error.Aes67TimerWait);
                    break;
                }
                continue;
            }
            const packets = pacer.due(current);
            for (0..packets) |_| self.sendOne();
        }
        self.mutex.lock();
        self.stats.late_ticks = pacer.late_ticks;
        self.mutex.unlock();
    }

    fn countFailure(self: *Tx, failure: anyerror) void {
        std.debug.print("AES67 sender stopped: {s}\n", .{@errorName(failure)});
        self.mutex.lock();
        defer self.mutex.unlock();
        self.stats.send_errors += 1;
    }
};
