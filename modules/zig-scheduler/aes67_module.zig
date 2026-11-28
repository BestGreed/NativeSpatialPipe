//! Control-plane adapter for the standalone AES67 DLL. The native pool calls
//! the module directly. This file never touches PCM or network resources.
const std = @import("std");
const u = @import("common.zig");
const settings = @import("aes67_config.zig");
const Playback = @import("desktop_playback.zig").Config;

// Mirror modules/aes67/nsp_aes67.h so the scheduler builds and tests without
// the optional DLL. The product build checks the actual library's exports.
const Flow = extern struct {
    dest_ip: [4]u8,
    src_interface_ip: [4]u8,
    port: u16,
    ttl: u8,
    channels: u8,
    ssrc: u32,
    channel_offset: u32,
    payload_type: u8,
    source_filter: u8,
    reserved: [2]u8 = .{ 0, 0 },
};
const Config = extern struct {
    struct_size: u32 = @sizeOf(Config),
    channels: u32,
    sample_rate: u32 = 48000,
    buffer_ms: u32,
    prebuffer_ms: u32,
    timestamping: u32,
    lock_timeout_ms: u32,
    loss_timeout_ms: u32 = 0,
    ptp_domain: u32,
    ptp_interface_ip: [4]u8,
    ptp_multicast_ip: [4]u8 = .{ 0, 0, 0, 0 },
    ptp_master_ip: [4]u8 = .{ 0, 0, 0, 0 },
    ptp_gmid: [8]u8 = .{0} ** 8,
    ptp_event_port: u16 = 0,
    ptp_general_port: u16 = 0,
    ptp_local_event_port: u16 = 0,
    ptp_local_general_port: u16 = 0,
    sap: u8 = 1,
    default_ttl: u8,
    default_port: u16 = 0,
    default_dest_ip: [4]u8 = .{ 0, 0, 0, 0 },
    default_src_interface_ip: [4]u8,
    ssrc_base: u32,
    flow_count: u32,
    flows: [*]const Flow,
    channel_labels: [*]const [*:0]const u8,
    session_name: ?[*:0]const u8 = null,
    channel_map: [*]const u32,
    input_channels: u32 = 0,
    default_payload_type: u8,
    default_source_filter: u8 = 0,
    flow_mode: u8 = 0,
    redundant: u8 = 0,
    packet_time_us: u32 = 1000,
    sap_interval_ms: u32 = 30000,
    frames_per_packet: u32 = 0,
    sap_address: [4]u8 = .{ 224, 2, 127, 254 },
    bit_depth: u8 = 0,
    port_policy: u8 = 0,
    reserved: [6]u8 = .{0} ** 6,
    // Appended 168-byte revision: PTP topology mode.
    ptp_announce_timeout_ms: u32 = 6000,
    ptp_mode: u8 = 0,
    ptp_priority1: u8 = 128,
    ptp_priority2: u8 = 128,
    ptp_gm_capable: u8 = 0,
    ptp_gm_utc_offset: i16 = -1,
    reserved2: [6]u8 = .{0} ** 6,
};
comptime {
    if (@sizeOf(Config) != 168) @compileError("AES67 ABI Config mirror must stay 168 bytes");
}

const Event = extern struct { kind: u32, code: i32, detail: i32 };
/// Mirror of session::Counters from the Rust DLL (nsp_aes67_counters).
const Counters = extern struct {
    packets: u64,
    silent: u64,
    dropped: u64,
    late: u64,
    errors: u64,
    bytes: u64,
    locked: u32,
    timestamping: u32,
    offset_ns: f64,
    rate_ppm: f64,
    fit_rms_ns: f64,
};
/// Mirror of abi::PtpStatusV1 (nsp_aes67_ptp_status).
const PtpStatus = extern struct {
    mode: u32,
    role: u32,
    source: u32,
    domain: u32,
    time_traceable: u32,
    ptp_timescale: u32,
    gm_identity: [8]u8,
    reason: [32]u8,
    transitions: u64,
    reserved: u32,
};
comptime {
    if (@sizeOf(Counters) != 80) @compileError("AES67 Counters ABI mirror must stay 80 bytes");
    if (@sizeOf(PtpStatus) != 80) @compileError("AES67 PtpStatus ABI mirror must stay 80 bytes");
}
/// Counters and PTP status are optional exports: a DLL built before they were
/// added keeps working, the diagnostics simply report null.
pub const Diagnostics = struct {
    counters: ?Counters,
    ptp: ?PtpStatus,
};
const started_event = 1;
const sender_exited_event = 2;
const stop_complete_event = 3;
const ptp_lost_event = 4;
const join_timeout = -6;
const unspecified_ip = [4]u8{ 0, 0, 0, 0 };

