const std = @import("std");
const u = @import("common.zig");
const a = u.a;
const c = u.c;
pub const Hook = struct {
    root: []const u8,
    pid: u32,
    config: c.HookConfig,
    path: []const u8,
    pub fn init(root: []const u8, out: []const u8, pid: u32, owner: u32, created: u64, fixture: bool) !Hook {
        var nonce: [8]u8 = undefined;
        std.crypto.random.bytes(&nonce);
        var self = Hook{ .root = root, .pid = pid, .config = std.mem.zeroes(c.HookConfig), .path = try u.fmt("{s}/hook-config-{s}.bin", .{ out, std.fmt.fmtSliceHexLower(&nonce) }) };
        self.config.magic = c.NSP_HOOK_MAGIC;
        self.config.size = @sizeOf(c.HookConfig);
        self.config.fixture = @intFromBool(fixture);
        self.config.duration_ms = 0;
        self.config.budget = std.math.maxInt(u64);
        self.config.owner_pid = owner;
        self.config.host_pid = c.GetCurrentProcessId();
        self.config.target_created = created;
        return self;
    }
    pub fn text(dest: []u16, value: []const u8) !void {
        const w = try u.wide(value);
        defer a.free(w);
        if (w.len >= dest.len) return error.PathTooLong;
        @memset(dest, 0);
        @memcpy(dest[0..w.len], w);
    }
    pub fn call(self: *Hook, action: []const u8) !std.json.Parsed(u.Value) {
        try u.write(self.path, std.mem.asBytes(&self.config));
        const exe = try u.join(&.{ self.root, "build/native-hook-managed-v2/nsp_hook_control.exe" });
        defer a.free(exe);
        const dll = try u.join(&.{ self.root, "build/native-hook-managed-v2/nsp_managed_hook_v2.dll" });
        defer a.free(dll);
        const pid = try u.fmt("{d}", .{self.pid});
        defer a.free(pid);
        const bytes = try u.run(&.{ exe, pid, dll, action, self.path });
        defer a.free(bytes);
        return u.parse(bytes);
    }
};
pub const Pipeline = struct {
    lib: std.DynLib,
    handle: ?*anyopaque,
    create: *const fn (u32, u32, u32, u32, [*:0]const u8) callconv(.C) ?*anyopaque,
    finishFn: *const fn (?*anyopaque, ?[*]u8, usize) callconv(.C) usize,
    destroy: *const fn (?*anyopaque) callconv(.C) void,
    enableFn: *const fn (?*anyopaque, u32) callconv(.C) c_int,
    tap: *const fn (?*anyopaque, [*]u8, usize) callconv(.C) usize,
    objects: *const fn (?*anyopaque, [*]u8, usize) callconv(.C) usize,
    submit: *const fn (?*anyopaque, u64, u32, [*]const u8, usize) callconv(.C) c_int,
    pub fn init(dll: []const u8, path: []const u8, capacity: u32) !Pipeline {
        var lib = try u.library(dll);
        errdefer lib.close();
        var p: Pipeline = undefined;
        p.create = try u.symbol(@TypeOf(p.create), &lib, "nsp_pipeline_create");
        p.finishFn = try u.symbol(@TypeOf(p.finishFn), &lib, "nsp_pipeline_finish");
        p.destroy = try u.symbol(@TypeOf(p.destroy), &lib, "nsp_pipeline_destroy");
        p.enableFn = try u.symbol(@TypeOf(p.enableFn), &lib, "nsp_pipeline_telemetry_enable");
        p.tap = try u.symbol(@TypeOf(p.tap), &lib, "nsp_pipeline_tap_read");
        p.objects = try u.symbol(@TypeOf(p.objects), &lib, "nsp_pipeline_object_tap_read");
        p.submit = try u.symbol(@TypeOf(p.submit), &lib, "nsp_pipeline_submit");
        const z = try a.dupeZ(u8, path);
        defer a.free(z);
        p.handle = p.create(480, capacity, 0, 0, z.ptr);
        if (p.handle == null) return error.PipelineCreate;
        p.lib = lib;
        return p;
    }
    pub fn enable(self: *Pipeline, value: bool) !void {
        if (self.enableFn(self.handle, @intFromBool(value)) != 0) return error.TelemetryEnable;
    }
    pub fn finish(self: *Pipeline) !std.json.Parsed(u.Value) {
        const size = self.finishFn(self.handle, null, 0);
        if (size == 0 or size > 1024 * 1024) return error.PipelineFinish;
        const bytes = try a.alloc(u8, size);
        defer a.free(bytes);
        if (self.finishFn(self.handle, bytes.ptr, size) != size) return error.PipelineFinish;
        return u.parse(std.mem.sliceTo(bytes, 0));
    }
    pub fn close(self: *Pipeline) void {
        self.destroy(self.handle);
        self.handle = null;
        self.lib.close();
    }
};
pub const Receiver = struct {
    send: std.DynLib,
    lib: std.DynLib,
    link: ?*anyopaque,
    handle: ?*anyopaque,
    closeLink: *const fn (?*anyopaque) callconv(.C) void,
    finishFn: *const fn (?*anyopaque, ?[*]u8, usize) callconv(.C) usize,
    destroy: *const fn (?*anyopaque) callconv(.C) c_int,
    received: *const fn (?*anyopaque) callconv(.C) u64,
    failed: *const fn (?*anyopaque) callconv(.C) c_int,
    mapping: []const u8,
    event: []const u8,
    pub fn init(root: []const u8, pid: u32, pipelines: *[4]Pipeline, fullTiming: bool) !Receiver {
        const sendPath = try u.join(&.{ root, "build/native-send/nsp_native_send.dll" });
        defer a.free(sendPath);
        const receiverPath = try u.join(&.{ root, "build/native-receiver-routing/release/nsp_native_receiver.dll" });
        defer a.free(receiverPath);
        var send = try u.library(sendPath);
        errdefer send.close();
        var lib = try u.library(receiverPath);
        errdefer lib.close();
        var self: Receiver = undefined;
        const open = try u.symbol(*const fn ([*:0]const u16, [*:0]const u16, c_int) callconv(.C) ?*anyopaque, &send, "nsp_link_open");
        self.closeLink = try u.symbol(@TypeOf(self.closeLink), &send, "nsp_link_close");
        self.finishFn = try u.symbol(@TypeOf(self.finishFn), &lib, "nsp_receiver_finish");
        self.destroy = try u.symbol(@TypeOf(self.destroy), &lib, "nsp_receiver_destroy");
        self.received = try u.symbol(@TypeOf(self.received), &lib, "nsp_receiver_received");
        self.failed = try u.symbol(@TypeOf(self.failed), &lib, "nsp_receiver_failed");
        const start = try u.symbol(*const fn ([*:0]const u16, [*:0]const u16, [*]?*anyopaque, u32, *const anyopaque, u32, u32, u32) callconv(.C) ?*anyopaque, &lib, if (fullTiming) "nsp_receiver_start_priority_full" else "nsp_receiver_start_priority");
        const submit = if (fullTiming) try u.symbol(*const anyopaque, &pipelines[0].lib, "nsp_pipeline_submit_timed") else @as(*const anyopaque, @ptrCast(pipelines[0].submit));
        var ownSession: u32 = 0;
        var targetSession: u32 = 0;
        if (c.ProcessIdToSessionId(c.GetCurrentProcessId(), &ownSession) == 0 or c.ProcessIdToSessionId(pid, &targetSession) == 0) return error.ProcessSession;
        const prefix = if (ownSession == targetSession) "Local" else "Global";
        var nonce: [16]u8 = undefined;
        std.crypto.random.bytes(&nonce);
        self.mapping = try u.fmt("{s}\\NSP-{s}", .{ prefix, std.fmt.fmtSliceHexLower(&nonce) });
        self.event = try u.fmt("{s}\\NSP-event-{s}", .{ prefix, std.fmt.fmtSliceHexLower(&nonce) });
        const name = try u.wide(self.mapping);
        defer a.free(name);
        const event = try u.wide(self.event);
        defer a.free(event);
        self.link = open(name.ptr, event.ptr, 1);
        if (self.link == null) return error.MappingCreate;
        errdefer self.closeLink(self.link);
        var handles: [4]?*anyopaque = undefined;
        for (pipelines, 0..) |p, i| handles[i] = p.handle;
        self.handle = start(name.ptr, event.ptr, &handles, 4, submit, 480, if (fullTiming) 3 else 2, 500);
        if (self.handle == null) return error.ReceiverStart;
        self.send = send;
        self.lib = lib;
        return self;
    }
    pub fn finish(self: *Receiver) !std.json.Parsed(u.Value) {
        const size = self.finishFn(self.handle, null, 0);
        if (size == 0 or size > 1024 * 1024) return error.ReceiverStillRunning;
        const bytes = try a.alloc(u8, size);
        defer a.free(bytes);
        if (self.finishFn(self.handle, bytes.ptr, size) != size) return error.ReceiverReport;
        return u.parse(std.mem.sliceTo(bytes, 0));
    }
    pub fn close(self: *Receiver) !void {
        if (self.destroy(self.handle) != 0) return error.ReceiverStillRunning;
        self.closeLink(self.link);
        self.lib.close();
        self.send.close();
        self.handle = null;
    }
};
