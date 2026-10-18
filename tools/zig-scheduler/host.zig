const std = @import("std");
const u = @import("common.zig");
const n = @import("native.zig");
const routing = @import("routing.zig");
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
    pub fn start(root: []const u8, python: []const u8, port: []const u8, origin: []const u8) !Telemetry {
        var listener = try (try std.net.Address.parseIp4("127.0.0.1", 0)).listen(.{ .force_nonblocking = true });
        defer listener.deinit();
        var secret: [32]u8 = undefined;
        std.crypto.random.bytes(&secret);
        const secretHex = try u.fmt("{s}", .{std.fmt.fmtSliceHexLower(&secret)});
        defer a.free(secretHex);
        const controlPort = try u.fmt("{d}", .{listener.listen_address.getPort()});
        defer a.free(controlPort);
        const script = try u.join(&.{ root, "tools/telemetry/standalone.py" });
        defer a.free(script);
        var process = try u.spawn(&.{ python, script, "--control-port", controlPort, "--secret", secretHex, "--http-port", port, "--origin", origin });
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
    pipelines: *[4]n.Pipeline,
    telemetry: *Telemetry,
    out: []const u8,
    stop: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    routes: routing.Routes = .{},
    selected: ?usize = null,
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
        var files: [4]?std.fs.File = .{ null, null, null, null };
        defer for (files) |file| {
            if (file) |f| f.close();
        };
        while (!self.stop.load(.acquire)) {
            const now = c.GetTickCount64();
            if (now >= nextPoll) {
                nextPoll = now + 100;
                var sizes: [4]?u64 = .{ null, null, null, null };
                for (&files, 0..) |*file, i| {
                    if (file.* == null) {
                        const path = try u.fmt("{s}/stream-{d}/roar-714.f32", .{ self.out, i });
                        defer a.free(path);
                        file.* = std.fs.cwd().openFile(path, .{}) catch null;
                    }
                    if (file.*) |f| {
                        sizes[i] = f.getEndPos() catch null;
                    }
                }
                const next = self.routes.update(now, sizes);
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
        for (self.routes.rows, 0..) |row, i| rows[i] = .{ .pipeline = i, .outputBytes = row.size, .ageSeconds = if (row.last) |v| @as(f64, @floatFromInt(now - v)) / 1000 else null, .progressUpdates = row.updates, .@"error" = if (row.failed) "read-error" else null };
        const path = try u.join(&.{ self.out, "telemetry-routing.json" });
        defer a.free(path);
        try u.writeJson(path, .{ .policy = "latest-active-render-output", .pollSeconds = @as(f64, 0.1), .staleAfterSeconds = @as(f64, 1), .selectedPipeline = self.selected, .selectionChanges = self.routes.changes, .routes = rows });
    }
    fn send(self: *Bridge, bytes: []const u8) void {
        const result = c.sendto(self.socket, @ptrCast(bytes.ptr), @intCast(bytes.len), 0, @ptrCast(&self.telemetry.udp.any), @intCast(self.telemetry.udp.getOsSockLen()));
        if (result != bytes.len) self.dropped += 1;
    }
    fn pump(self: *Bridge, pipeline: *n.Pipeline) !void {
        const max = 32 + 160 * 1968;
        const packet = &self.packet;
        @memcpy(packet[0..32], &self.telemetry.secret);
        var raw: [23072]u8 = undefined;
        for (0..16) |_| {
            const size = pipeline.tap(pipeline.handle, &raw, raw.len);
            if (size == 0) break;
            if (size != raw.len) return error.RawTapFormat;
            self.epoch = @max(self.epoch, std.mem.readInt(u64, raw[16..24], .little));
            std.mem.writeInt(u64, raw[16..24], self.epoch, .little);
            @memcpy(packet[32 .. 32 + size], &raw);
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
    const created = if (fixture) (try u.processIdentity(pid)) orelse return error.TargetExited else try u.checkTarget(pid);
    if (try u.processIdentity(owner) == null) return error.OwnerExited;
    if (u.exists(out)) return error.OutputExists;
    try std.fs.cwd().makePath(out);
    // The scheduler owns setup only; all audio submission stays inside native DLLs.
    _ = c.SetEnvironmentVariableW((try u.wide("NSP_RENDER_MMCSS")).ptr, (try u.wide("1")).ptr);
    const dll = try u.join(&.{ root, "build/telemetry-objects/release/nsp_roar_bridge.dll" });
    defer a.free(dll);
    var pipelines: [4]n.Pipeline = undefined;
    const first = try u.fmt("{s}/stream-0", .{out});
    defer a.free(first);
    try std.fs.cwd().makePath(first);
    const firstPath = try u.join(&.{ first, "roar-714.f32" });
    defer a.free(firstPath);
    pipelines[0] = try n.Pipeline.init(dll, firstPath, 64);
    var telemetry = try Telemetry.start(root, u.option(args, "--python") orelse "python.exe", u.option(args, "--telemetry-port") orelse "8766", u.option(args, "--origin") orelse "http://127.0.0.1:8765");
    defer telemetry.close();
    const infoPath = try u.join(&.{ out, "telemetry-service.json" });
    defer a.free(infoPath);
    try u.write(infoPath, telemetry.info);
    for (1..4) |i| {
        const directory = try u.fmt("{s}/stream-{d}", .{ out, i });
        defer a.free(directory);
        try std.fs.cwd().makePath(directory);
        const path = try u.join(&.{ directory, "roar-714.f32" });
        defer a.free(path);
        pipelines[i] = try n.Pipeline.init(dll, path, 8);
    }
    var receiver = try n.Receiver.init(root, pid, &pipelines, u.flag(args, "--full-timing"));
    var hook = try n.Hook.init(root, out, pid, owner, created, fixture);
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
    var bridge = Bridge{ .pipelines = &pipelines, .telemetry = &telemetry, .out = out };
    var thread = try std.Thread.spawn(.{}, Bridge.run, .{&bridge});
    var recorder = Recorder{ .telemetry = &telemetry, .out = out };
    var recordThread = try std.Thread.spawn(.{}, Recorder.run, .{&recorder});
    // Establish cleanup before the first Hook activation attempt, including timeouts.
    var activation = hook.call("start") catch |err| {
        const failure = u.join(&.{ out, "start-error.json" }) catch null;
        if (failure) |path| {
            defer a.free(path);
            u.writeJson(path, .{ .errorName = @errorName(err) }) catch {};
        }
        try shutdown(&hook, &receiver, &pipelines, &bridge, &thread, &recorder, &recordThread, out);
        return err;
    };
    activation.deinit();
    const readyPath = try u.join(&.{ out, "ready.txt" });
    defer a.free(readyPath);
    u.write(readyPath, "ready") catch |err| {
        try shutdown(&hook, &receiver, &pipelines, &bridge, &thread, &recorder, &recordThread, out);
        return err;
    };
    const deadline = if (u.option(args, "--duration-ms")) |v| c.GetTickCount64() + try std.fmt.parseInt(u64, v, 10) else std.math.maxInt(u64);
    var nextCheck: u64 = 0;
    while (c.GetTickCount64() < deadline and !u.exists(stopFile)) {
        if (receiver.failed(receiver.handle) != 0) break;
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
    try shutdown(&hook, &receiver, &pipelines, &bridge, &thread, &recorder, &recordThread, out);
}
fn shutdown(hook: *n.Hook, receiver: *n.Receiver, pipelines: *[4]n.Pipeline, bridge: *Bridge, thread: *std.Thread, recorder: *Recorder, recordThread: *std.Thread, out: []const u8) !void {
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
    var report = while (true) {
        const r = receiver.finish() catch {
            c.Sleep(1000);
            continue;
        };
        break r;
    };
    defer report.deinit();
    try receiver.close();
    bridge.stop.store(true, .release);
    thread.join();
    recorder.stop.store(true, .release);
    recordThread.join();
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
