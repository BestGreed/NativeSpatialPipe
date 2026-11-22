//! AES67 output backend for the generic output owner worker. It consumes the
//! same Rust output-pool leases as the ASIO/WASAPI sinks, copies them into the
//! sender ring and owns the transport state machine. No DSP or layout logic.
const std = @import("std");
const u = @import("common.zig");
const Stream = @import("output_stream.zig").Stream;
const StreamState = @import("output_stream.zig").State;
const processing = @import("output_processing.zig");
const playback_config = @import("desktop_playback.zig");
const rtp = @import("aes67_rtp.zig");
const cfg = @import("aes67_config.zig");
const tx_mod = @import("aes67_tx.zig");

pub const Backend = struct {
    pub const Config = struct {
        // Owned copy of the validated transport plan (flows/labels/map are fixed
        // size so the generic worker can keep this across owner-thread calls).
        prepared: cfg.Prepared,
        sdp_path: [1024]u8 = [_]u8{0} ** 1024,
        layout_id: [16]u8 = [_]u8{0} ** 16,
        labels: [16][24]u8 = [_][24]u8{[_]u8{0} ** 24} ** 16,
        label_count: u8 = 0,
        session_name: [64]u8 = [_]u8{0} ** 64,
        processing: processing.Config = .{},
        drain_timeout_ms: u32 = 5000,
        stream: ?*Stream = null,

        /// Builds the transport config from an already validated playback
        /// document. Channel count/order/labels come from outputLayout and the
        /// configured routing (or an explicit aes67.channelMap), never from a
        /// layout baked into AES67.
        pub fn fromPlayback(settings: playback_config.Config, out: []const u8, channels: u16) !Config {
            const file = settings.aes67 orelse return error.Aes67ConfigRequired;
            if (channels == 0 or channels > cfg.max_channels) return error.Aes67ChannelCount;
            var prepared = try cfg.prepare(file, channels);
            // Wire channel k <- source channel k of the renderer output. The
            // explicit aes67.channelMap wins; otherwise the existing routing
            // sources() order is used (layouts without channelMap keep it).
            if (file.channelMap == null) {
                const sources = try settings.sources();
                for (0..channels) |index| prepared.channel_map[index] = @intCast(sources[index]);
            }
            var result = Config{
                .prepared = prepared,
                .processing = settings.processingConfig(),
                .drain_timeout_ms = settings.drainTimeoutMs,
            };
            const preset = try settings.preset();
            for (0..channels) |channel| {
                const source = prepared.channel_map[channel];
                if (source >= channels) return error.InvalidSourceChannel;
                result.labels[channel] = copyLabel(preset.labels[source]);
            }
            result.label_count = @intCast(channels);
            copyBytes(&result.layout_id, preset.id);
            const sdp_path = try u.join(&.{ out, "aes67.sdp" });
            defer u.a.free(sdp_path);
            if (sdp_path.len >= result.sdp_path.len) return error.InvalidSdpPath;
            copyBytes(&result.sdp_path, sdp_path);
            copyBytes(&result.session_name, "NativeSpatialPipe AES67 output");
            return result;
        }
    };

    tx: ?*tx_mod.Tx = null,
    stream: ?*Stream = null,
    limits: Config = undefined,
    timer: ?std.time.Timer = null,
    state: StreamState = .waiting,
    next_frame: u64 = 0,
    drain_started: u64 = 0,
    started: bool = false,
    message: [1024]u8 = [_]u8{0} ** 1024,

    pub fn errorDetail(self: *Backend, failure: anyerror) []const u8 {
        const text = std.mem.sliceTo(&self.message, 0);
        return if (text.len != 0) text else @errorName(failure);
    }
    fn setError(self: *Backend, text: []const u8) void {
        const length = @min(text.len, self.message.len - 1);
        @memcpy(self.message[0..length], text[0..length]);
        self.message[length] = 0;
    }
    pub fn open(self: *Backend, config: Config) !void {
        if (self.tx != null) return error.Aes67AlreadyOpen;
        const plan = config.prepared;
        if (plan.channels == 0 or plan.channels > cfg.max_channels or
            plan.flow_count == 0 or plan.flow_count > cfg.max_flows) return error.InvalidAes67Config;
        if (config.label_count != 0 and config.label_count != plan.channels) return error.InvalidAes67Config;
        // SDP is written before the socket exists, so bad SDP parameters fail
        // closed with no network side effect.
        try self.writeSdp(&config);
        self.tx = tx_mod.Tx.create(std.heap.page_allocator, plan) catch |err| {
            self.setError(@errorName(err));
            return error.Aes67CreateFailed;
        };
        self.stream = config.stream;
        self.limits = config;
        self.state = .waiting;
        self.started = false;
        self.next_frame = 0;
    }
    pub fn start(self: *Backend) !void {
        const tx = self.tx orelse return error.Aes67NotOpen;
        tx.start() catch |err| {
            self.setError(@errorName(err));
            return error.Aes67StartFailed;
        };
        self.timer = try std.time.Timer.start();
        self.started = true;
    }
    pub fn stop(self: *Backend) !void {
        if (self.tx) |tx| tx.stop();
        self.started = false;
        if (self.stream) |stream| {
            const stats = if (self.tx) |tx| tx.snapshot() else tx_mod.Stats{};
            self.publish(stream, stats);
            stream.update(.done, stats.frames_sent, stats.packets_sent, stats.silent_packets, 0);
        }
    }
    pub fn close(self: *Backend) !void {
        if (self.tx) |tx| {
            tx.destroy();
            self.tx = null;
        }
        self.stream = null;
    }
    pub fn pump(self: *Backend) !void {
        const stream = self.stream orelse return;
        self.pumpInner(stream) catch |err| {
            stream.fail(err);
            return err;
        };
    }
    fn publish(self: *Backend, stream: *Stream, stats: tx_mod.Stats) void {
        _ = self;
        stream.updateNet(.{
            .packets_sent = stats.packets_sent,
            .silent_packets = stats.silent_packets,
            .frames_sent = stats.frames_sent,
            .frames_submitted = stats.frames_submitted,
            .bytes_sent = stats.bytes_sent,
            .last_seq = stats.last_seq,
            .last_timestamp = stats.last_timestamp,
            .frames_per_packet = stats.frames_per_packet,
            .channels = stats.channels,
            .ssrc = stats.ssrc,
            .ring_dropped_frames = stats.ring_dropped_frames,
            .send_errors = stats.send_errors,
            .late_ticks = stats.late_ticks,
            .buffered_frames = stats.buffered_frames,
            .start_ms = stats.start_ms,
            .stop_ms = stats.stop_ms,
        });
    }
    fn pumpInner(self: *Backend, stream: *Stream) !void {
        if (self.state == .done) return;
        if (stream.snapshot().failure) |failure| return failure;
        const tx = self.tx orelse return error.Aes67NotOpen;
        // Bounded hand-off: the sender ring is the only buffer between the pool
        // owner and the network. submit() copies, so the lease returns at once.
        for (0..4) |_| {
            const lease = stream.popLease() orelse break;
            const channels = self.limits.prepared.channels;
            const lease_pcm = lease.pcm[0..@as(usize, lease.frames) * channels];
            const result = tx.submit(lease_pcm, lease.frames, @intCast(channels), self.limits.prepared.channel_map[0..channels]);
            stream.releaseLease(lease);
            try result;
            self.next_frame += lease.frames;
        }
        const stats = tx.snapshot();
        self.publish(stream, stats);
        const now = if (self.timer) |*timer| timer.read() else 0;
        const ended = stream.ended();
        switch (self.state) {
            .waiting => {
                if (ended) {
                    self.state = .draining;
                    self.drain_started = now;
                } else if (stats.frames_submitted >= self.limits.prepared.prebuffer_frames) {
                    self.state = .playing;
                }
            },
            .playing => {
                if (ended) {
                    self.state = .draining;
                    self.drain_started = now;
                }
            },
            .draining => {
                if (stats.frames_sent >= stats.frames_submitted) {
                    self.state = .done;
                } else if (now >= self.drain_started and now - self.drain_started >= @as(u64, self.limits.drain_timeout_ms) * std.time.ns_per_ms) {
                    return error.OutputTimeout;
                }
            },
            else => {},
        }
        stream.update(self.state, stats.frames_sent, stats.packets_sent, stats.silent_packets, 0);
        const prebuffer: u32 = @intCast(self.limits.prepared.prebuffer_frames);
        stream.metrics(rtp.sample_rate, stats.frames_per_packet, @intCast(stats.buffered_frames), 0, 0, 0, prebuffer, 0);
    }
    fn writeSdp(self: *Backend, config: *const Config) !void {
        _ = self;
        var labels: [16][]const u8 = undefined;
        const count: usize = config.label_count;
        for (0..count) |index| labels[index] = std.mem.sliceTo(&config.labels[index], 0);
        const layout = std.mem.sliceTo(&config.layout_id, 0);
        const gmid: ?[]const u8 = if (config.prepared.ptp_gmid) |*value| value[0..] else null;
        var params: [cfg.max_flows]rtp.SdpParams = undefined;
        // Per-flow scratch must outlive the loop: iterate by pointer so
        // &flow.dest_text points into the stable prepared array. A by-value
        // capture dangled every session's c=/source-filter onto the LAST
        // flow's values (the SDP dump bug caught in real-network
        // verification; the same pattern is fixed in the offline tools).
        var source_buffers: [cfg.max_flows][15]u8 = undefined;
        for (config.prepared.flows[0..config.prepared.flow_count], 0..) |*flow, index| {
            const offset: usize = flow.channel_offset;
            // RFC 4570 source-filter address follows the flow's own effective
            // egress interface (flow-level override or the session-level one).
            const source_text: ?[]const u8 = if (flow.source_ip) |source| blk: {
                source_buffers[index] = cfg.formatIpv4(source);
                break :blk std.mem.sliceTo(&source_buffers[index], 0);
            } else null;
            params[index] = .{
                .session_name = std.mem.sliceTo(&config.session_name, 0),
                .dest_ip = std.mem.sliceTo(&flow.dest_text, 0),
                .port = flow.port,
                .channels = flow.channels,
                .frames_per_packet = config.prepared.frames_per_packet,
                .payload_type = flow.payload_type,
                .ssrc = flow.ssrc,
                .ptp_gmid = gmid,
                .ptp_domain = config.prepared.ptp_domain,
                .layout_id = layout,
                .channel_labels = labels[offset .. offset + flow.channels],
                .flow_index = @intCast(index),
                .channel_offset = flow.channel_offset,
                // RFC 4566: multicast connections carry the TTL suffix in c=.
                .ttl = if (flow.multicast) flow.ttl else null,
                .source_filter = config.prepared.source_filter,
                .source_addr = source_text,
            };
        }
        var buffer: [rtp.sdp_max_bytes]u8 = undefined;
        const size = rtp.writeSdpMulti(&buffer, params[0..config.prepared.flow_count]) catch return error.Aes67SdpGeneration;
        const path = std.mem.sliceTo(&config.sdp_path, 0);
        if (std.fs.path.dirname(path)) |parent| std.fs.cwd().makePath(parent) catch {};
        var file = std.fs.cwd().createFile(path, .{}) catch return error.Aes67SdpWrite;
        defer file.close();
        file.writeAll(buffer[0..size]) catch return error.Aes67SdpWrite;
    }
};

fn copyBytes(destination: []u8, text: []const u8) void {
    const count = @min(text.len, destination.len);
    @memset(destination, 0);
    @memcpy(destination[0..count], text[0..count]);
}

fn copyLabel(text: []const u8) [24]u8 {
    var result = [_]u8{0} ** 24;
    const count = @min(text.len, result.len - 1);
    @memcpy(result[0..count], text[0..count]);
    return result;
}

pub const Worker = @import("output_control.zig").Worker(Backend);