pub const Session = struct {
    const Create = *const fn (*?*anyopaque) callconv(.C) c_int;
    const Configure = *const fn (*anyopaque, *const Config) callconv(.C) c_int;
    const Start = *const fn (*anyopaque) callconv(.C) c_int;
    const Retire = *const fn (*anyopaque, u32) callconv(.C) c_int;
    const Poll = *const fn (*anyopaque, *Event) callconv(.C) c_int;
    const Submit = *const @TypeOf(@import("output_stream.zig").publishPool);
    const CountersFn = *const fn (*anyopaque, *Counters) callconv(.C) c_int;
    const PtpStatusFn = *const fn (*anyopaque, *PtpStatus) callconv(.C) c_int;
    /// nsp_aes67_sdp(handle, flow_index, out, capacity): required bytes
    /// including NUL, negative on invalid/state.
    const SdpFn = *const fn (*anyopaque, u32, ?[*]u8, usize) callconv(.C) c_int;
    lib: std.DynLib,
    handle: *anyopaque,
    start_fn: Start,
    stop_fn: Retire,
    close_fn: Retire,
    poll_fn: Poll,
    submit_leased: Submit,
    counters_fn: ?CountersFn = null,
    ptp_status_fn: ?PtpStatusFn = null,
    sdp_fn: ?SdpFn = null,
    stop_timeout_ms: u32,
    started: bool = false,
    failed: bool = false,
    stopped: bool = false,

    pub fn open(root: []const u8, playback: Playback) !Session {
        const net = playback.aes67 orelse return error.Aes67ConfigRequired;
        const preset = try playback.preset();
        const channels = playback.channelCount();
        const prepared = try settings.prepare(net, channels);
        const ptp = net.ptp orelse settings.Ptp{ .domain = net.ptpDomain };
        var flows: [settings.max_flows]Flow = undefined;
        for (prepared.flows[0..prepared.flow_count], flows[0..prepared.flow_count]) |flow, *dest| {
            dest.* = .{
                .dest_ip = flow.dest_ip,
                .src_interface_ip = flow.source_ip orelse unspecified_ip,
                .port = flow.port,
                .ttl = flow.ttl,
                .channels = @intCast(flow.channels),
                .ssrc = flow.ssrc,
                .channel_offset = flow.channel_offset,
                .payload_type = flow.payload_type,
                .source_filter = @intFromBool(net.sourceFilter and flow.multicast),
            };
        }
        const sources = try playback.sources();
        var channel_map: [settings.max_channels]u32 = undefined;
        var labels: [settings.max_channels][*:0]const u8 = undefined;
        var arena = std.heap.ArenaAllocator.init(u.a);
        defer arena.deinit();
        // aes67.name wins; otherwise the device name is NSP-<COMPUTERNAME>
        // (COMPUTERNAME missing or unreadable falls back to "AES67"). The ABI
        // copies the name during configure, so the arena only keeps it valid
        // for this open() call.
        const configured_name = std.mem.trim(u8, net.name, " \t\r\n");
        const session_name: [:0]const u8 = if (configured_name.len != 0)
            try arena.allocator().dupeZ(u8, configured_name)
        else blk: {
            const computer_name = std.process.getEnvVarOwned(arena.allocator(), "COMPUTERNAME") catch |err| switch (err) {
                error.OutOfMemory => return err,
                else => "AES67",
            };
            const host = if (computer_name.len != 0) computer_name else "AES67";
            break :blk try std.fmt.allocPrintZ(arena.allocator(), "NSP-{s}", .{host});
        };
        for (0..channels) |index| {
            const source = if (net.channelMap) |map| map[index] else sources[index];
            channel_map[index] = source;
            labels[index] = try arena.allocator().dupeZ(u8, preset.labels[source]);
        }
        // The ABI field is milliseconds, while the config stores source frames.
        // Round up so the ring always covers the requested frames, and reject a
        // prebuffer the ring cannot hold before configure() can only answer -22.
        const prebuffer_ms = prebufferMilliseconds(net.prebufferFrames, prepared.sample_rate);
        if (prebuffer_ms == 0 or prebuffer_ms > @min(net.ringPackets, 1000)) {
            std.log.err("AES67 prebuffer {d} frames needs {d} ms; ring holds {d} ms", .{ net.prebufferFrames, prebuffer_ms, net.ringPackets });
            return error.Aes67PrebufferExceedsRing;
        }
        const config = Config{
            .channels = @intCast(channels),
            .sample_rate = prepared.sample_rate,
            .buffer_ms = net.ringPackets,
            .prebuffer_ms = prebuffer_ms,
            .timestamping = @intFromEnum(ptp.timestamping),
            .lock_timeout_ms = ptp.lockTimeoutMs,
            .ptp_domain = ptp.domain,
            .ptp_interface_ip = prepared.src_interface_ip orelse prepared.flows[0].source_ip orelse unspecified_ip,
            .default_ttl = net.ttl,
            .default_port = prepared.port,
            .default_src_interface_ip = prepared.src_interface_ip orelse unspecified_ip,
            .ssrc_base = net.ssrc,
            .flow_count = prepared.flow_count,
            .flows = &flows,
            .channel_labels = &labels,
            .session_name = session_name.ptr,
            .channel_map = &channel_map,
            .default_payload_type = net.payloadType,
            .flow_mode = prepared.abi_flow_mode,
            .redundant = @intFromBool(prepared.redundant),
            .packet_time_us = prepared.packet_time_us,
            .sap_interval_ms = prepared.sap_interval_ms,
            .frames_per_packet = prepared.frames_per_packet,
            .sap_address = prepared.sap_address,
            .bit_depth = @intFromEnum(prepared.bit_depth),
            .port_policy = @intFromEnum(prepared.port_policy),
            .ptp_announce_timeout_ms = ptp.announceTimeoutMs,
            .ptp_mode = ptp.abiMode(),
            .ptp_priority1 = ptp.priority1,
            .ptp_priority2 = ptp.priority2,
            .ptp_gm_capable = ptp.abiGmCapable(),
            .ptp_gm_utc_offset = ptp.abiUtcOffset(),
        };
        const path = try u.join(&.{ root, @import("native.zig").default_aes67_module });
        defer u.a.free(path);
        var lib = try u.library(path);
        errdefer lib.close();
        const create = try u.symbol(Create, &lib, "nsp_aes67_create");
        const configure = try u.symbol(Configure, &lib, "nsp_aes67_configure");
        const start_fn = try u.symbol(Start, &lib, "nsp_aes67_start");
        const stop_fn = try u.symbol(Retire, &lib, "nsp_aes67_stop");
        const close_fn = try u.symbol(Retire, &lib, "nsp_aes67_close");
        const poll_fn = try u.symbol(Poll, &lib, "nsp_aes67_poll_lifecycle_event");
        const submit_leased = try u.symbol(Submit, &lib, "nsp_aes67_submit_leased");
        // Optional observability exports: older DLLs lack them.
        const counters_fn = lib.lookup(CountersFn, "nsp_aes67_counters");
        const ptp_status_fn = lib.lookup(PtpStatusFn, "nsp_aes67_ptp_status");
        const sdp_fn = lib.lookup(SdpFn, "nsp_aes67_sdp");
        var handle: ?*anyopaque = null;
        try check("create", create(&handle));
        const session = handle orelse return error.Aes67MissingHandle;
        // No worker starts until after configure succeeds. close is immediate
        // here, and the original error remains the startup result.
        errdefer _ = close_fn(session, playback.drainTimeoutMs);
        try check("configure", configure(session, &config));
        return .{ .lib = lib, .handle = session, .start_fn = start_fn, .stop_fn = stop_fn, .close_fn = close_fn,
            .poll_fn = poll_fn, .submit_leased = submit_leased, .counters_fn = counters_fn,
            .ptp_status_fn = ptp_status_fn, .sdp_fn = sdp_fn, .stop_timeout_ms = playback.drainTimeoutMs };
    }

    pub fn start(self: *Session) !void {
        // Synchronous ABI call. The module owns the configured PTP deadline.
        try check("start", self.start_fn(self.handle));
        try self.checkRunning();
    }

    /// Counters plus PTP status, for the host's periodic diagnostics file.
    /// Both exports are optional so a pre-diagnostics DLL still opens.
    pub fn diagnostics(self: *Session) !Diagnostics {
        return .{
            .counters = if (self.counters_fn) |function| blk: {
                var counters = std.mem.zeroes(Counters);
                if (function(self.handle, &counters) != 0) return error.Aes67DiagnosticsUnavailable;
                break :blk counters;
            } else null,
            .ptp = if (self.ptp_status_fn) |function| blk: {
                var status = std.mem.zeroes(PtpStatus);
                // STATE means no PTP status exists yet (before start); null is
                // the honest value, not an error.
                if (function(self.handle, &status) != 0) break :blk null;
                break :blk status;
            } else null,
        };
    }

    /// One flow's SDP text, or null when no SDP exists yet (before the first
    /// media clock) or the DLL predates the export. Caller owns the slice.
    pub fn sdpText(self: *Session, flow: u32) !?[]u8 {
        const function = self.sdp_fn orelse return null;
        var buffer: [4096]u8 = undefined;
        const result = function(self.handle, flow, &buffer, buffer.len);
        if (result <= 0) return null;
        const size: usize = @intCast(result);
        if (size > buffer.len) return null;
        // size includes the terminating NUL.
        return try u.a.dupe(u8, buffer[0 .. size - 1]);
    }

    pub fn checkRunning(self: *Session) !void {
        try self.poll();
        if (!self.started) return error.Aes67MissingStartedEvent;
        if (self.failed) return error.Aes67OutputFailed;
    }

    pub fn poll(self: *Session) !void {
        while (true) {
            var event: Event = undefined;
            const result = self.poll_fn(self.handle, &event);
            if (result == 0) return;
            if (result != 1) return error.Aes67LifecyclePoll;
            switch (event.kind) {
                started_event => self.started = true,
                sender_exited_event => {
                    self.failed = true;
                    std.log.err("AES67 sender exited (code {d}, detail {d})", .{ event.code, event.detail });
                },
                stop_complete_event => self.stopped = true,
                // PTP_LOST is emitted when holdover starts; RTP keeps flowing
                // until the holdover deadline expires. The old text claimed RTP
                // had already stopped and misled field diagnosis.
                ptp_lost_event => std.log.warn("AES67 PTP lock lost; RTP continues through holdover until its deadline", .{}),
                else => {},
            }
        }
    }

    pub fn stop(self: *Session) !void {
        try check("stop", self.stop_fn(self.handle, self.stop_timeout_ms));
        try self.poll();
        if (!self.stopped) return error.Aes67MissingStopCompleteEvent;
    }

    pub fn close(self: *Session) void {
        // A timed-out join leaves the handle valid. Retry until the module
        // confirms that it can be unloaded, but never sleep forever: the host
        // process may exit and an unloadable module must stay loaded instead of
        // hanging shutdown. Unloading the library while the handle is live
        // would leave a dangling callback, so the timeout leaves it loaded.
        const deadline = std.time.milliTimestamp() + @as(i64, self.stop_timeout_ms) + 5_000;
        while (true) {
            const result = self.close_fn(self.handle, self.stop_timeout_ms);
            if (result == 0) {
                self.lib.close();
                return;
            }
            std.log.err("AES67 close could not retire the backend (code {d})", .{result});
            if (std.time.milliTimestamp() >= deadline) {
                std.log.err("AES67 close exceeded its {d} ms budget; keeping the module loaded", .{self.stop_timeout_ms + 5_000});
                return;
            }
            u.c.Sleep(200);
        }
    }
};

