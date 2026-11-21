const std = @import("std");
const u = @import("common.zig");
const n = @import("native.zig");
const routing = @import("routing.zig");
const primary_election = @import("primary_election.zig");
const playback = @import("output_playback.zig");
const asio = @import("asio_control.zig");
const a = u.a;
const c = u.c;
const Telemetry = struct {
    process: u.Process,
    control: std.net.Stream,
    reader: std.io.BufferedReader(4096, std.net.Stream.Reader),
    secret: [32]u8,
    udp: std.net.Address,
    healthy: bool = true,
    info: []u8,
    pub fn start(root: []const u8, python: []const u8, port: []const u8, origin: []const u8, output_layout:[]const u8, observed: []const u8) !Telemetry {
        var listener = try (try std.net.Address.parseIp4("127.0.0.1", 0)).listen(.{ .force_nonblocking = true });
        defer listener.deinit();
        var secret: [32]u8 = undefined;
        std.crypto.random.bytes(&secret);
        const secretHex = try u.fmt("{s}", .{std.fmt.fmtSliceHexLower(&secret)});
        defer a.free(secretHex);
        const controlPort = try u.fmt("{d}", .{listener.listen_address.getPort()});
        defer a.free(controlPort);
        const script = try u.join(&.{ root, "modules/telemetry/standalone.py" });
        defer a.free(script);
        var process = try u.spawn(&.{ python, script, "--control-port", controlPort, "--secret", secretHex, "--http-port", port, "--origin", origin, "--output-layout",output_layout, "--observed-dir", observed });
        errdefer process.close();
        const deadline = c.GetTickCount64() + 10000;
        var stream: ?std.net.Stream = null;
        while (c.GetTickCount64() < deadline) {
            const connection = listener.accept() catch |err| {
                if (err != error.WouldBlock) return err;
                if (try u.done(&process) != null) return error.TelemetryExited;
                c.Sleep(10);
                continue;
            };
            stream = connection.stream;
            break;
        }
        const control = stream orelse return error.TelemetryTimeout;
        errdefer control.close();
        timeout(control, 10000);
        const auth = try u.line(control, 4096);
        defer a.free(auth);
        var parsed = try u.parse(auth);
        defer parsed.deinit();
        if (!std.mem.eql(u8, try u.string(u.field(parsed.value, "secret")), secretHex)) return error.TelemetryAuthentication;
        const info = try u.line(control, 4096);
        defer a.free(info);
        var details = try u.parse(info);
        defer details.deinit();
        if (u.field(details.value, "error") != .null) return error.TelemetryStart;
        const address = u.field(details.value, "udp");
        if (address != .array or address.array.items.len != 2) return error.TelemetryAddress;
        const udp = try std.net.Address.parseIp4(try u.string(address.array.items[0]), @intCast(try u.number(address.array.items[1])));
        timeout(control, 2000);
        return .{ .process = process, .control = control, .reader = std.io.bufferedReader(control.reader()), .secret = secret, .udp = udp, .info = try a.dupe(u8, info) };
    }
    fn timeout(stream: std.net.Stream, ms: u32) void {
        _ = c.setsockopt(@intFromPtr(stream.handle), c.SOL_SOCKET, c.SO_RCVTIMEO, @ptrCast(&ms), 4);
        _ = c.setsockopt(@intFromPtr(stream.handle), c.SOL_SOCKET, c.SO_SNDTIMEO, @ptrCast(&ms), 4);
    }
    pub fn query(self: *Telemetry, command: []const u8) ![]u8 {
        if (!self.healthy) return error.TelemetryUnavailable;
        errdefer self.healthy = false;
        const msg = try u.json(command);
        defer a.free(msg);
        try self.control.writeAll(msg);
        try self.control.writeAll("\n");
        return self.reader.reader().readUntilDelimiterAlloc(a, '\n', 8 * 1024 * 1024);
    }
    pub fn close(self: *Telemetry) void {
        const result = self.query("stop") catch null;
        if (result) |bytes| a.free(bytes);
        self.control.close();
        _ = c.WaitForSingleObject(self.process.handle, 3000);
        self.process.close();
        a.free(self.info);
    }
};
const Recorder = struct {
    telemetry: *Telemetry,
    out: []const u8,
    stop: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    failure: ?[]const u8 = null,
    pub fn run(self: *Recorder) void {
        self.loop() catch |err| {
            self.failure = @errorName(err);
        };
    }
    fn headerNumber(bytes: []const u8, key: []const u8) ?u64 {
        const start = (std.mem.indexOf(u8, bytes[0..@min(bytes.len, 2048)], key) orelse return null) + key.len;
        var end = start;
        while (end < bytes.len and std.ascii.isDigit(bytes[end])) : (end += 1) {}
        return std.fmt.parseInt(u64, bytes[start..end], 10) catch null;
    }
    fn loop(self: *Recorder) !void {
        const path = try u.join(&.{ self.out, "telemetry-snapshots.jsonl" });
        defer a.free(path);
        const file = try std.fs.cwd().createFile(path, .{ .exclusive = true });
        defer file.close();
        var lastEpoch: ?u64 = null;
        var lastSeq: ?u64 = null;
        while (!self.stop.load(.acquire)) {
            const bytes = try self.telemetry.query("snapshot");
            defer a.free(bytes);
            const epoch = headerNumber(bytes, "\"streamEpoch\": ");
            const seq = headerNumber(bytes, "\"telemetrySeq\": ");
            if (epoch != null and seq != null and (epoch != lastEpoch or seq != lastSeq)) {
                try file.writeAll(bytes);
                try file.writeAll("\n");
                lastEpoch = epoch;
                lastSeq = seq;
            }
            c.Sleep(100);
        }
    }
};
const Bridge = struct {
    pipelines: []n.Pipeline,
    receiver: *n.Receiver,
    telemetry: *Telemetry,
    out: []const u8,
    stop: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    routes: routing.Routes = .{},
    selected: ?usize = null,
    audioRoute:?*@import("output_route.zig").Route=null,
    audioAutomatic:bool=false,
    unified: bool = false,
    unified_slot_profile: ?[]const u8 = null,
    unified_primary_policy: ?[]const u8 = null,
    renderer_layout: []const u8,
    device_layout: []const u8,
    lsac_enabled: bool,
    audioSelected:?usize=null,
    routeRevision:u64=0,
    routeError:?[]const u8=null,
    epoch: u64 = 0,
    attached: bool = false,
    forwarded: u64 = 0,
    dropped: u64 = 0,
    frame: u64 = 0,
    failure: ?[]const u8 = null,
    socket: c.SOCKET = c.INVALID_SOCKET,
    packet: [32 + 160 * 1968 + 64]u8 = undefined,
    objectBuffer: [32 + 160 * 1968]u8 = undefined,
    pub fn run(self: *Bridge) void {
        self.loop() catch |err| {
            self.failure = @errorName(err);
        };
    }
    fn loop(self: *Bridge) !void {
        self.socket = c.socket(c.AF_INET, c.SOCK_DGRAM, c.IPPROTO_UDP);
        if (self.socket == c.INVALID_SOCKET) return error.UdpSocket;
        defer {
            _ = c.closesocket(self.socket);
            self.socket = c.INVALID_SOCKET;
        }
        var nonblock: c_ulong = 1;
        _ = c.ioctlsocket(self.socket, @as(c_long, @bitCast(@as(u32, 0x8004667e))), &nonblock);
        const sendBuffer: c_int = 1024 * 1024;
        _ = c.setsockopt(self.socket, c.SOL_SOCKET, c.SO_SNDBUF, @ptrCast(&sendBuffer), 4);
        var nextPoll: u64 = 0;
        var nextReport: u64 = 0;
        while (!self.stop.load(.acquire)) {
            const now = c.GetTickCount64();
            if (now >= nextPoll) {
                nextPoll = now + 100;
                var sizes: [4]?u64 = .{ null, null, null, null };
                for(self.pipelines,0..) |*pipeline,i| sizes[i]=pipeline.progress();
                const next = self.routes.update(now, sizes);
                if(if(self.unified) null else self.audioRoute) |route| {
                    const path=try u.join(&.{self.out,"output-route-request.json"});defer a.free(path);
                    if(u.exists(path)){
                        var request=try u.load(path);defer request.deinit();
                        const revision=try u.number(u.field(request.value,"revision"));
                        if(revision>self.routeRevision){
                            const policy=try u.string(u.field(request.value,"sourcePolicy"));
                            self.audioAutomatic=std.mem.eql(u8,policy,"active");
                            const source=u.field(request.value,"sourceStream");
                            const target=if(self.audioAutomatic) next else if(source==.null) null else @as(?usize,@intCast(try u.number(source)));
                            if(route.select(target)) |_| {self.audioSelected=target;self.routeRevision=revision;self.routeError=null;}
                            else |err| {self.routeError=@errorName(err);}
                            nextReport=0;
                        }
                    }
                    if(self.audioAutomatic and self.audioSelected!=next){
                        if(route.select(next)) |_| {self.audioSelected=next;self.routeError=null;nextReport=0;}
                        else |err| {self.routeError=@errorName(err);}
                    }
                }
                if (next != self.selected) {
                    if (self.selected) |i| try self.pipelines[i].enable(false);
                    if (next) |i| {
                        try self.pipelines[i].enable(false);
                        try self.pipelines[i].enable(true);
                        self.epoch = if (self.attached) self.epoch + 2 else 1;
                        self.attached = true;
                    }
                    self.selected = next;
                    nextReport = 0;
                }
                if (now >= nextReport) {
                    self.report(now) catch {};
                    nextReport = now + 1000;
                }
            }
            if (self.selected) |i| try self.pump(&self.pipelines[i]);
            c.Sleep(5);
        }
    }
    fn report(self: *Bridge, now: u64) !void {
        const Row = struct { pipeline: usize, outputBytes: ?u64, ageSeconds: ?f64, progressUpdates: u64, @"error": ?[]const u8 };
        var rows: [4]Row = undefined;
        for (self.routes.rows[0..self.pipelines.len], 0..) |row, i| rows[i] = .{ .pipeline = i, .outputBytes = row.size, .ageSeconds = if (row.last) |v| @as(f64, @floatFromInt(now - v)) / 1000 else null, .progressUpdates = row.updates, .@"error" = if (row.failed) "read-error" else null };
        const path = try u.join(&.{ self.out, "telemetry-routing.json" });
        defer a.free(path);
        var buffer: [4096]u8 = undefined;
        var ingress = if (self.unified) self.pipelines[0].unifiedStats(&buffer) catch null else null;
        defer if (ingress) |*parsed| parsed.deinit();
        var receiver_buffer: [2048]u8 = undefined;
        var receiver_status = self.receiver.routeStatus(&receiver_buffer) catch null;
        defer if (receiver_status) |*parsed| parsed.deinit();
        try u.writeJson(path, .{ .policy = "latest-active-render-output", .progressSource="renderer-memory-counter", .pollSeconds = @as(f64, 0.1), .staleAfterSeconds = @as(f64, 1), .selectedPipeline = self.selected, .selectionChanges = self.routes.changes, .routes = rows[0..self.pipelines.len],
            .audioSourcePolicy=if(self.unified) "unified" else if(self.audioRoute==null) "external" else if(self.audioAutomatic) "active" else "fixed",.audioSourcePipeline=if(self.unified) @as(?usize,0) else self.audioSelected,.rendererInstances=self.pipelines.len,.unifiedInput=if(ingress) |parsed| parsed.value else @as(u.Value,.null),.routeRevision=self.routeRevision,.routeError=self.routeError,
            .unifiedSlotProfile = self.unified_slot_profile, .unifiedPrimaryPolicy = self.unified_primary_policy,
            .receiverRoutes = if (receiver_status) |parsed| parsed.value else @as(u.Value, .null),
            .rendererLayout=self.renderer_layout,.deviceLayout=self.device_layout,.adapter=if(self.lsac_enabled) "LSAC" else @as(?[]const u8,null) });
    }
    fn send(self: *Bridge, bytes: []const u8) void {
        const result = c.sendto(self.socket, @ptrCast(bytes.ptr), @intCast(bytes.len), 0, @ptrCast(&self.telemetry.udp.any), @intCast(self.telemetry.udp.getOsSockLen()));
        if (result != bytes.len) self.dropped += 1;
    }
    fn pump(self: *Bridge, pipeline: *n.Pipeline) !void {
        const max = 32 + 160 * 1968;
        const packet = &self.packet;
        @memcpy(packet[0..32], &self.telemetry.secret);
        const upmix_meter_bytes = 8 * 16;
        var raw: [32+16*480*4+upmix_meter_bytes]u8 = undefined;
        for (0..16) |_| {
            const size = pipeline.tap(pipeline.handle, &raw, raw.len);
            if (size == 0) break;
            if (size < 32 or size > raw.len or !std.mem.eql(u8,raw[0..4],"NSPT")) return error.RawTapFormat;
            const trailer: usize = switch (std.mem.readInt(u16,raw[4..6],.little)) {
                1 => 0,
                2 => upmix_meter_bytes,
                else => return error.RawTapFormat,
            };
            if (size != 32+@as(usize,std.mem.readInt(u16,raw[6..8],.little))*480*4+trailer) return error.RawTapFormat;
            self.epoch = @max(self.epoch, std.mem.readInt(u64, raw[16..24], .little));
            std.mem.writeInt(u64, raw[16..24], self.epoch, .little);
            @memcpy(packet[32 .. 32 + size], raw[0..size]);
            self.send(packet[0 .. 32 + size]);
            self.forwarded += 1;
        }
        const objects = &self.objectBuffer;
        for (0..16) |_| {
            const size = pipeline.objects(pipeline.handle, objects.ptr, objects.len);
            if (size == 0) break;
            if (size < 32 or size > max) return error.ObjectTapFormat;
            self.epoch = @max(self.epoch, std.mem.readInt(u64, objects[16..24], .little));
            std.mem.writeInt(u64, objects[16..24], self.epoch, .little);
            self.frame += 1;
            const count = (size + 47999) / 48000;
            for (0..count) |i| {
                @memcpy(packet[32..36], "NSPF");
                std.mem.writeInt(u64, packet[36..44], self.frame, .little);
                std.mem.writeInt(u16, packet[44..46], @intCast(i), .little);
                std.mem.writeInt(u16, packet[46..48], @intCast(count), .little);
                const chunk = objects[i * 48000 .. @min(size, (i + 1) * 48000)];
                @memcpy(packet[48 .. 48 + chunk.len], chunk);
                self.send(packet[0 .. 48 + chunk.len]);
            }
        }
    }
};
pub fn run(args: []const []const u8, root: []const u8) !void {
    const out = u.option(args, "--out") orelse return error.OutputRequired;
    const pid = try std.fmt.parseInt(u32, u.option(args, "--pid") orelse return error.PidRequired, 10);
    const owner = try std.fmt.parseInt(u32, u.option(args, "--owner-pid") orelse return error.OwnerRequired, 10);
    const stopFile = u.option(args, "--stop-file") orelse return error.StopFileRequired;
    const fixture = u.flag(args, "--fixture");
    var created: u64 = undefined;
    var target_build: ?*const u.builds.Profile = null;
    if (fixture) {
        created = (try u.processIdentity(pid)) orelse return error.TargetExited;
    } else {
        const target = try u.checkTarget(pid);
        created = target.created;
        target_build = target.build;
    }
    if (try u.processIdentity(owner) == null) return error.OwnerExited;
    if (u.exists(out)) return error.OutputExists;
    try std.fs.cwd().makePath(out);
    // The scheduler owns setup only; all audio submission stays inside native DLLs.
    if (c.SetEnvironmentVariableW((try u.wide("NSP_RENDER_MMCSS")).ptr, (try u.wide("1")).ptr) == 0) return error.RendererMmcssEnvironment;
    const Playback = @import("desktop_playback.zig").Config;
    var desktop: ?std.json.Parsed(Playback) = null;
    defer if(desktop) |*parsed| parsed.deinit();
    if(u.option(args,"--playback-config")) |path| {
        const bytes=try u.read(path);defer a.free(bytes);
        desktop=try @import("desktop_playback.zig").parse(a,bytes);
        if(u.flag(args,"--record-pcm") or u.flag(args,"--archive-telemetry"))return error.DuplicatePlaybackConfiguration;
        for([_][]const u8{"--output-layout","--renderer","--renderer-mode","--render-bridge-dll","--lira-renderer-dll","--spatial-renderer-dll","--asio-driver","--asio-dll","--asio-stream","--asio-channel-order"}) |key| {
            if(u.option(args,key)!=null) return error.DuplicatePlaybackConfiguration;
        }
    }
    const output_layout=if(desktop) |settings| (try settings.value.preset()).id else u.option(args,"--output-layout") orelse "7.1.4";
    const preset=@import("layout_presets.zig").get(output_layout) orelse return error.UnsupportedOutputLayout;
    const output_channels=preset.labels.len;
    const recording=if(desktop) |settings| settings.value.recording else @import("desktop_playback.zig").Recording{.pcm=u.flag(args,"--record-pcm"),.telemetrySnapshots=u.flag(args,"--archive-telemetry")};
    const dll = if(desktop) |settings| try a.dupe(u8,settings.value.bridgeDll) else if (u.option(args,"--render-bridge-dll")) |path| try a.dupe(u8,path) else try u.join(&.{ root, n.default_render_bridge });
    defer a.free(dll);
    if (desktop) |settings| {
        try settings.value.validateSpatialFormat(u.option(args, "--active-spatial-format"));
        try settings.value.validateRuntime();
    }
    const unified = if (desktop) |settings| std.mem.eql(u8, settings.value.sourcePolicy, "unified") else false;
    var pipeline_storage: [4]n.Pipeline = undefined;
    const pipelines = pipeline_storage[0..if (unified) @as(usize, 1) else 4];
    const requested_renderer = if(desktop) |settings| settings.value.renderer else u.option(args,"--renderer") orelse "roar";
    const selection = try @import("desktop_playback.zig").select(
        if (std.mem.eql(u8,requested_renderer,"spatial")) "lira" else requested_renderer,
        if(desktop) |settings| settings.value.mode else u.option(args,"--renderer-mode"));
    const renderer = selection.backend;
    const lira_dll = if(desktop != null and std.mem.eql(u8,renderer,"lira")) desktop.?.value.rendererDll else if (std.mem.eql(u8,renderer,"lira")) u.option(args,"--lira-renderer-dll") orelse u.option(args,"--spatial-renderer-dll") orelse return error.LiraRendererDllRequired else null;
    if (lira_dll != null and (u.option(args,"--position-mode") != null or u.option(args,"--position-reference") != null)) return error.OlrOptionsWithLiraBackend;
    const mapping = if(desktop) |settings| settings.value.positionMapping else null;
    if(mapping!=null and (u.option(args,"--position-mode")!=null or u.option(args,"--position-reference")!=null)) return error.DuplicatePositionConfiguration;
    const position_mode = if(mapping) |value| value.mode else u.option(args, "--position-mode") orelse "scaled";
    if (!std.mem.eql(u8, position_mode, "direction") and !std.mem.eql(u8, position_mode, "scaled")) return error.InvalidPositionMode;
    const radius_scaled = std.mem.eql(u8, position_mode, "scaled");
    const radius_reference = if(mapping) |value| value.reference else if (u.option(args, "--position-reference")) |v| try std.fmt.parseFloat(f64, v) else 1.0;
    if (!std.math.isFinite(radius_reference) or radius_reference <= 0) return error.InvalidPositionReference;
    if (!radius_scaled and radius_reference != 1.0) return error.ReferenceRequiresScaledMode;
    const first = try u.fmt("{s}/stream-0", .{out});
    defer a.free(first);
    try std.fs.cwd().makePath(first);
    const firstPath = try u.join(&.{ first, "roar-714.f32" });
    defer a.free(firstPath);
    const notebook_file=if(desktop) |settings| settings.value.filterFile() else null;
    const notebook_bass=if(desktop) |settings| settings.value.virtualBass() else null;
    pipelines[0] = if (unified) try n.Pipeline.initUnified(dll, firstPath, radius_scaled, radius_reference, desktop.?.value) else if(notebook_file) |file| try n.Pipeline.initNotebookLive(dll,firstPath,128,radius_scaled,radius_reference,file,recording.pcm,notebook_bass,lira_dll) else try n.Pipeline.initLive(dll, firstPath, 64, radius_scaled, radius_reference, lira_dll,preset.code,recording.pcm);
    errdefer if (pipelines[0].handle != null) pipelines[0].close();
    var telemetry = try Telemetry.start(root, u.option(args, "--python") orelse "python.exe", u.option(args, "--telemetry-port") orelse "8766", u.option(args, "--origin") orelse "http://127.0.0.1:8765",if (desktop) |settings| settings.value.deviceLayout() else output_layout,out);
    defer telemetry.close();
    const infoPath = try u.join(&.{ out, "telemetry-service.json" });
    defer a.free(infoPath);
    try u.write(infoPath, telemetry.info);
    for (1..pipelines.len) |i| {
        const directory = try u.fmt("{s}/stream-{d}", .{ out, i });
        defer a.free(directory);
        try std.fs.cwd().makePath(directory);
        const path = try u.join(&.{ directory, "roar-714.f32" });
        defer a.free(path);
        pipelines[i] = if(notebook_file) |file| try n.Pipeline.initNotebookLive(dll,path,32,radius_scaled,radius_reference,file,recording.pcm,notebook_bass,lira_dll) else try n.Pipeline.initLive(dll, path, 8, radius_scaled, radius_reference, lira_dll,preset.code,recording.pcm);
    }
    const pcm_settings=if(desktop) |settings| settings.value.pcmCapture else null;
    var pcm:?@import("pcm_capture.zig").Session=null;
    if(pcm_settings) |settings| {if(settings.enabled) {
        if(@import("pcm_capture.zig").Session.init(settings,desktop.?.value,pid,out,radius_scaled,radius_reference,lira_dll,preset.code,if (unified) &pipelines[0] else null)) |session| {pcm=session;} else |err| {
            const path=try u.join(&.{out,"pcm-capture.json"});defer a.free(path);
            try u.writeJson(path,.{.state="unavailable",.errorName=@errorName(err),.spatialCaptureAction="unchanged"});
            if(settings.routeToOutput)return err;
        }
    }}
    defer if(pcm) |*session| session.deinit();
    // Explicit opt-in, fixed selected source; no automatic switching/merging.
    var audio: ?*playback.Session = null;
    var audio_source: usize = 0;
    var audio_order: []const u8 = "olr";
    var audio_sources=@import("output_layout.zig").identity;
    var audio_physical=audio_sources;
    var audio_labels:[16][]const u8=[_] []const u8{""} ** 16;
    @memcpy(audio_labels[0..output_channels],preset.labels);
    var audio_config: ?asio.Backend.Config = null;
    var capture_may_have_started = false;
    const aoip_path=u.option(args,"--aoip-config");
    const aoip_profile=if(desktop) |settings| (if(std.mem.eql(u8,settings.value.outputBackend,"aoip")) settings.value.aoip else null) else null;
    const aoip_enabled=aoip_path!=null or aoip_profile!=null;
    const wasapi_profile=if(desktop) |settings| (if(std.mem.eql(u8,settings.value.outputBackend,"wasapi")) settings.value.wasapi else null) else null;
    var wasapi:?@import("wasapi_control.zig").Session=null;
    const wasapi_source:usize=if(desktop) |settings| @intCast(settings.value.sourceStream) else 0;
    const wasapi_pipeline=if(pcm_settings!=null and pcm_settings.?.enabled and pcm_settings.?.routeToOutput) &pcm.?.pipeline.? else &pipelines[wasapi_source];
    const wasapi_report=try u.join(&.{out,"wasapi-output.json"});
    defer a.free(wasapi_report);
    if(wasapi_profile) |settings| {
        if(aoip_enabled)return error.DuplicateOutputBackend;
        for([_][]const u8{"--asio-driver","--asio-dll","--asio-stream","--asio-channel-order"}) |key| {
            if(u.option(args,key)!=null)return error.DuplicateOutputBackend;
        }
        if(wasapi_source>=pipelines.len)return error.InvalidWasapiStream;
        wasapi=try @import("wasapi_control.zig").Session.init(settings.dllPath,settings.endpointId,desktop.?.value.drainTimeoutMs,settings.mode,settings.periodFrames);
        wasapi.?.bind(&wasapi_pipeline.lib,wasapi_pipeline.handle,desktop.?.value.processingConfig(),desktop.?.value.loudness()) catch |err| {
            wasapi.?.deinit();wasapi=null;return err;
        };
    }
    defer if(wasapi) |*session| {
        if(!wasapi_pipeline.finished and !capture_may_have_started){
            var unused=wasapi_pipeline.finish() catch null;
            if(unused) |*value| value.deinit();
        }
        // Keep native callbacks and their DLL alive until the Rust writer joins.
        while(!wasapi_pipeline.finished)c.Sleep(1000);
        session.drain() catch {};
        session.report(wasapi_report,wasapi_profile.?.endpointId,@intCast(wasapi_source)) catch {};
        session.deinit();
    };
    defer if(pcm) |*session| session.stopAndFinish();
    var aoip_source:?usize=null;
    var aoip_selected:?usize=null;
    const aoip_automatic = if(desktop) |settings| std.mem.eql(u8,settings.value.sourcePolicy,"active") else false;
    var outputRoute:?@import("output_route.zig").Route=null;
    defer if(outputRoute) |*route| route.deinit();
    if(aoip_enabled) {
        if(aoip_path!=null and aoip_profile!=null)return error.DuplicateOutputBackend;
        for([_][]const u8{"--asio-driver","--asio-dll","--asio-stream","--asio-channel-order"}) |key| {
            if(u.option(args,key)!=null)return error.DuplicateOutputBackend;
        }
        const source=if(u.option(args,"--aoip-stream")) |v| try std.fmt.parseInt(usize,v,10) else if(desktop) |settings| @as(usize,settings.value.sourceStream) else 0;
        if(source>=pipelines.len)return error.InvalidAoipStream;
        const json=if(aoip_path) |path| try u.read(path) else try u.json(aoip_profile.?);defer a.free(json);
        const processing=if(desktop) |settings| settings.value.processingConfig() else @import("output_processing.zig").Config{.channels=@intCast(output_channels)};
        const network=try @import("aoip_config.zig").parse(a,json);
        outputRoute=try @import("output_route.zig").Route.init(dll,network,@intCast(output_channels));
        for(pipelines,0..) |*pipeline,i| try pipeline.bindAoipRoute(processing,outputRoute.?.handle,@intCast(i));
        if(!aoip_automatic) {
            try outputRoute.?.select(source);
            aoip_selected=source;
        }
        aoip_source=0; // all source slots share the same network fault status.
    }
    if (if(aoip_enabled or wasapi_profile!=null) @as(?[]const u8,null) else if(desktop) |settings| @as(?[]const u8,settings.value.driver) else u.option(args, "--asio-driver")) |driver| {
        const device_dll = if(desktop) |settings| settings.value.asioDll else u.option(args, "--asio-dll") orelse return error.AsioDllRequired;
        var config = try asio.Backend.Config.init(device_dll, driver);
        audio_order = if(desktop) |settings| settings.value.channelOrder else u.option(args, "--asio-channel-order") orelse "olr";
        if(desktop) |settings| {
            try settings.value.applyOutput(&config);
            @memcpy(audio_sources[0..output_channels],try settings.value.sources());@memcpy(audio_physical[0..output_channels],try settings.value.physical());audio_labels=try settings.value.labels();
        } else {
            const options=@import("desktop_playback.zig").Config{.bridgeDll=dll,.asioDll=device_dll,.driver=driver,.outputLayout=output_layout,.channelOrder=audio_order};
            try options.applyOutput(&config);
            @memcpy(audio_sources[0..output_channels],try options.sources());audio_labels=try options.labels();
        }
        if(u.flag(args, "--asio-fixed-clock")) config.clock_sync=false;
        if (u.option(args, "--asio-startup-timeout-ms")) |v| config.startup_timeout_ms = try std.fmt.parseInt(u32, v, 10);
        const source = if(desktop) |settings| @as(usize,settings.value.sourceStream) else if (u.option(args, "--asio-stream")) |v| try std.fmt.parseInt(usize, v, 10) else 0;
        if (source >= pipelines.len) return error.InvalidAsioStream;
        audio_source = source;
        audio_config=config;
        audio = try playback.Session.create(a, config);
        pipelines[source].bindOutput(audio.?.stream) catch |err| {
            audio.?.finish(); audio.?.destroy(); audio = null; return err;
        };
    }
    defer if (audio) |session| {
        if (!pipelines[audio_source].finished and !capture_may_have_started) {
            var unused = pipelines[audio_source].finish() catch null;
            if (unused) |*value| value.deinit();
        }
        // A failed upstream teardown cannot free a context still borrowed by
        // its writer. Retain the host/resources until that proof is available.
        while (!pipelines[audio_source].finished) c.Sleep(1000);
        session.finish();
        const status = session.stream.snapshot();
        const path = u.join(&.{ out, "asio-output.json" }) catch null;
        if (path) |p| {
            defer a.free(p);
            u.writeJson(p, .{ .outputLayout=output_layout,.outputChannels=output_channels,.renderer = renderer, .rendererMode = selection.mode, .rendererDelayFrames = @as(u32, if (lira_dll != null) 512 else 0), .state = @tagName(status.state), .receivedFrames = status.received_frames,
                .outputOrder = audio_order,
                .outputChannelLabels = audio_labels[0..output_channels],
                .sourceChannelIndices = audio_sources[0..output_channels],
                .physicalChannelIndices = audio_physical[0..output_channels],
                .outputHandoff = .{.mode="rust-pool-lease",.pcmCopiesBeforeAsioSubmit=@as(u32,0),.poolBlocksReceived=status.pool_blocks_received,.poolBlocksReleased=status.pool_blocks_released,.dspTailFrames=status.dsp_tail_frames},
                .outputProcessing = .{.bypass=audio_config.?.processing.bypass,.effectiveBypass=status.dsp_bypassed,.placement="rust-output-pool",.master=audio_config.?.processing.master,.trim=audio_config.?.processing.trim[0..output_channels],.delayFrames=audio_config.?.processing.delay_frames[0..output_channels],
                    .limiter=.{.enabled=audio_config.?.processing.limiter,.ceiling=audio_config.?.processing.ceiling,.releaseMs=audio_config.?.processing.release_ms,.lookaheadFrames=audio_config.?.processing.lookahead_frames}},
                .playbackPolicy = .{.lifetime="session",.noInput="silence",.clockSync=audio_config.?.clock_sync,.prebufferFrames=audio_config.?.prebuffer_frames,.startupTimeoutMs=@as(u32,0),
                    .rebufferTimeoutMs=@as(u32,0),.maxRebuffers=@as(u32,0),.drainTimeoutMs=audio_config.?.drain_timeout_ms},
                .submittedFrames = status.submitted_frames, .callbacks = status.callbacks,
                .starvationFrames = status.starvation_frames, .rebuffers = status.rebuffers,
                .deviceSampleRate = status.device_sample_rate, .deviceBufferFrames = status.device_buffer_frames,
                .bufferedSourceFrames = status.buffered_source_frames, .sourceFrame = status.source_frame,
                .calibrationMaxFrames = status.calibration_max_frames, .lookaheadFrames = status.lookahead_frames,
                .prebufferFrames = status.prebuffer_frames,
                .rebufferWaitNs = status.rebuffer_wait_ns,
                .errorDetail = std.mem.sliceTo(&session.detail, 0),
                .failure = if (status.failure) |err| @errorName(err) else @as(?[]const u8, null) }) catch {};
        }
        session.destroy();
    };
    const auto_primary = unified and (desktop.?.value.unifiedPrimaryPolicy == .objectCount or
        u.flag(args, "--experimental-auto-primary"));
    var receiver = try n.Receiver.init(root, pid, pipelines, u.flag(args, "--full-timing"), unified, auto_primary, recording.rawStreamStats);
    const snapshot = if (auto_primary) try u.symbol(
        *const fn (?*const anyopaque, u32, *u32, *u64) callconv(.C) c_int,
        &receiver.lib, "nsp_receiver_route_snapshot",
    ) else null;
    const set_primary = if (auto_primary) try u.symbol(
        *const fn (?*anyopaque, i32) callconv(.C) c_int,
        &pipelines[0].lib, "nsp_pipeline_request_primary_lane",
    ) else null;
    var election = primary_election.Election.init(.{});
    var candidates: [8]primary_election.Candidate = undefined;
    var last_capture = [_]u64{0} ** candidates.len;
    for (&candidates, 0..) |*candidate, slot| candidate.* = .{
        .pipeline = slot, .dynamic_objects = 0, .last_progress_ms = 0, .eligible = false,
    };
    // PCM ingress begins only after every output callback has been configured.
    if(pcm) |*session| try session.startIngress();
    var hook = try n.Hook.init(root, out, pid, owner, created, fixture, target_build);
    defer {
        u.remove(hook.path);
        a.free(hook.path);
    }
    if (!fixture) {
        const legacy = try u.checkModules(pid);
        var baseline = try hook.call("baseline");
        defer baseline.deinit();
        if (!u.yes(u.field(baseline.value, "baselineVerified"))) return error.BaselineNotVerified;
        hook.config.legacy_stopped = @intFromBool(legacy);
    }
    const capture = try u.join(&.{ root, "build/capture-scheduler-mmcss/nsp_native_capture.dll" });
    defer a.free(capture);
    const send = try u.join(&.{ root, "build/native-send/nsp_native_send.dll" });
    defer a.free(send);
    try n.Hook.text(&hook.config.capture, capture);
    try n.Hook.text(&hook.config.send, send);
    try n.Hook.text(&hook.config.mapping, receiver.mapping);
    try n.Hook.text(&hook.config.event, receiver.event);
    const marker = try u.join(&.{ out, "hook-session.json" });
    defer a.free(marker);
    try u.writeJson(marker, .{ .pid = pid, .created = created, .ownerPid = owner, .hookGeneration = 2, .scheduler = "zig" });
    var bridge = Bridge{ .pipelines = pipelines, .receiver = &receiver, .telemetry = &telemetry, .out = out, .unified = unified,
        .unified_slot_profile = if (unified) @tagName(desktop.?.value.unifiedSlotProfile) else null,
        .unified_primary_policy = if (unified) (if (auto_primary) "objectCount" else "legacy") else null,
        .renderer_layout=output_layout,.device_layout=if(desktop) |settings| settings.value.deviceLayout() else output_layout,.lsac_enabled=notebook_file!=null,
        .audioRoute=if(outputRoute) |*route| route else null,
        .audioAutomatic=aoip_automatic,
        .audioSelected=aoip_selected };
    var thread = try std.Thread.spawn(.{}, Bridge.run, .{&bridge});
    var recorder = Recorder{ .telemetry = &telemetry, .out = out };
    var recordThread:?std.Thread = if(recording.telemetrySnapshots) try std.Thread.spawn(.{}, Recorder.run, .{&recorder}) else null;
    // Establish cleanup before the first Hook activation attempt, including timeouts.
    capture_may_have_started = true;
    var activation = hook.call("start") catch |err| {
        const failure = u.join(&.{ out, "start-error.json" }) catch null;
        if (failure) |path| {
            defer a.free(path);
            u.writeJson(path, .{ .errorName = @errorName(err) }) catch {};
        }
        if (pcm) |*session| session.stopAndFinish();
        try shutdown(&hook, &receiver, pipelines, &bridge, &thread, &recorder, &recordThread, out);
        return err;
    };
    activation.deinit();
    const readyPath = try u.join(&.{ out, "ready.txt" });
    defer a.free(readyPath);
    u.write(readyPath, "ready") catch |err| {
        if (pcm) |*session| session.stopAndFinish();
        try shutdown(&hook, &receiver, pipelines, &bridge, &thread, &recorder, &recordThread, out);
        return err;
    };
    const deadline = if (u.option(args, "--duration-ms")) |v| c.GetTickCount64() + try std.fmt.parseInt(u64, v, 10) else std.math.maxInt(u64);
    var nextCheck: u64 = 0;
    var reportedAoipFault:u32=0;
    while (c.GetTickCount64() < deadline and !u.exists(stopFile)) {
        if(aoip_source) |source| {
            const fault=try pipelines[source].aoipFault();
            if(fault!=reportedAoipFault){
                const path=try u.join(&.{out,"aoip-output-fault.json"});defer a.free(path);
                try u.writeJson(path,.{.fault=fault,.captureAction="unchanged"});reportedAoipFault=fault;
            }
        }
        if (audio) |session| { if (session.stream.snapshot().failure != null) break; }
        if(wasapi) |*session| {
            session.pollVolume();
            session.report(wasapi_report,wasapi_profile.?.endpointId,@intCast(wasapi_source)) catch {};
            if(session.failed())break;
        }
        if(pcm) |*session| session.report(out,pcm_settings.?) catch {};
        if (receiver.failed(receiver.handle) != 0) break;
        if (snapshot) |read| {
            const now = c.GetTickCount64();
            for (&candidates, 0..) |*candidate, slot| {
                var count: u32 = 0;
                var capture_ns: u64 = 0;
                if (read(receiver.handle, @intCast(slot), &count, &capture_ns) == 1 and capture_ns != last_capture[slot]) {
                    last_capture[slot] = capture_ns;
                    candidate.dynamic_objects = count;
                    candidate.last_progress_ms = now;
                    candidate.eligible = true;
                }
            }
            const decision = election.update(now, &candidates, null);
            if (decision.changed) {
                const lane: i32 = if (decision.selected) |slot| @intCast(slot) else -1;
                if (set_primary.?(pipelines[0].handle, lane) != 0) break;
            }
        }
        if (c.GetTickCount64() >= nextCheck) {
            nextCheck = c.GetTickCount64() + 1000;
            var status = hook.call("status") catch {
                break;
            };
            const active = (u.number(u.field(status.value, "state")) catch 0) == 1;
            status.deinit();
            if (!active) break;
        }
        c.Sleep(100);
    }
    if (pcm) |*session| session.stopAndFinish();
    try shutdown(&hook, &receiver, pipelines, &bridge, &thread, &recorder, &recordThread, out);
    if(wasapi) |*session| {
        try session.drain();
        if(session.failed())return error.WasapiOutputFailed;
    }
    if (audio) |session| {
        session.finish();
        if (session.stream.snapshot().failure != null) return error.AsioOutputFailed;
    }
}
fn shutdown(hook: *n.Hook, receiver: *n.Receiver, pipelines: []n.Pipeline, bridge: *Bridge, thread: *std.Thread, recorder: *Recorder, recordThread: *?std.Thread, out: []const u8) !void {
    // Never free a mapping/renderer until Hook stop and receiver join are proven.
    var stats: ?std.json.Parsed(u.Value) = null;
    while (stats == null) {

        if ((try u.processIdentity(hook.pid)) != hook.config.target_created) break;
        var result = hook.call("stop") catch {
            var inventory = u.modules(hook.pid) catch {
                c.Sleep(1000);
                continue;
            };
            defer inventory.deinit();
            if (!inventory.has("nsp_managed_hook_v2.dll")) break;
            if (hook.config.fixture == 0) {
                var baseline = hook.call("baseline") catch {
                    c.Sleep(1000);
                    continue;
                };
                defer baseline.deinit();
                if (u.yes(u.field(baseline.value, "baselineVerified"))) break;
            }
            c.Sleep(1000);
            continue;
        };
        if (!u.yes(u.field(result.value, "hookBytesRestored"))) {
            result.deinit();
            c.Sleep(1000);
            continue;
        }
        stats = result;
    }
    defer if (stats) |*v| v.deinit();
    var expected: u64 = receiver.received(receiver.handle);
    if (stats) |v| expected = u.number(u.field(v.value, "copies")) catch expected;
    const deadline = c.GetTickCount64() + 5000;
    while (receiver.received(receiver.handle) < expected and c.GetTickCount64() < deadline) c.Sleep(5);
    // The bridge queries receiver diagnostics. Join it before finishing or
    // destroying that handle; the final tap drain below covers pending output.
    bridge.stop.store(true, .release);
    thread.join();
    var report = while (true) {
        const r = receiver.finish() catch {
            c.Sleep(1000);
            continue;
        };
        break r;
    };
    defer report.deinit();
    try receiver.close();
    recorder.stop.store(true, .release);
    if(recordThread.*) |worker| worker.join();
    // Renderer finish joins its producer; drain EOS from still-live tap handles
    // only after the bridge thread has joined, before destroying each pipeline.
    bridge.socket = c.socket(c.AF_INET, c.SOCK_DGRAM, c.IPPROTO_UDP);
    defer {
        if (bridge.socket != c.INVALID_SOCKET) _ = c.closesocket(bridge.socket);
    }
    var nonblock: c_ulong = 1;
    _ = c.ioctlsocket(bridge.socket, @as(c_long, @bitCast(@as(u32, 0x8004667e))), &nonblock);
    var passed = u.yes(u.field(report.value, "passed")) and (u.number(u.field(report.value, "received")) catch 0) == expected;
    if (stats) |v| {
        passed = passed and u.yes(u.field(v.value, "captureLibraryReleased")) and u.yes(u.field(v.value, "transportLibraryReleased"));
        for ([_][]const u8{ "queueDropped", "budgetRejected", "queueRemaining", "concurrentRejected", "faults", "sendErrors" }) |key| passed = passed and (u.number(u.field(v.value, key)) catch 1) == 0;
    }
    for (pipelines, 0..) |*pipeline, i| {
        var summary = try pipeline.finish();
        defer summary.deinit();
        if (bridge.selected == i and bridge.socket != c.INVALID_SOCKET) {
            for (0..16) |_| try bridge.pump(pipeline);
        }
        const enqueued = u.number(u.field(summary.value, "enqueued")) catch 0;
        passed = passed and (u.yes(u.field(summary.value, "passed")) or (enqueued == 0 and u.field(summary.value, "error") == .null and u.yes(u.field(summary.value, "threadStopped")) and (u.number(u.field(summary.value, "queueDropped")) catch 1) == 0));
        const path = try u.fmt("{s}/stream-{d}/native-summary.json", .{ out, i });
        defer a.free(path);
        try u.writeJson(path, summary.value);
        pipeline.close();
    }
    const reportPath = try u.join(&.{ out, "observer.json" });
    defer a.free(reportPath);
    try u.writeJson(reportPath, .{ .scheduler = "zig", .finalStats = if (stats) |v| v.value else @as(u.Value, .null), .nativeTransport = report.value, .streamSummary = .{ .passed = passed }, .telemetryBridgeError = bridge.failure, .telemetryRecordError = recorder.failure, .bridgeForwardedOutputBlocks = bridge.forwarded, .bridgeSendErrors = bridge.dropped });
    c.Sleep(200);
    const final = finalTelemetry(bridge) catch null;
    if (final) |bytes| {
        defer a.free(bytes);
        const path = try u.join(&.{ out, "telemetry-summary.json" });
        defer a.free(path);
        try u.write(path, bytes);
    }
    if (!passed) return error.SessionValidationFailed;
}
fn finalTelemetry(bridge: *Bridge) ![]u8 {
    const status = try bridge.telemetry.query("status");
    defer a.free(status);
    const snapshot = try bridge.telemetry.query("snapshot");
    defer a.free(snapshot);
    const history600 = try bridge.telemetry.query("history600");
    defer a.free(history600);
    const history3600 = try bridge.telemetry.query("history3600");
    defer a.free(history3600);
    return u.fmt("{{\"status\":{s},\"snapshot\":{s},\"history600\":{s},\"history3600\":{s},\"bridgeForwardedOutputBlocks\":{d},\"bridgeSendErrors\":{d}}}", .{ status, snapshot, history600, history3600, bridge.forwarded, bridge.dropped });
}
