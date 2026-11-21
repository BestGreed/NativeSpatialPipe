//! AES67 (RTP/L24) output backend. Loads nsp_aes67.dll and feeds it the same
//! Rust output-pool leases the ASIO backend consumes; the DLL copies into its
//! own bounded ring, so no PCM pointer survives the submit call.
const std = @import("std");
const Stream = @import("output_stream.zig").Stream;
const StreamState = @import("output_stream.zig").State;
const playback_config = @import("desktop_playback.zig");
const native_library = @import("native_library.zig");

pub const ABI_VERSION = "nsp_aes67/1";

/// Mirrors tools/aes67-output/aes67_tx.h exactly (extern "C", MSVC/GNU x64).
pub const ConfigAbi = extern struct {
    struct_size: u32 = @sizeOf(ConfigAbi),
    dest_ip: [64]u8 = [_]u8{0} ** 64,
    port: u16 = 5004,
    channels: u16 = 12,
    frames_per_packet: u16 = 48,
    ttl: u8 = 32,
    payload_type: u8 = 96,
    ssrc: u32 = 0,
    src_interface_ip: [64]u8 = [_]u8{0} ** 64,
    ptp_domain: u16 = 0,
    ring_packets: u16 = 64,
};

pub const Stats = extern struct {
    struct_size: u32 = @sizeOf(Stats),
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
    start_ms: u64 = 0,
    stop_ms: u64 = 0,
};

pub const SdpParams = extern struct {
    struct_size: u32 = @sizeOf(SdpParams),
    session_name: ?[*:0]const u8 = null,
    dest_ip: ?[*:0]const u8 = null,
    port: u16 = 5004,
    channels: u16 = 12,
    frames_per_packet: u16 = 48,
    ttl: u8 = 32,
    payload_type: u8 = 96,
    ssrc: u32 = 0,
    origin_addr: ?[*:0]const u8 = null,
    ptp_gmid: ?[*:0]const u8 = null,
    local_mac: ?[*:0]const u8 = null,
    ptp_domain: u16 = 0,
    layout_id: ?[*:0]const u8 = null,
    smpte_order: ?[*:0]const u8 = null,
    channel_labels: ?[*]const ?[*:0]const u8 = null,
    channel_label_count: u16 = 0,
};

const CreateFn = *const fn (*const ConfigAbi, [*]u8, usize) callconv(.C) ?*anyopaque;
const StartFn = *const fn (?*anyopaque) callconv(.C) c_int;
const SubmitFn = *const fn (?*anyopaque, [*]const f32, u32) callconv(.C) c_int;
const StatsFn = *const fn (?*anyopaque, *Stats) callconv(.C) c_int;
const StopFn = *const fn (?*anyopaque) callconv(.C) c_int;
const DestroyFn = *const fn (?*anyopaque) callconv(.C) void;
const LastErrorFn = *const fn (?*anyopaque) callconv(.C) [*:0]const u8;
const AbiVersionFn = *const fn () callconv(.C) [*:0]const u8;
const BuildSdpFn = *const fn (*const SdpParams, [*]u8, usize) callconv(.C) usize;