/// Source frames to ABI milliseconds (the ABI prebuffer field is time, not
/// packets; the old ceil(frames / frames_per_packet) only worked for 1 ms).
fn prebufferMilliseconds(frames: u32, sample_rate: u32) u32 {
    return (frames * 1000 + sample_rate - 1) / sample_rate;
}

test "prebuffer is expressed in milliseconds for every packet time" {
    try std.testing.expectEqual(@as(u32, 10), prebufferMilliseconds(480, 48_000));
    try std.testing.expectEqual(@as(u32, 1), prebufferMilliseconds(48, 48_000));
    try std.testing.expectEqual(@as(u32, 10), prebufferMilliseconds(960, 96_000));
}

test "diagnostics ABI mirrors the Rust Counters/PtpStatus layouts" {
    try std.testing.expectEqual(@as(usize, 80), @sizeOf(Counters));
    try std.testing.expectEqual(@as(usize, 0), @offsetOf(Counters, "packets"));
    try std.testing.expectEqual(@as(usize, 40), @offsetOf(Counters, "bytes"));
    try std.testing.expectEqual(@as(usize, 56), @offsetOf(Counters, "offset_ns"));
    try std.testing.expectEqual(@as(usize, 80), @sizeOf(PtpStatus));
    try std.testing.expectEqual(@as(usize, 24), @offsetOf(PtpStatus, "gm_identity"));
    try std.testing.expectEqual(@as(usize, 32), @offsetOf(PtpStatus, "reason"));
    try std.testing.expectEqual(@as(usize, 64), @offsetOf(PtpStatus, "transitions"));
}

fn check(operation: []const u8, code: c_int) !void {
    if (code == 0) return;
    std.log.err("AES67 {s} failed (code {d})", .{ operation, code });
    return switch (code) {
        -3 => error.Aes67NetworkIo,
        -4 => error.Aes67TimestampUnavailable,
        -5 => error.Aes67LockTimeout,
        // EXTENSION: gm mode requested without a readable interface MAC.
        -10 => error.Aes67PtpIdentityUnavailable,
        join_timeout => error.Aes67JoinTimeout,
        else => error.Aes67ModuleFailure,
    };
}
