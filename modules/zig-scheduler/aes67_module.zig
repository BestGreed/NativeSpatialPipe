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
    // Appended 168-byte revision: PTP topology mode (DESIGN-TOPO.md).
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
    lib: std.DynLib,
    handle: *anyopaque,
    start_fn: Start,
    stop_fn: Retire,
    close_fn: Retire,
    poll_fn: Poll,
    submit_leased: Submit,
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
        const config = Config{
            .channels = @intCast(channels),
            .sample_rate = prepared.sample_rate,
            .buffer_ms = net.ringPackets,
            .prebuffer_ms = (net.prebufferFrames + prepared.frames_per_packet - 1) / prepared.frames_per_packet,
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
        var handle: ?*anyopaque = null;
        try check("create", create(&handle));
        const session = handle orelse return error.Aes67MissingHandle;
        // No worker starts until after configure succeeds. close is immediate
        // here, and the original error remains the startup result.
        errdefer _ = close_fn(session, playback.drainTimeoutMs);
        try check("configure", configure(session, &config));
        return .{ .lib = lib, .handle = session, .start_fn = start_fn, .stop_fn = stop_fn, .close_fn = close_fn,
            .poll_fn = poll_fn, .submit_leased = submit_leased, .stop_timeout_ms = playback.drainTimeoutMs };
    }

    pub fn start(self: *Session) !void {
        // Synchronous ABI call. The module owns the configured PTP deadline.
        try check("start", self.start_fn(self.handle));
        try self.checkRunning();
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
                ptp_lost_event => std.log.warn("AES67 PTP lock lost; module paused RTP", .{}),
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
        // A timed-out join leaves the handle valid. Keep the module loaded
        // and retry until the module confirms that it can be unloaded.
        while (true) {
            const result = self.close_fn(self.handle, self.stop_timeout_ms);
            if (result == 0) break;
            std.log.err("AES67 close could not retire the backend (code {d})", .{result});
            u.c.Sleep(1000);
        }
        self.lib.close();
    }
};

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
