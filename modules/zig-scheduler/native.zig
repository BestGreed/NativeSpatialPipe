const std = @import("std");
const u = @import("common.zig");
const a = u.a;
const c = u.c;
pub const default_render_bridge = "build/nsp-product/runtime/release/nsp_croksor.dll";
pub const Hook = struct {
    root: []const u8,
    pid: u32,
    config: c.HookConfig,
    path: []const u8,
    pub fn init(root: []const u8, out: []const u8, pid: u32, owner: u32, created: u64, fixture: bool, build: ?*const u.builds.Profile) !Hook {
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
        if (build) |profile| {
            self.config.target_rva = profile.target_rva;
            self.config.target_size = profile.target_size;
            self.config.vt1_rva = profile.vt1_rva;
            self.config.vt2_rva = profile.vt2_rva;
            @memcpy(self.config.prologue[0..], profile.prologue[0..]);
            @memcpy(self.config.profile_sha256[0..], profile.sha256[0..]);
        }
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
    finished: bool,
    lib: std.DynLib,
    dll_path: []const u8,
    handle: ?*anyopaque,
    progressFn:?*const fn(?*anyopaque) callconv(.C) u64=null,
    create: *const fn (u32, u32, u32, u32, [*:0]const u8) callconv(.C) ?*anyopaque,
    finishFn: *const fn (?*anyopaque, ?[*]u8, usize) callconv(.C) usize,
    destroy: *const fn (?*anyopaque) callconv(.C) void,
    enableFn: *const fn (?*anyopaque, u32) callconv(.C) c_int,
    tap: *const fn (?*anyopaque, [*]u8, usize) callconv(.C) usize,
    objects: *const fn (?*anyopaque, [*]u8, usize) callconv(.C) usize,
    submit: *const fn (?*anyopaque, u64, u32, [*]const u8, usize) callconv(.C) c_int,
    pub fn init(dll: []const u8, path: []const u8, capacity: u32) !Pipeline {
        return initMapping(dll, path, capacity, false, 1.0);
    }
    /// ROAR/OLR-only entry. Explicit radius mode requires the additive DLL entry; never silently
    /// fall back to direction when an older library is selected.
    pub fn initMapping(dll: []const u8, path: []const u8, capacity: u32, scaled: bool, reference: f64) !Pipeline {
        return initBackend(dll,path,capacity,scaled,reference,null);
    }
    pub fn initBackend(dll: []const u8, path: []const u8, capacity: u32, scaled: bool, reference: f64, lira_dll: ?[]const u8) !Pipeline {
        return initLayout(dll,path,capacity,scaled,reference,lira_dll,714);
    }
    pub fn initLayout(dll:[]const u8,path:[]const u8,capacity:u32,scaled:bool,reference:f64,lira_dll:?[]const u8,code:u32) !Pipeline {
        return initOptions(dll,path,capacity,scaled,reference,lira_dll,code,null,null);
    }
    pub fn initLive(dll:[]const u8,path:[]const u8,capacity:u32,scaled:bool,reference:f64,lira_dll:?[]const u8,code:u32,record:bool) !Pipeline {
        return initOptions(dll,path,capacity,scaled,reference,lira_dll,code,record,null);
    }
    pub fn initNotebookLive(dll:[]const u8,path:[]const u8,capacity:u32,scaled:bool,reference:f64,filter_file:[]const u8,record:bool,bass:?@import("desktop_playback.zig").NotebookBass,lira_dll:?[]const u8) !Pipeline {
        return initOptionsBass(dll,path,capacity,scaled,reference,lira_dll,200,record,filter_file,bass,false,null,.standard);
    }
    pub fn initUnified(dll: []const u8, path: []const u8, scaled: bool, reference: f64, config: @import("desktop_playback.zig").Config) !Pipeline {
        return initOptionsBass(dll, path, 64, scaled, reference, null, (try config.preset()).code, config.recording.pcm, config.filterFile(), config.virtualBass(), true, config.wasapiUpmix, config.unifiedSlotProfile);
    }
    pub fn input(self: *Pipeline, lane: u32) !?*anyopaque {
        const get = try u.symbol(*const fn (?*anyopaque, u32) callconv(.C) ?*anyopaque, &self.lib, "nsp_pipeline_input");
        return get(self.handle, lane) orelse error.UnifiedInputUnavailable;
    }
    pub fn unifiedStats(self: *Pipeline, buffer: []u8) !std.json.Parsed(u.Value) {
        const query = try u.symbol(*const fn (?*anyopaque, [*]u8, usize) callconv(.C) usize, &self.lib, "nsp_pipeline_unified_stats");
        const len = query(self.handle, buffer.ptr, buffer.len);
        if (len == 0 or len > buffer.len) return error.UnifiedStatsUnavailable;
        return u.parse(buffer[0..len]);
    }
    pub fn progress(self:*Pipeline) u64 {return if(self.progressFn) |query| query(self.handle) else 0;}
    fn initOptions(dll:[]const u8,path:[]const u8,capacity:u32,scaled:bool,reference:f64,lira_dll:?[]const u8,code:u32,record:?bool,notebook:?[]const u8) !Pipeline {
        return initOptionsBass(dll,path,capacity,scaled,reference,lira_dll,code,record,notebook,null,false,null,.standard);
    }
    fn initOptionsBass(dll:[]const u8,path:[]const u8,capacity:u32,scaled:bool,reference:f64,lira_dll:?[]const u8,code:u32,record:?bool,notebook:?[]const u8,bass:?@import("desktop_playback.zig").NotebookBass,unified:bool,upmix:?@import("desktop_playback.zig").Upmix,slot_profile:@import("desktop_playback.zig").UnifiedSlotProfile) !Pipeline {
        // scaled/reference belong to ROAR's OLR adapter, not shared input or LIRA.
        if (lira_dll == null) {
            if (!std.math.isFinite(reference) or reference <= 0) return error.InvalidPositionReference;
            if (!scaled and reference != 1.0) return error.ReferenceRequiresScaledMode;
        }
        var lib = try u.library(dll);
        errdefer |err| std.debug.print("Render bridge initialization failed: {s}: {s}\n", .{ dll, @errorName(err) });
        errdefer lib.close();
        var p: Pipeline = undefined;
        p.finished = false;
        p.create = try u.symbol(@TypeOf(p.create), &lib, "nsp_pipeline_create");
        p.finishFn = try u.symbol(@TypeOf(p.finishFn), &lib, "nsp_pipeline_finish");
        p.destroy = try u.symbol(@TypeOf(p.destroy), &lib, "nsp_pipeline_destroy");
        p.enableFn = try u.symbol(@TypeOf(p.enableFn), &lib, "nsp_pipeline_telemetry_enable");
        p.tap = try u.symbol(@TypeOf(p.tap), &lib, "nsp_pipeline_tap_read");
        p.objects = try u.symbol(@TypeOf(p.objects), &lib, "nsp_pipeline_object_tap_read");
        p.submit = try u.symbol(@TypeOf(p.submit), &lib, "nsp_pipeline_submit");
        const z = try a.dupeZ(u8, path);
        defer a.free(z);
        p.progressFn=null;
        if (unified) {
            const UpmixAbi = extern struct { dll: ?[*:0]const u8, cpu: ?[*:0]const u8, model: ?[*:0]const u8, fs: ?[*:0]const u8 };
            const Create = *const fn (u32, u32, f64, ?[*:0]const u8, [*:0]const u8, u32, u32, f64, f64, u32, ?*const UpmixAbi) callconv(.C) ?*anyopaque;

            var upmix_abi: ?UpmixAbi = null;
            var paths = [_]?[:0]u8{null} ** 4;
            defer for (paths) |entry| { if (entry) |value| a.free(value); };
            if (upmix) |cfg| if (cfg.enabled) {
                for ([_]?[]const u8{cfg.dllPath, cfg.cpuFallbackDll, cfg.modelPath, cfg.freeSurroundDll}, &paths) |src, *dest| {
                    if (src) |value| dest.* = try a.dupeZ(u8, value);
                }
                upmix_abi = .{ .dll=paths[0].?.ptr, .cpu=if (paths[1]) |value| value.ptr else null, .model=paths[2].?.ptr, .fs=paths[3].?.ptr };
            };
            const filter = if (notebook) |file| try a.dupeZ(u8, file) else null;
            defer if (filter) |value| a.free(value);
            const settings = bass orelse @import("desktop_playback.zig").NotebookBass{};
            p.progressFn = try u.symbol(@TypeOf(p.progressFn.?), &lib, "nsp_pipeline_progress");
            const filter_ptr = if (filter) |value| value.ptr else null;
            const upmix_ptr = if (upmix_abi) |*value| value else null;
            if (slot_profile == .standard) {
                const create = try u.symbol(Create, &lib, "nsp_pipeline_create_unified_v2");
                p.handle = create(capacity, @intFromBool(scaled), reference, filter_ptr, z.ptr, @intFromBool(record orelse false),
                    @intFromBool(settings.enabled), settings.amount, settings.highpassHz, code, upmix_ptr);
            } else {
                const CreateProfile = *const fn (u32, u32, f64, ?[*:0]const u8, [*:0]const u8, u32, u32, f64, f64, u32, ?*const UpmixAbi, u32) callconv(.C) ?*anyopaque;
                const create = u.symbol(CreateProfile, &lib, "nsp_pipeline_create_unified_v3") catch return error.CompactSlotsUnavailable;
                p.handle = create(capacity, @intFromBool(scaled), reference, filter_ptr, z.ptr, @intFromBool(record orelse false),
                    @intFromBool(settings.enabled), settings.amount, settings.highpassHz, code, upmix_ptr, @intFromEnum(slot_profile));
            }
        } else if(notebook) |filter_file| {
            const Create = *const fn(u32,u32,u32,f64,?[*:0]const u8,[*:0]const u8,[*:0]const u8,u32,u32,f64,f64) callconv(.C) ?*anyopaque;
            const create_lsac = try u.symbol(Create, &lib, "nsp_pipeline_create_lsac_live");
            p.progressFn = try u.symbol(@TypeOf(p.progressFn.?), &lib, "nsp_pipeline_progress");
            const filter = try a.dupeZ(u8, filter_file);
            defer a.free(filter);
            const renderer = if(lira_dll) |value| try a.dupeZ(u8,value) else null;
            defer if(renderer) |value| a.free(value);
            const settings = bass orelse @import("desktop_playback.zig").NotebookBass{};
            p.handle = create_lsac(480,capacity,@intFromBool(scaled),reference,if(renderer) |value| value.ptr else null,filter.ptr,z.ptr,
                @intFromBool(record orelse false),@intFromBool(settings.enabled),settings.amount,settings.highpassHz);
        } else if(record) |enabled| {
            const CreateLive=*const fn(u32,u32,u32,u32,f64,?[*:0]const u8,[*:0]const u8,u32) callconv(.C) ?*anyopaque;
            const create_live=try u.symbol(CreateLive,&lib,"nsp_pipeline_create_live");
            p.progressFn=try u.symbol(@TypeOf(p.progressFn.?),&lib,"nsp_pipeline_progress");
            const renderer_z=if(lira_dll) |value| try a.dupeZ(u8,value) else null;
            defer if(renderer_z) |value| a.free(value);
            p.handle=create_live(480,capacity,code,@intFromBool(scaled),reference,if(renderer_z) |value| value.ptr else null,z.ptr,@intFromBool(enabled));
        } else if(code!=714) {
            const CreateLayout=*const fn(u32,u32,u32,u32,f64,?[*:0]const u8,[*:0]const u8) callconv(.C) ?*anyopaque;
            const create_layout=try u.symbol(CreateLayout,&lib,"nsp_pipeline_create_layout");
            const renderer_z=if(lira_dll) |value| try a.dupeZ(u8,value) else null;
            defer if(renderer_z) |value| a.free(value);
            p.handle=create_layout(480,capacity,code,@intFromBool(scaled),reference,if(renderer_z) |value| value.ptr else null,z.ptr);
        } else if (lira_dll) |renderer_dll| {
            const CreateSpatial = *const fn(u32,u32,[*:0]const u8,[*:0]const u8) callconv(.C) ?*anyopaque;
            const create_spatial = try u.symbol(CreateSpatial,&lib,"nsp_pipeline_create_spatial");
            const renderer_z = try a.dupeZ(u8,renderer_dll);
            defer a.free(renderer_z);
            p.handle = create_spatial(480,capacity,renderer_z.ptr,z.ptr);
        } else if (scaled) {
            const CreateMapping = *const fn (u32, u32, u32, u32, u32, f64, [*:0]const u8) callconv(.C) ?*anyopaque;
            const create_mapping = try u.symbol(CreateMapping, &lib, "nsp_pipeline_create_with_position_mapping");
            p.handle = create_mapping(480, capacity, 0, 0, 1, reference, z.ptr);
        } else {
            p.handle = p.create(480, capacity, 0, 0, z.ptr);
        }
        if (p.handle == null) return error.PipelineCreate;
        errdefer p.destroy(p.handle);
        p.lib = lib;
        p.dll_path = try a.dupe(u8, dll);
        return p;
    }
    pub fn enable(self: *Pipeline, value: bool) !void {
        if (self.enableFn(self.handle, @intFromBool(value)) != 0) return error.TelemetryEnable;
    }
    pub fn bindOutput(self: *Pipeline, stream: *@import("output_stream.zig").Stream) !void {
        const pool = @import("output_stream.zig");
        const Publish = *const @TypeOf(pool.publishPool);
        const Config = @import("output_processing.zig").NativeConfig;
        const Bind = *const fn (?*anyopaque, *const Config, Publish, ?*anyopaque) callconv(.C) c_int;
        const bind = try u.symbol(Bind, &self.lib, "nsp_pipeline_set_native_pool_output");
        const config = Config.from(stream.processing_config orelse return error.OutputNotConfigured);
        try stream.holdLibrary(self.dll_path);
        if (bind(self.handle, &config, pool.publishPool, stream) != 0) return error.OutputPoolBind;
    }
    pub fn bindAoip(self:*Pipeline, processing:@import("output_processing.zig").Config, json:[]const u8) !void {
        const Config=@import("output_processing.zig").NativeConfig;
        const network=try @import("aoip_config.zig").parse(a,json);
        const Bind=*const fn(?*anyopaque,*const Config,*const @import("aoip_config.zig").Native) callconv(.C) c_int;
        const bind=try u.symbol(Bind,&self.lib,"nsp_pipeline_set_aoip_output");
        const config=Config.from(processing);
        if(bind(self.handle,&config,&network)!=0)return error.AoipOutputBind;
    }
    pub fn aoipFault(self:*Pipeline) !u32 {
        const Fault=*const fn(?*anyopaque) callconv(.C) u32;
        const fault=try u.symbol(Fault,&self.lib,"nsp_pipeline_aoip_fault");
        return fault(self.handle);
    }
    pub fn bindAoipRoute(self:*Pipeline,processing:@import("output_processing.zig").Config,route:?*anyopaque,source:u32) !void{
        const Config=@import("output_processing.zig").NativeConfig;
        const Bind=*const fn(?*anyopaque,*const Config,?*anyopaque,u32) callconv(.C) c_int;
        const bind=try u.symbol(Bind,&self.lib,"nsp_pipeline_set_aoip_route");
        const config=Config.from(processing);
        if(bind(self.handle,&config,route,source)!=0)return error.OutputRouteBind;
    }
    pub fn finish(self: *Pipeline) !std.json.Parsed(u.Value) {
        const size = self.finishFn(self.handle, null, 0);
        if (size == 0 or size > 1024 * 1024) return error.PipelineFinish;
        self.finished = true; // native finish has joined renderer and sink/writer
        const bytes = try a.alloc(u8, size);
        defer a.free(bytes);
        if (self.finishFn(self.handle, bytes.ptr, size) != size) return error.PipelineFinish;
        return u.parse(std.mem.sliceTo(bytes, 0));
    }
    pub fn close(self: *Pipeline) void {
        self.destroy(self.handle);
        self.handle = null;
        self.lib.close();
        a.free(self.dll_path);
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
    pub fn init(root: []const u8, pid: u32, pipelines: []Pipeline, fullTiming: bool, unified: bool, autoPrimary: bool, rawStreamStats: bool) !Receiver {
        const sendPath = try u.join(&.{ root, "build/native-send/nsp_native_send.dll" });
        defer a.free(sendPath);
        const receiverPath = try u.join(&.{ root, "build/nsp-product/receiver/release/nsp_native_receiver.dll" });
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
        const start = try u.symbol(*const fn ([*:0]const u16, [*:0]const u16, [*]?*anyopaque, u32, *const anyopaque, u32, u32, u32) callconv(.C) ?*anyopaque, &lib, if (unified) "nsp_receiver_start_priority_leased" else if (fullTiming) "nsp_receiver_start_priority_full" else "nsp_receiver_start_priority");
        const submit = if (unified)
            try u.symbol(*const anyopaque, &pipelines[0].lib, "nsp_pipeline_submit_input_leased")
        else if (fullTiming)
            try u.symbol(*const anyopaque, &pipelines[0].lib, "nsp_pipeline_submit_timed")
        else
            @as(*const anyopaque, @ptrCast(pipelines[0].submit));
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
        var handles: [8]?*anyopaque = undefined;
        const route_count: usize = if (unified) 8 else 4;
        for (handles[0..route_count], 0..) |*handle, i| handle.* = if (unified) try pipelines[0].input(@intCast(i)) else pipelines[i].handle;
        self.handle = start(name.ptr, event.ptr, &handles, @intCast(route_count), submit, 480, (if (fullTiming) @as(u32, 3) else 2) | (if (unified) @as(u32, 4) else 0) | (if (autoPrimary) @as(u32, 8) else 0) | (if (rawStreamStats) @as(u32, 16) else 0), 500);
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
    pub fn routeStatus(self: *Receiver, buffer: []u8) !std.json.Parsed(u.Value) {
        const Query = *const fn (?*anyopaque, [*]u8, usize) callconv(.C) usize;
        const query = try u.symbol(Query, &self.lib, "nsp_receiver_route_status");
        const size = query(self.handle, buffer.ptr, buffer.len);
        if (size == 0 or size > buffer.len) return error.ReceiverStatusUnavailable;
        return u.parse(buffer[0..size]);
    }
    pub fn close(self: *Receiver) !void {
        if (self.destroy(self.handle) != 0) return error.ReceiverStillRunning;
        self.closeLink(self.link);
        self.lib.close();
        self.send.close();
        self.handle = null;
    }
};
