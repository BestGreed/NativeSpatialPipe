const std = @import("std");
const SocketIO = @import("socket_io.zig").SocketIO;
const u = @import("common.zig");
const n = @import("native.zig");
const a = u.a;
const c = u.c;
pub const Config = struct { version: u32 = 1, spatialFormat: []const u8 = "WindowsSonic", fullTiming: bool = false };
pub fn validFormat(format: []const u8) bool {
    for ([_][]const u8{ "WindowsSonic", "DTSXForHomeTheater", "DolbyAtmosForHomeTheater", "DolbyAtmosForHeadphones", "DolbyAtmosForSpeakers", "DTSHeadphoneX", "DTSXUltra" }) |name| {
        if (std.mem.eql(u8, format, name)) return true;
    }
    return false;
}
const Playback = @import("desktop_playback.zig").Config;
const Session = struct { playback: ?Playback = null, id: []const u8, outputDirectory: []const u8, config: Config, ownerPid: u32, endpoint: []const u8, orchestratorPid: u32 = 0, state: []const u8 = "starting", @"error": ?[]const u8 = null };
pub const Controller = struct {
    root: []const u8,
    home: []const u8,
    python: []const u8,
    port: u16,
    telemetryPort: u16,
    config: Config = .{},
    playback_owner: ?std.json.Parsed(Playback) = null,
    session: ?Session = null,
    process: ?u.Process = null,
    state: []const u8 = "idle",
    err: ?[]const u8 = null,
    deadline: u64 = 0,
    exitRequested: bool = false,
    fn statePath(self: *Controller, name: []const u8) ![]u8 {
        return u.join(&.{ self.home, name });
    }
    fn save(self: *Controller) !void {
        if (self.session) |*session| {
            session.state = self.state;
            session.@"error" = self.err;
            const path = try self.statePath("session.json");
            defer a.free(path);
            try u.writeJson(path, session.*);
        }
    }
    pub fn init(root: []const u8, home: []const u8, python: []const u8, port: u16, telemetryPort: u16) !Controller {
        var self = Controller{ .root = root, .home = home, .python = python, .port = port, .telemetryPort = telemetryPort };
        const config = try self.statePath("config.json");
        defer a.free(config);
        if (u.exists(config)) {
            var parsed = try u.load(config);
            defer parsed.deinit();
            if ((try u.number(u.field(parsed.value, "version"))) != 1 or u.field(parsed.value, "fullTiming") != .bool) return error.InvalidConfig;
            const format = try u.string(u.field(parsed.value, "spatialFormat"));
            if (!validFormat(format)) return error.InvalidConfig;
            self.config.spatialFormat = try a.dupe(u8, format);
            self.config.fullTiming = u.yes(u.field(parsed.value, "fullTiming"));
        }
        const sessionPath = try self.statePath("session.json");
        defer a.free(sessionPath);
        if (u.exists(sessionPath)) {
            var parsed = try u.load(sessionPath);
            defer parsed.deinit();
            const v = parsed.value;
            const prior = try u.string(u.field(v, "state"));
            self.session = .{ .id = try a.dupe(u8, try u.string(u.field(v, "id"))), .outputDirectory = try a.dupe(u8, try u.string(u.field(v, "outputDirectory"))), .config = self.config, .ownerPid = @intCast(u.number(u.field(v, "ownerPid")) catch 0), .endpoint = try a.dupe(u8, u.string(u.field(v, "endpoint")) catch "") };
            self.session.?.orchestratorPid = std.math.cast(u32, u.number(u.field(v, "orchestratorPid")) catch 0) orelse 0;
            if (!std.mem.eql(u8, prior, "stopped") and !std.mem.eql(u8, prior, "idle")) {
                self.state = "recovery-required";
                self.err = "Previous session requires confirmed stop and endpoint restore";
            }
        }
        try u.writeJson(config, self.config);
        return self;
    }
    fn reportClean(self: *Controller, recovery: bool) bool {
        const session = self.session orelse return false;
        if (session.orchestratorPid != 0 and (u.processIdentity(session.orchestratorPid) catch return false) != null) return false;
        const path = u.join(&.{ session.outputDirectory, "endpoint.json" }) catch return false;
        defer a.free(path);
        var parsed = u.load(path) catch return false;
        defer parsed.deinit();
        const v = parsed.value;
        if (!u.yes(u.field(v, "restored")) or !u.yes(u.field(v, "hookCompleted"))) return false;
        if ((u.number(u.field(v, "hookExitCode")) catch 255) == 0) return recovery or u.field(v, "error") == .null;
        if (!recovery) return false;
        const markerPath = u.join(&.{ session.outputDirectory, "observed/hook-session.json" }) catch return false;
        defer a.free(markerPath);
        if (!u.exists(markerPath)) {
            const value = u.json(session) catch return false;
            defer a.free(value);
            var object = u.parse(value) catch return false;
            defer object.deinit();
            return @import("stop_proof.zig").unmarked(self.root, self.home, object.value, session.outputDirectory) catch false;
        }
        var marker = u.load(markerPath) catch return false;
        defer marker.deinit();
        const pid = std.math.cast(u32, u.number(u.field(marker.value, "pid")) catch return false) orelse return false;
        const created = u.number(u.field(marker.value, "created")) catch return false;
        if ((u.inspectProcessIdentity(pid) catch return false) != created) return true;
        u.debugPrivilege() catch return false;
        if ((u.number(u.field(marker.value, "hookGeneration")) catch 0) != 2) return false;
        var hook = n.Hook.init(self.root, self.home, pid, 0, created, false) catch return false;
        defer {
            u.remove(hook.path);
            a.free(hook.path);
        }
        var actual = hook.call("status") catch return false;
        defer actual.deinit();
        if ((u.number(u.field(actual.value, "state")) catch 0) == 2 and u.yes(u.field(actual.value, "hookBytesRestored"))) return true;
        if ((u.number(u.field(actual.value, "state")) catch 0) == 0) {
            var baseline = hook.call("baseline") catch return false;
            defer baseline.deinit();
            return u.yes(u.field(baseline.value, "baselineVerified"));
        }
        return false;
    }
    pub fn tick(self: *Controller) !void {
        if (self.process) |*process| {
            if (try u.done(process)) |code| {
                const clean = code == 0 and self.reportClean(false);
                process.close();
                self.process = null;
                self.state = if (clean) "stopped" else "faulted";
                self.err = if (clean) null else "Session ended without confirmed clean stop and restore";
                try self.save();
            } else if (std.mem.eql(u8, self.state, "starting")) {
                const ready = try u.join(&.{ self.session.?.outputDirectory, "observed/ready.txt" });
                defer a.free(ready);
                if (u.exists(ready)) {
                    self.state = "running";
                    try self.save();
                }
            } else if (std.mem.eql(u8, self.state, "stopping") and c.GetTickCount64() > self.deadline) {
                self.state = "faulted";
                self.err = "Stop timed out; resources retained";
                try self.save();
            }
        }
    }
    pub fn status(self: *Controller) ![]u8 {
        try self.tick();
        return u.json(.{ .backend = "ready", .scheduler = "zig", .root = self.root, .capabilities = .{ .desktopPlayback = true, .guardedStop = true }, .pid = c.GetCurrentProcessId(), .sessionState = self.state, .dataState = if (std.mem.eql(u8, self.state, "running")) (if (telemetryLive(self.telemetryPort)) "live" else "waiting-or-stale") else "disconnected", .@"error" = self.err, .session = self.session, .config = self.config });
    }
    pub fn command(self: *Controller, action: []const u8, value: u.Value) ![]u8 {
        try self.tick();
        if (std.mem.eql(u8, action, "status")) return self.status();
        if (value != .object) return error.ObjectRequired;
        if (std.mem.eql(u8, action, "start-session")) {
            const owner = std.math.cast(u32, try u.number(u.field(value, "ownerPid"))) orelse return error.InvalidOwnerPid;
            if (owner == 0) return error.OwnerRequired;
            var fields = value.object.iterator();
            while (fields.next()) |entry| {
                if (!std.mem.eql(u8, entry.key_ptr.*, "ownerPid") and !std.mem.eql(u8, entry.key_ptr.*, "endpoint") and !std.mem.eql(u8, entry.key_ptr.*, "spatialFormat") and !std.mem.eql(u8,entry.key_ptr.*,"playback")) return error.UnsupportedSessionField;
            }
            var playback: ?Playback = null;
            var playback_owner: ?std.json.Parsed(Playback) = null;
            defer if(playback_owner) |*parsed| parsed.deinit();
            if (u.field(value,"playback") != .null) {
                playback_owner = try std.json.parseFromValue(Playback,a,u.field(value,"playback"),.{.allocate=.alloc_always});
                playback=playback_owner.?.value;
                try playback.?.validate();
            }
            const endpoint = try u.string(u.field(value, "endpoint"));
            const frontend = if (u.field(value,"spatialFormat") == .null) "WindowsSonic" else try u.string(u.field(value,"spatialFormat"));
            if (!validFormat(frontend)) return error.InvalidConfig;
            if (endpoint.len == 0) return error.EndpointRequired;
            if (std.mem.eql(u8, self.state, "running") or std.mem.eql(u8, self.state, "starting")) {
                if (self.session.?.ownerPid != owner or !std.mem.eql(u8, self.session.?.endpoint, endpoint) or !std.mem.eql(u8,self.session.?.config.spatialFormat,frontend)) return error.OwnerConflict;
                const prior=try u.json(self.session.?.playback);defer a.free(prior);
                const requested=try u.json(playback);defer a.free(requested);
                if(!std.mem.eql(u8,prior,requested)) return error.ActivePlaybackConfiguration;
                return self.status();
            }
            if (self.process != null or (!std.mem.eql(u8, self.state, "idle") and !std.mem.eql(u8, self.state, "stopped"))) return error.RecoveryRequired;
            var nonce: [16]u8 = undefined;
            std.crypto.random.bytes(&nonce);
            const ident = try u.fmt("{s}", .{std.fmt.fmtSliceHexLower(&nonce)});
            const output = try u.join(&.{ self.home, "sessions", ident });
            self.state = "starting";
            self.err = null;
            self.session = .{ .playback = playback, .id = ident, .outputDirectory = output, .config = self.config, .ownerPid = owner, .endpoint = try a.dupe(u8, endpoint) };
            if(self.playback_owner) |*prior| prior.deinit();
            self.playback_owner=playback_owner;
            playback_owner=null;
            self.session.?.config.spatialFormat = try a.dupe(u8,frontend);
            try self.save(); // Durable intent precedes endpoint and Hook effects.
            errdefer {
                self.state = "faulted";
                self.err = "Launch failed; inspect session logs";
                self.save() catch {};
            }
            const script = try u.join(&.{ self.root, "tools/wwise-probe/Run-GameTelemetry.ps1" });
            defer a.free(script);
            const exe = try std.fs.selfExePathAlloc(a);
            defer a.free(exe);
            const ownerText = try u.fmt("{d}", .{owner});
            defer a.free(ownerText);
            const tport = try u.fmt("{d}", .{self.telemetryPort});
            defer a.free(tport);
            const origin = try u.fmt("http://127.0.0.1:{d}", .{self.port});
            defer a.free(origin);
            const log = try u.fmt("{s}.log", .{output});
            defer a.free(log);
            var launchArgs = std.ArrayList([]const u8).init(a);
            defer launchArgs.deinit();
            try launchArgs.appendSlice(&.{ "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-OutputDirectory", output, "-SpatialFormat", frontend, "-Managed", "-OwnerPid", ownerText, "-Endpoint", endpoint, "-SchedulerExecutable", exe, "-TelemetryPython", self.python, "-TelemetryPort", tport, "-FrontendOrigin", origin });
            if (playback) |settings| {
                // The existing orchestrator requires a new output directory.
                // Keep launch settings beside it until the orchestrator creates it.
                const path=try u.fmt("{s}.playback.json",.{output});
                try u.writeJson(path,settings);
                try launchArgs.appendSlice(&.{"-PlaybackConfig",path});
            }
            if (self.config.fullTiming) try launchArgs.append("-FullTiming");
            self.process = try u.spawnLog(launchArgs.items, log);
            self.session.?.orchestratorPid = self.process.?.pid;
            try self.save();
        } else if (std.mem.eql(u8, action, "stop-session")) {
            // Additive guarded stop for clients. Existing FSA requests retain
            // their original contract; clients must provide both owner and id.
            if (u.field(value,"ownerPid") != .null or u.field(value,"sessionId") != .null) {
                const session=self.session orelse return error.NoOwnedSession;
                if ((try u.number(u.field(value,"ownerPid"))) != session.ownerPid or
                    !std.mem.eql(u8,try u.string(u.field(value,"sessionId")),session.id)) return error.OwnerConflict;
            }
            if (self.process != null) {
                const stop = try u.fmt("{s}.stop", .{self.session.?.outputDirectory});
                defer a.free(stop);
                try u.write(stop, "stop");
                if (!std.mem.eql(u8, self.state, "stopping")) {
                    self.state = "stopping";
                    self.deadline = c.GetTickCount64() + 45000;
                    try self.save();
                }
            } else if (!std.mem.eql(u8, self.state, "idle") and !std.mem.eql(u8, self.state, "stopped")) return error.OwnershipUnknown;
        } else if (std.mem.eql(u8, action, "recover")) {
            // Desktop recovery must not race into a different owner's session.
            // Existing FSA/CLI recovery without identity fields stays compatible.
            if (u.field(value,"ownerPid") != .null or u.field(value,"sessionId") != .null) {
                const session=self.session orelse return error.NoOwnedSession;
                if ((try u.number(u.field(value,"ownerPid"))) != session.ownerPid or
                    !std.mem.eql(u8,try u.string(u.field(value,"sessionId")),session.id)) return error.OwnerConflict;
            }
            if (self.process != null or !self.reportClean(true)) return error.StopEvidenceRequired;
            self.state = "stopped";
            self.err = null;
            try self.save();
        } else if (std.mem.eql(u8, action, "configure")) {
            if (self.process != null or (!std.mem.eql(u8, self.state, "idle") and !std.mem.eql(u8, self.state, "stopped"))) return error.SessionActive;
            var candidate = self.config;
            var it = value.object.iterator();
            while (it.next()) |entry| {
                if (std.mem.eql(u8, entry.key_ptr.*, "spatialFormat")) {
                    const text = try u.string(entry.value_ptr.*);
                    if (!validFormat(text)) return error.InvalidConfig;
                    candidate.spatialFormat = try a.dupe(u8, text);
                } else if (std.mem.eql(u8, entry.key_ptr.*, "fullTiming")) {
                    if (entry.value_ptr.* != .bool) return error.InvalidConfig;
                    candidate.fullTiming = entry.value_ptr.bool;
                } else return error.InvalidConfig;
            }
            const path = try self.statePath("config.json");
            defer a.free(path);
            try u.writeJson(path, candidate);
            self.config = candidate;
        } else return error.UnsupportedCommand;
        return self.status();
    }
};
fn telemetryLive(port: u16) bool {
    const address = std.net.Address.parseIp4("127.0.0.1", port) catch return false;
    const stream = std.net.tcpConnectToAddress(address) catch return false;
    defer stream.close();
    var io = SocketIO.init(stream, 300) catch return false;
    io.writer().writeAll("GET /v1/routes/native-route/snapshot HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n") catch return false;
    var prefix: [12]u8 = undefined;
    io.reader().readNoEof(&prefix) catch return false;
    return std.mem.eql(u8, &prefix, "HTTP/1.1 200") or std.mem.eql(u8, &prefix, "HTTP/1.0 200");
}
pub fn sendResponse(stream: std.net.Stream, code: u16, mime: []const u8, body: []const u8) !void {
    var io = try SocketIO.init(stream, 2000);
    const headers = try u.fmt("HTTP/1.1 {d} Result\r\nContent-Type: {s}\r\nContent-Length: {d}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n", .{ code, mime, body.len });
    defer a.free(headers);
    try io.writer().writeAll(headers);
    try io.writer().writeAll(body);
}
pub fn staticFile(root: []const u8, path: []const u8) !struct { body: []u8, mime: []const u8 } {
    if (std.mem.indexOfAny(u8, path, "\\%?#") != null or std.mem.indexOf(u8, path, "..") != null) return error.InvalidPath;
    const base = if (std.mem.startsWith(u8, path, "/contracts/")) "contracts" else if (std.mem.startsWith(u8, path, "/replay/")) "build/wwise-visualization-replay" else "tools/visualization-frontend";
    const relative = if (std.mem.startsWith(u8, path, "/contracts/")) path[11..] else if (std.mem.startsWith(u8, path, "/replay/")) path[8..] else if (std.mem.eql(u8, path, "/")) "index.html" else path[1..];
    const directory = try u.join(&.{ root, base });
    defer a.free(directory);
    const file = try u.join(&.{ directory, relative });
    defer a.free(file);
    const resolved = try std.fs.cwd().realpathAlloc(a, file);
    defer a.free(resolved);
    const resolvedBase = try std.fs.cwd().realpathAlloc(a, directory);
    defer a.free(resolvedBase);
    if (!std.mem.startsWith(u8, resolved, resolvedBase) or resolved.len <= resolvedBase.len or (resolved[resolvedBase.len] != '\\' and resolved[resolvedBase.len] != '/')) return error.InvalidPath;
    const ext = std.fs.path.extension(file);
    const mime = if (std.mem.eql(u8, ext, ".html")) "text/html; charset=utf-8" else if (std.mem.eql(u8, ext, ".js")) "text/javascript; charset=utf-8" else if (std.mem.eql(u8, ext, ".css")) "text/css; charset=utf-8" else if (std.mem.eql(u8, ext, ".json")) "application/json" else if (std.mem.eql(u8, ext, ".svg")) "image/svg+xml" else "application/octet-stream";
    return .{ .body = try u.read(file), .mime = mime };
}
fn handle(controller: *Controller, stream: std.net.Stream, token: []const u8) !bool {
    var io = try SocketIO.init(stream, 2000);
    var buffer: [12288]u8 = undefined;
    var used: usize = 0;
    var end: usize = 0;
    while (used < 8192) {
        const count = try io.read(buffer[used..8192]);
        if (count == 0) return error.TruncatedRequest;
        used += count;
        if (std.mem.indexOf(u8, buffer[0..used], "\r\n\r\n")) |i| {
            end = i + 4;
            break;
        }
    }
    if (end == 0) return error.RequestTooLarge;
    var lines = std.mem.splitSequence(u8, buffer[0..end], "\r\n");
    var words = std.mem.splitScalar(u8, lines.next() orelse return error.BadRequest, ' ');
    const method = words.next() orelse return error.BadRequest;
    const path = words.next() orelse return error.BadRequest;
    var authorized = false;
    var origin = false;
    var size: usize = 0;
    const expected = try u.fmt("Bearer {s}", .{token});
    defer a.free(expected);
    while (lines.next()) |line| {
        const colon = std.mem.indexOfScalar(u8, line, ':') orelse continue;
        const name = line[0..colon];
        const value = std.mem.trim(u8, line[colon + 1 ..], " \t");
        if (std.ascii.eqlIgnoreCase(name, "Authorization")) authorized = std.mem.eql(u8, value, expected);
        if (std.ascii.eqlIgnoreCase(name, "Origin")) origin = true;
        if (std.ascii.eqlIgnoreCase(name, "Content-Length")) size = try std.fmt.parseInt(usize, value, 10);
    }
    if (std.mem.eql(u8, method, "GET")) {
        if (std.mem.eql(u8, path, "/api/backend/status")) {
            const status = try controller.status();
            defer a.free(status);
            try sendResponse(stream, 200, "application/json", status);
        } else {
            const file = staticFile(controller.root, path) catch {
                try sendResponse(stream, 404, "application/json", "{\"error\":\"not-found\"}");
                return false;
            };
            defer a.free(file.body);
            try sendResponse(stream, 200, file.mime, file.body);
        }
        return false;
    }
    if (!std.mem.eql(u8, method, "POST") or !authorized or origin) {
        try sendResponse(stream, 403, "application/json", "{\"error\":\"unauthorized\"}");
        return false;
    }
    if (!std.mem.startsWith(u8, path, "/api/backend/") or size == 0 or size > 4096) return error.BadRequest;
    while (used < end + size) {
        const count = try io.read(buffer[used .. end + size]);
        if (count == 0) return error.TruncatedRequest;
        used += count;
    }
    var parsed = try u.parse(buffer[end .. end + size]);
    defer parsed.deinit();
    const action = path[13..];
    const exiting = std.mem.eql(u8, action, "exit");
    const result = controller.command(if (exiting) "stop-session" else action, parsed.value) catch |err| {
        const failure = try u.json(.{ .@"error" = @errorName(err) });
        defer a.free(failure);
        try sendResponse(stream, 409, "application/json", failure);
        return false;
    };
    defer a.free(result);
    if (exiting) controller.exitRequested = true;
    const clean = std.mem.eql(u8, controller.state, "idle") or std.mem.eql(u8, controller.state, "stopped");
    try sendResponse(stream, if (exiting and !clean) 409 else 200, "application/json", result);
    return exiting and clean;
}
pub fn serve(args: []const []const u8, root: []const u8, home: []const u8) !void {
    try std.fs.cwd().makePath(home);
    const lockPath = try u.join(&.{ home, "owner.lock" });
    defer a.free(lockPath);
    const lockW = try u.wide(lockPath);
    defer a.free(lockW);
    const lock = c.CreateFileW(lockW.ptr, c.GENERIC_READ | c.GENERIC_WRITE, 0, null, c.OPEN_ALWAYS, c.FILE_ATTRIBUTE_NORMAL, null);
    if (lock == c.INVALID_HANDLE_VALUE) return error.BackendAlreadyOwned;
    defer _ = c.CloseHandle(lock);
    const port = try std.fmt.parseInt(u16, u.option(args, "--port") orelse "8765", 10);
    var server = try (try std.net.Address.parseIp4("127.0.0.1", port)).listen(.{ .force_nonblocking = true });
    defer server.deinit();
    var controller = try Controller.init(root, home, u.option(args, "--python") orelse "python.exe", server.listen_address.getPort(), try std.fmt.parseInt(u16, u.option(args, "--telemetry-port") orelse "8766", 10));
    var nonce: [32]u8 = undefined;
    std.crypto.random.bytes(&nonce);
    const token = try u.fmt("{s}", .{std.fmt.fmtSliceHexLower(&nonce)});
    defer a.free(token);
    const runtime = try u.join(&.{ home, "runtime.json" });
    defer a.free(runtime);
    try u.writeJson(runtime, .{ .pid = c.GetCurrentProcessId(), .scheduler = "zig", .url = try u.fmt("http://127.0.0.1:{d}", .{controller.port}), .port = controller.port, .token = token });
    defer u.remove(runtime);
    while (true) {
        try controller.tick();
        if (controller.exitRequested and (std.mem.eql(u8, controller.state, "idle") or std.mem.eql(u8, controller.state, "stopped"))) break;
        const connection = server.accept() catch |err| {
            if (err != error.WouldBlock) return err;
            c.Sleep(50);
            continue;
        };
        defer connection.stream.close();
        if (handle(&controller, connection.stream, token) catch false) break;
    }
}
pub fn request(home: []const u8, action: []const u8, value: anytype) ![]u8 {
    const path = try u.join(&.{ home, "runtime.json" });
    defer a.free(path);
    var runtime = try u.load(path);
    defer runtime.deinit();
    if (!std.mem.eql(u8, u.string(u.field(runtime.value, "scheduler")) catch "", "zig")) return error.BackendIsNotZig;
    const port: u16 = @intCast(try u.number(u.field(runtime.value, "port")));
    const token = try u.string(u.field(runtime.value, "token"));
    const body = try u.json(value);
    defer a.free(body);
    const stream = try std.net.tcpConnectToAddress(try std.net.Address.parseIp4("127.0.0.1", port));
    defer stream.close();
    var io = try SocketIO.init(stream, 2000);
    const message = try u.fmt("POST /api/backend/{s} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {s}\r\nContent-Type: application/json\r\nContent-Length: {d}\r\nConnection: close\r\n\r\n{s}", .{ action, token, body.len, body });
    defer a.free(message);
    try io.writer().writeAll(message);
    const reply = try io.reader().readAllAlloc(a, 1024 * 1024);
    defer a.free(reply);
    if (!std.mem.startsWith(u8, reply, "HTTP/1.1 200 ")) return error.BackendRejected;
    const start = (std.mem.indexOf(u8, reply, "\r\n\r\n") orelse return error.BadResponse) + 4;
    return a.dupe(u8, reply[start..]);
}