pub const Backend = struct {
    pub const Config = struct {
        dll_path: [1024]u8 = [_]u8{0} ** 1024,
        dest_ip: [64]u8 = [_]u8{0} ** 64,
        src_interface_ip: [64]u8 = [_]u8{0} ** 64,
        port: u16 = 5004,
        frames_per_packet: u16 = 48,
        ttl: u8 = 32,
        payload_type: u8 = 96,
        ssrc: u32 = 0,
        ptp_domain: u16 = 0,
        ring_packets: u16 = 64,
        channels: u16 = 12,
        prebuffer_frames: u32 = 480,
        drain_timeout_ms: u32 = 5000,
        sdp_path: [1024]u8 = [_]u8{0} ** 1024,
        layout_id: [16]u8 = [_]u8{0} ** 16,
        smpte_order: [32]u8 = [_]u8{0} ** 32,
        ptp_gmid: [32]u8 = [_]u8{0} ** 32,
        labels: [16][32]u8 = [_][32]u8{[_]u8{0} ** 32} ** 16,
        label_count: u16 = 0,
        stream: ?*Stream = null,
        processing: @import("output_processing.zig").Config = .{},
        /// Build the backend config from the validated playback document. The
        /// SDP file is written next to the session output directory.
        pub fn fromPlayback(settings: playback_config.Config, root: []const u8, sdp_path: []const u8, channels: u16) !Config {
            const net = settings.network orelse return error.NetworkConfigRequired;
            if (channels == 0 or channels > 16) return error.Aes67ChannelCount;
            if (sdp_path.len == 0 or sdp_path.len >= 1024) return error.InvalidSdpPath;
            var config = Config{};
            try copyField(&config.dest_ip, net.destIp);
            try copyField(&config.src_interface_ip, net.srcInterfaceIp);
            config.port = net.port;
            config.frames_per_packet = net.framesPerPacket;
            config.ttl = net.ttl;
            config.payload_type = net.payloadType;
            config.ssrc = net.ssrc;
            config.ptp_domain = net.ptpDomain;
            config.ring_packets = net.ringPackets;
            config.channels = channels;
            config.prebuffer_frames = net.prebufferFrames;
            config.drain_timeout_ms = settings.drainTimeoutMs;
            if (net.dll.len != 0) {
                try copyField(&config.dll_path, net.dll);
            } else {
                var path_buf: [1024]u8 = undefined;
                const path = std.fmt.bufPrint(&path_buf, "{s}/build/aes67-output/nsp_aes67.dll", .{root}) catch return error.InvalidPlaybackConfigPath;
                try copyField(&config.dll_path, path);
            }
            @memcpy(config.sdp_path[0..sdp_path.len], sdp_path);
            @memcpy(config.layout_id[0..settings.outputLayout.len], settings.outputLayout);
            const smpte = smpteToken(settings.outputLayout) orelse "";
            @memcpy(config.smpte_order[0..smpte.len], smpte);
            if (net.ptpGmid.len != 0) @memcpy(config.ptp_gmid[0..net.ptpGmid.len], net.ptpGmid);
            const labels = try settings.labels();
            const count: usize = channels;
            config.label_count = @intCast(count);
            for (0..count) |i| try copyField(&config.labels[i], labels[i]);
            return config;
        }
        fn copyField(dst: []u8, value: []const u8) !void {
            if (value.len >= dst.len or std.mem.indexOfScalar(u8, value, 0) != null) return error.InvalidPlaybackConfigPath;
            @memset(dst, 0);
            @memcpy(dst[0..value.len], value);
        }
        fn smpteToken(layout: []const u8) ?[]const u8 {
            const table = [_]struct { id: []const u8, token: []const u8 }{
                .{ .id = "5.1.2", .token = "SMPTE2110.(51,2)" },
                .{ .id = "5.1.4", .token = "SMPTE2110.(51,4)" },
                .{ .id = "7.1.2", .token = "SMPTE2110.(71,2)" },
                .{ .id = "7.1.4", .token = "SMPTE2110.(71,4)" },
                .{ .id = "7.1.6", .token = "SMPTE2110.(71,6)" },
                .{ .id = "9.1.4", .token = "SMPTE2110.(91,4)" },
                .{ .id = "9.1.6", .token = "SMPTE2110.(91,6)" },
            };
            for (table) |row| if (std.mem.eql(u8, row.id, layout)) return row.token;
            return null;
        }
    };
    dll: ?std.DynLib = null,
    handle: ?*anyopaque = null,
    create_fn: CreateFn = undefined,
    start_fn: StartFn = undefined,
    submit_fn: SubmitFn = undefined,
    stats_fn: StatsFn = undefined,
    stop_fn: StopFn = undefined,
    destroy_fn: DestroyFn = undefined,
    error_fn: LastErrorFn = undefined,
    sdp_fn: BuildSdpFn = undefined,
    stream: ?*Stream = null,
    limits: Config = .{},
    timer: ?std.time.Timer = null,
    state: StreamState = .waiting,
    next_frame: u64 = 0,
    drain_started: u64 = 0,
    started: bool = false,
    last_code: i32 = 0,
    message: [1024]u8 = [_]u8{0} ** 1024,
    ssrc: u32 = 0,
    fn check(self: *Backend, code: i32) !void {
        self.last_code = code;
        if (code == 0) return;
        if (self.handle) |handle| {
            const text = std.mem.sliceTo(self.error_fn(handle), 0);
            const len = @min(text.len, self.message.len - 1);
            @memcpy(self.message[0..len], text[0..len]);
            self.message[len] = 0;
        }
        return switch (code) {
            -1 => error.InvalidAes67Input,
            -2 => error.Aes67State,
            -3 => error.Aes67DeviceFailure,
            -4 => error.OutOfMemory,
            else => error.Aes67DeviceFailure,
        };
    }
    pub fn errorDetail(self: *Backend, failure: anyerror) []const u8 {
        return switch (failure) {
            error.InvalidAes67Input, error.Aes67State, error.OutOfMemory, error.Aes67DeviceFailure => std.mem.sliceTo(&self.message, 0),
            else => @errorName(failure),
        };
    }
    pub fn open(self: *Backend, config: Config) !void {
        if (self.dll != null) return error.Aes67AlreadyOpen;
        if (config.stream != null and (config.channels == 0 or config.channels > 16 or config.prebuffer_frames > 48000 or
            config.drain_timeout_ms == 0 or config.frames_per_packet != 48 and config.frames_per_packet != 96 and
            config.frames_per_packet != 192 and config.frames_per_packet != 480)) return error.InvalidPlaybackConfig;
        var dll = try native_library.open(std.mem.sliceTo(&config.dll_path, 0));
        errdefer dll.close();
        const version = dll.lookup(AbiVersionFn, "nsp_aes67_abi_version") orelse return error.MissingAes67Export;
        const abi = std.mem.sliceTo(version(), 0);
        if (!std.mem.eql(u8, abi, ABI_VERSION)) return error.Aes67AbiMismatch;
        self.create_fn = dll.lookup(CreateFn, "nsp_aes67_create") orelse return error.MissingAes67Export;
        self.start_fn = dll.lookup(StartFn, "nsp_aes67_start") orelse return error.MissingAes67Export;
        self.submit_fn = dll.lookup(SubmitFn, "nsp_aes67_submit") orelse return error.MissingAes67Export;
        self.stats_fn = dll.lookup(StatsFn, "nsp_aes67_get_stats") orelse return error.MissingAes67Export;
        self.stop_fn = dll.lookup(StopFn, "nsp_aes67_stop") orelse return error.MissingAes67Export;
        self.destroy_fn = dll.lookup(DestroyFn, "nsp_aes67_destroy") orelse return error.MissingAes67Export;
        self.error_fn = dll.lookup(LastErrorFn, "nsp_aes67_last_error") orelse return error.MissingAes67Export;
        self.sdp_fn = dll.lookup(BuildSdpFn, "nsp_aes67_build_sdp") orelse return error.MissingAes67Export;
        // SDP is generated before the socket exists: a bad layout/path fails the
        // open with no network side effect.
        if (std.mem.sliceTo(&config.sdp_path, 0).len != 0) try self.writeSdp(&config);
        var abi_config = ConfigAbi{};
        abi_config.struct_size = @sizeOf(ConfigAbi);
        @memcpy(abi_config.dest_ip[0..std.mem.sliceTo(&config.dest_ip, 0).len], std.mem.sliceTo(&config.dest_ip, 0));
        @memcpy(abi_config.src_interface_ip[0..std.mem.sliceTo(&config.src_interface_ip, 0).len], std.mem.sliceTo(&config.src_interface_ip, 0));
        abi_config.port = config.port;
        abi_config.channels = config.channels;
        abi_config.frames_per_packet = config.frames_per_packet;
        abi_config.ttl = config.ttl;
        abi_config.payload_type = config.payload_type;
        abi_config.ssrc = config.ssrc;
        abi_config.ptp_domain = config.ptp_domain;
        abi_config.ring_packets = config.ring_packets;
        var err: [256]u8 = [_]u8{0} ** 256;
        const handle = self.create_fn(&abi_config, &err, err.len) orelse {
            const text = std.mem.sliceTo(&err, 0);
            const len = @min(text.len, self.message.len - 1);
            @memcpy(self.message[0..len], text[0..len]);
            self.message[len] = 0;
            return error.Aes67CreateFailed;
        };
        self.handle = handle;
        self.dll = dll;
        self.stream = config.stream;
        self.limits = config;
        self.ssrc = if (config.ssrc != 0) config.ssrc else 0x4E535036;
        self.next_frame = 0;
        self.state = .waiting;
        self.started = false;
    }
    fn writeSdp(self: *Backend, config: *const Config) !void {
        var params = SdpParams{};
        params.struct_size = @sizeOf(SdpParams);
        params.session_name = "NativeSpatialPipe AES67 output";
        params.dest_ip = @ptrCast(&config.dest_ip);
        params.port = config.port;
        params.channels = config.channels;
        params.frames_per_packet = config.frames_per_packet;
        params.ttl = config.ttl;
        params.payload_type = config.payload_type;
        params.ssrc = if (config.ssrc != 0) config.ssrc else 0x4E535036;
        params.origin_addr = null;
        params.ptp_gmid = @ptrCast(&config.ptp_gmid);
        params.ptp_domain = config.ptp_domain;
        params.layout_id = @ptrCast(&config.layout_id);
        params.smpte_order = @ptrCast(&config.smpte_order);
        var label_ptrs: [16]?[*:0]const u8 = [_]?[*:0]const u8{null} ** 16;
        const count: usize = @min(@as(usize, config.label_count), 16);
        for (0..count) |i| {
            if (std.mem.sliceTo(&config.labels[i], 0).len != 0) label_ptrs[i] = @ptrCast(&config.labels[i]);
        }
        params.channel_labels = &label_ptrs;
        params.channel_label_count = @intCast(count);
        var buffer: [8192]u8 = undefined;
        const size = self.sdp_fn(&params, &buffer, buffer.len);
        if (size == 0 or size > buffer.len) return error.Aes67SdpGeneration;
        const path = std.mem.sliceTo(&config.sdp_path, 0);
        if (std.fs.path.dirname(path)) |parent| std.fs.cwd().makePath(parent) catch {};
        var file = std.fs.cwd().createFile(path, .{}) catch return error.Aes67SdpWrite;
        defer file.close();
        file.writeAll(buffer[0..size]) catch return error.Aes67SdpWrite;
    }
    pub fn start(self: *Backend) !void {
        if (self.handle == null) return error.Aes67NotOpen;
        try self.check(self.start_fn(self.handle));
        self.timer = try std.time.Timer.start();
        self.started = true;
    }
    pub fn stop(self: *Backend) !void {
        if (self.handle == null) return;
        try self.check(self.stop_fn(self.handle));
        self.started = false;
        // Final sample after the sender thread stopped.
        if (self.stream) |stream| {
            var stats = Stats{};
            stats.struct_size = @sizeOf(Stats);
            if (self.stats_fn(self.handle, &stats) == 0) {
                self.publish(stream, stats);
                stream.update(.done, stats.frames_sent, stats.packets_sent, stats.silent_packets, 0);
            }
        }
    }
    pub fn close(self: *Backend) !void {
        if (self.handle) |handle| self.destroy_fn(handle);
        self.handle = null;
        if (self.dll) |*dll| dll.close();
        self.dll = null;
        self.stream = null;
    }
    pub fn pump(self: *Backend) !void {
        const stream = self.stream orelse return;
        self.pumpInner(stream) catch |err| {
            stream.fail(err);
            return err;
        };
    }
    fn publish(self: *Backend, stream: *Stream, stats: Stats) void {
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
            .ssrc = self.ssrc,
            .ring_dropped_frames = stats.ring_dropped_frames,
            .send_errors = stats.send_errors,
            .late_ticks = stats.late_ticks,
            .start_ms = stats.start_ms,
            .stop_ms = stats.stop_ms,
        });
    }
    fn pumpInner(self: *Backend, stream: *Stream) !void {
        if (self.state == .done) return;
        if (stream.snapshot().failure) |err| return err;
        var stats = Stats{};
        stats.struct_size = @sizeOf(Stats);
        try self.check(self.stats_fn(self.handle, &stats));
        // Fixed budget per pass, exactly like the ASIO owner: the C ring is the
        // only hand-off buffer and the lease is returned immediately after copy.
        for (0..4) |_| {
            const lease = stream.popLease() orelse break;
            // nsp_aes67_submit copies into the bounded ring and never retains
            // the pointer, so the lease returns immediately after the call.
            const result = self.submit_fn(self.handle, lease.pcm, lease.frames);
            stream.releaseLease(lease);
            try self.check(result);
            self.next_frame += lease.frames;
        }
        var after = Stats{};
        after.struct_size = @sizeOf(Stats);
        try self.check(self.stats_fn(self.handle, &after));
        self.publish(stream, after);
        const now = if (self.timer) |*timer| timer.read() else 0;
        const ended = stream.ended();
        switch (self.state) {
            .waiting => {
                if (ended) {
                    self.state = .draining;
                    self.drain_started = now;
                } else if (after.frames_submitted >= self.limits.prebuffer_frames) {
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
                if (after.frames_sent >= after.frames_submitted) {
                    self.state = .done;
                } else if (now >= self.drain_started and now - self.drain_started >= @as(u64, self.limits.drain_timeout_ms) * std.time.ns_per_ms) {
                    return error.OutputTimeout;
                }
            },
            else => {},
        }
        stream.update(self.state, after.frames_sent, after.packets_sent, after.silent_packets, 0);
        stream.metrics(48000, after.frames_per_packet, 0, 0, 0, 0, self.limits.prebuffer_frames, 0);
    }
};

pub const Worker = @import("output_control.zig").Worker(Backend);