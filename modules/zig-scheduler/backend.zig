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

test "hot output control writes only route intent and preserves capture session" {
    var temp=std.testing.tmpDir(.{});defer temp.cleanup();
    const home=try temp.dir.realpathAlloc(a,".");defer a.free(home);
    const output=try u.join(&.{home,"sessions","route-test"});defer a.free(output);
    var controller=Controller{.root=home,.home=home,.python="unused",.port=0,.telemetryPort=0,.state="running",
        .session=.{.id="route-test",.outputDirectory=output,.config=.{},.ownerPid=123,.endpoint="fixture",.orchestratorPid=456,
            .playback=.{.bridgeDll="C:\\fixture\\bridge.dll",.outputBackend="aoip",.aoip=.{.interfaceIp="192.168.50.1",.masterIp="192.168.50.2",.mac="f0d7afd04b1d"}}}};
    var testRequest=try u.parse("{\"ownerPid\":123,\"sessionId\":\"route-test\",\"sourcePolicy\":\"fixed\",\"sourceStream\":1}");defer testRequest.deinit();
    const response=try controller.command("select-output",testRequest.value);defer a.free(response);
    const path=try u.join(&.{output,"observed","output-route-request.json"});defer a.free(path);
    var route=try u.load(path);defer route.deinit();
    try std.testing.expectEqual(@as(u64,1),try u.number(u.field(route.value,"sourceStream")));
    try std.testing.expectEqualStrings("running",controller.state);
    try std.testing.expectEqual(@as(u32,456),controller.session.?.orchestratorPid);
    try std.testing.expectEqual(@as(u32,0),controller.session.?.playback.?.sourceStream);
    try std.testing.expect(controller.process==null);
    try testRequest.value.object.put("ownerPid",.{.integer=124});
    try std.testing.expectError(error.OwnerConflict,controller.command("select-output",testRequest.value));
    try testRequest.value.object.put("ownerPid",.{.integer=123});
    controller.session.?.playback.?.sourcePolicy = "unified";
    try std.testing.expectError(error.OutputRouteUnavailable,controller.command("select-output",testRequest.value));
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
    ready_event: c.HANDLE = null,
    state: []const u8 = "idle",
    err: ?[]const u8 = null,
    deadline: u64 = 0,
    exitRequested: bool = false,
    audioPid: *const fn () anyerror!u32 = u.audioPid,
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
        if (recovery) {
            const bytes = u.json(session) catch return false;
            defer a.free(bytes);
            var parsed = u.parse(bytes) catch return false;
            defer parsed.deinit();
            if (@import("orphan_recovery.zig").proveWithAudio(self.root, self.home, parsed.value, self.audioPid) catch false) return true;
        }
        if (session.orchestratorPid != 0 and (u.processIdentity(session.orchestratorPid) catch return false) != null) return false;
        const path = u.join(&.{ session.outputDirectory, "endpoint.json" }) catch return false;
        defer a.free(path);
        var parsed = u.load(path) catch return false;
        defer parsed.deinit();
        const v = parsed.value;
        if (!u.yes(u.field(v, "restored"))) {
            if (!recovery) return false;
            const bytes = u.json(session) catch return false;
            defer a.free(bytes);
            var data = u.parse(bytes) catch return false;
            defer data.deinit();
            return @import("endpoint_retirement.zig").proven(session.outputDirectory, data.value);
        }
        if (!u.yes(u.field(v, "hookCompleted"))) return false;
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
                if (self.ready_event) |event| _ = c.CloseHandle(event);
                self.ready_event = null;
                self.state = if (clean) "stopped" else "faulted";
                self.err = if (clean) null else "Session ended without confirmed clean stop and restore";
                try self.save();
            } else if (std.mem.eql(u8, self.state, "starting")) {
                const event = self.ready_event orelse return error.ReadyEventMissing;
                const wait = c.WaitForSingleObject(event, 0);
                if (wait == c.WAIT_FAILED) return error.ReadyEventWait;
                if (wait == c.WAIT_OBJECT_0) {
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
        var arena_state = std.heap.ArenaAllocator.init(a);
        defer arena_state.deinit();
        const observation = self.aes67Observation(arena_state.allocator()) catch std.json.Value.null;
        return u.json(.{ .backend = "ready", .scheduler = "zig", .root = self.root, .capabilities = .{ .desktopPlayback = true, .guardedStop = true, .hotOutputRouting = true }, .pid = c.GetCurrentProcessId(), .sessionState = self.state, .dataState = if (std.mem.eql(u8, self.state, "running")) (if (telemetryLive(self.telemetryPort)) "live" else "waiting-or-stale") else "disconnected", .@"error" = self.err, .session = self.session, .config = self.config, .aes67 = observation });
    }

    /// Bounded, optional AES67 sender observation for the web status card.
    /// Reads only the current session's fixed relative report file: no
    /// historical scan and no absolute path exposure. Display fields are
    /// projected into a fresh object and a host-measured age is added, so the
    /// browser never compares clock domains; missing and stale stay distinct.
    fn aes67Observation(self: *Controller, arena: std.mem.Allocator) !std.json.Value {
        const session = self.session orelse return .null;
        const playback = session.playback orelse return .null;
        if (!std.mem.eql(u8, playback.outputBackend, "aes67")) return .null;
        const path = try u.join(&.{ session.outputDirectory, "observed/aes67-output.json" });
        defer a.free(path);
        const file = std.fs.cwd().openFile(path, .{}) catch |err| switch (err) {
            error.FileNotFound => return observedState(arena, "missing", null),
            else => return observedState(arena, "unavailable", null),
        };
        defer file.close();
        const stat = file.stat() catch return observedState(arena, "unavailable", null);
        const max_report_size = 64 * 1024;
        if (stat.kind != .file or stat.size == 0 or stat.size > max_report_size) return observedState(arena, "unavailable", null);
        const raw = file.readToEndAlloc(a, max_report_size) catch return observedState(arena, "unavailable", null);
        defer a.free(raw);
        const parsed = std.json.parseFromSlice(std.json.Value, arena, raw, .{ .allocate = .alloc_always }) catch return observedState(arena, "unavailable", null);
        if (parsed.value != .object) return observedState(arena, "unavailable", null);
        const now = std.time.nanoTimestamp();
        const age_ns: i128 = if (now > stat.mtime) now - stat.mtime else 0;
        const age_ms: u64 = @intCast(@divTrunc(age_ns, std.time.ns_per_ms));
        const source = parsed.value.object;
        var projected = std.json.ObjectMap.init(arena);
        try copyFields(&projected, source, &.{ "outputBackend", "outputLayout", "outputChannels", "outputChannelLabels", "sourcePolicy", "selectedSourceStream" });
        try copySubset(&projected, arena, source, "health", &.{ "active", "failure", "errorDetail" });
        try copySubset(&projected, arena, source, "stats", &.{ "packetsSent", "silentPackets", "framesSent", "framesSubmitted", "ringDroppedFrames", "ringDiscardedFrames", "sendErrors", "lateTicks", "bufferedFrames" });
        try copySubset(&projected, arena, source, "network", &.{ "destIp", "port", "payloadType", "framesPerPacket", "ssrc", "ptpGmid", "ptpDomain", "channels", "multicast", "ttl", "flowCount", "flows" });
        var outer = std.json.ObjectMap.init(arena);
        try outer.put("state", std.json.Value{ .string = if (age_ms > 5000) "stale" else "live" });
        try outer.put("ageMs", std.json.Value{ .integer = @intCast(age_ms) });
        try outer.put("report", std.json.Value{ .object = projected });
        return std.json.Value{ .object = outer };
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
            }
            const endpoint = try u.string(u.field(value, "endpoint"));
            if (endpoint.len == 0) return error.EndpointRequired;
            if (playback) |*profile| {
                try profile.resolvePcmInput(playback_owner.?.arena.allocator(), endpoint);
            }
            const frontend = if (u.field(value,"spatialFormat") == .null) "WindowsSonic" else try u.string(u.field(value,"spatialFormat"));
            if (!validFormat(frontend)) return error.InvalidConfig;
            if (playback) |profile| try profile.validateSpatialFormat(frontend);
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
            const ready_name = try u.fmt("Local\\NSP.Ready.{s}", .{ident});
            defer a.free(ready_name);
            const ready_wide = try u.wide(ready_name);
            defer a.free(ready_wide);
            c.SetLastError(0);
            var ready_event: c.HANDLE = c.CreateEventW(null, c.TRUE, c.FALSE, ready_wide.ptr) orelse return error.ReadyEventCreate;
            defer if (ready_event) |event| { _ = c.CloseHandle(event); };
            if (c.GetLastError() == c.ERROR_ALREADY_EXISTS) return error.ReadyEventAlreadyExists;
            const script = try u.join(&.{ self.root, "modules/zig-scheduler/Run-GameTelemetry.ps1" });
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
            try launchArgs.appendSlice(&.{ "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-OutputDirectory", output, "-SpatialFormat", frontend, "-Managed", "-OwnerPid", ownerText, "-Endpoint", endpoint, "-SchedulerExecutable", exe, "-TelemetryPython", self.python, "-TelemetryPort", tport, "-FrontendOrigin", origin, "-ReadyEvent", ready_name });
            if (playback) |settings| {
                // The existing orchestrator requires a new output directory.
                // Keep launch settings beside it until the orchestrator creates it.
                const path=try u.fmt("{s}.playback.json",.{output});
                try u.writeJson(path,settings);
                try launchArgs.appendSlice(&.{"-PlaybackConfig",path});
            }
            if (self.config.fullTiming) try launchArgs.append("-FullTiming");
            self.process = try u.spawnLog(launchArgs.items, log);
            self.ready_event = ready_event;
            ready_event = null;
            self.session.?.orchestratorPid = self.process.?.pid;
            try self.save();
        } else if(std.mem.eql(u8,action,"select-output")) {
            const session=self.session orelse return error.NoOwnedSession;
            if(!std.mem.eql(u8,self.state,"running"))return error.SessionNotRunning;
            if(try u.number(u.field(value,"ownerPid"))!=session.ownerPid or
                !std.mem.eql(u8,try u.string(u.field(value,"sessionId")),session.id))return error.OwnerConflict;
            const profile=session.playback orelse return error.OutputRouteUnavailable;
            if(!std.mem.eql(u8,profile.outputBackend,"aoip") or std.mem.eql(u8,profile.sourcePolicy,"unified"))return error.OutputRouteUnavailable;
            const policy=try u.string(u.field(value,"sourcePolicy"));
            if(!std.mem.eql(u8,policy,"active") and !std.mem.eql(u8,policy,"fixed"))return error.InvalidSourcePolicy;
            const v=u.field(value,"sourceStream");
            const source:?u64=if(v==.null) null else try u.number(v);
            if(source) |i| {if(i>3)return error.InvalidOutputSource;}
            const path=try u.join(&.{session.outputDirectory,"observed","output-route-request.json"});defer a.free(path);
            var revision=c.GetTickCount64();
            if(u.exists(path)){
                var previous=try u.load(path);defer previous.deinit();
                revision=@max(revision,(try u.number(u.field(previous.value,"revision")))+1);
            }
            try u.writeJson(path,.{.revision=revision,.sourcePolicy=policy,.sourceStream=source});
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
            const prior_state = self.state;
            const prior_error = self.err;
            self.state = "stopped";
            self.err = null;
            self.save() catch |err| {
                self.state = prior_state;
                self.err = prior_error;
                if (self.session) |*session| {
                    session.state = prior_state;
                    session.@"error" = prior_error;
                }
                return err;
            };
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

/// Status projection helpers for the AES67 observation: allow-listed fields are
/// copied into a fresh object so absolute paths (network.sdp) and internal
/// report fields never leak through the web status API.
fn observedState(arena: std.mem.Allocator, state: []const u8, age_ms: ?u64) !std.json.Value {
    var map = std.json.ObjectMap.init(arena);
    try map.put("state", std.json.Value{ .string = state });
    const age_value: std.json.Value = if (age_ms) |value| std.json.Value{ .integer = @intCast(value) } else .null;
    try map.put("ageMs", age_value);
    try map.put("report", .null);
    return std.json.Value{ .object = map };
}

fn copyFields(target: *std.json.ObjectMap, source: std.json.ObjectMap, keys: []const []const u8) !void {
    for (keys) |key| if (source.get(key)) |value| try target.put(key, value);
}

fn copySubset(target: *std.json.ObjectMap, arena: std.mem.Allocator, source: std.json.ObjectMap, key: []const u8, allowed: []const []const u8) !void {
    const value = source.get(key) orelse return;
    if (value != .object) return try target.put(key, value);
    var subset = std.json.ObjectMap.init(arena);
    for (allowed) |name| if (value.object.get(name)) |item| try subset.put(name, item);
    try target.put(key, std.json.Value{ .object = subset });
}

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
    const base = if (std.mem.startsWith(u8, path, "/contracts/")) "contracts" else if (std.mem.startsWith(u8, path, "/replay/")) "build/wwise-visualization-replay" else "build/nsp-product/web";
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
            try sendResponse(stream, 404, "application/json", "{\"error\":\"not-found\"}");
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
    defer if (controller.ready_event) |event| { _ = c.CloseHandle(event); };
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


test "replacement backend retires an orphan and repeated recovery has consistent status" {
    var temp = std.testing.tmpDir(.{});
    defer temp.cleanup();
    const home = try temp.dir.realpathAlloc(a, ".");
    defer a.free(home);
    try temp.dir.makePath("observed");
    const dead: u32 = 0xfffffffc;
    const session_path = try u.join(&.{home, "session.json"});
    defer a.free(session_path);
    try u.writeJson(session_path, .{.id="reboot", .outputDirectory=home, .ownerPid=dead, .orchestratorPid=dead, .endpoint="fixture", .state="running"});
    const marker = try u.join(&.{home, "observed/hook-session.json"});
    defer a.free(marker);
    try u.writeJson(marker, .{.hookGeneration=2, .pid=dead, .created=@as(u64,1)});
    const config_path = try u.join(&.{home, "observed/hook-config-test.bin"});
    defer a.free(config_path);
    var config = std.mem.zeroes(c.HookConfig);
    config.magic = c.NSP_HOOK_MAGIC;
    config.size = @sizeOf(c.HookConfig);
    config.host_pid = dead;
    config.target_created = 1;
    try u.write(config_path, std.mem.asBytes(&config));
    const runtime = try u.join(&.{home, "runtime.json"});
    defer a.free(runtime);
    try u.writeJson(runtime, .{.pid=c.GetCurrentProcessId(), .scheduler="zig"});
    var controller = try Controller.init(home,home,"unused",0,0);
    const Audio = struct {
        fn absent() anyerror!u32 { return error.NoAudioHost; }
    };
    controller.audioPid = Audio.absent;
    try std.testing.expectEqualStrings("recovery-required", controller.state);
    var empty = try u.parse("{}");
    defer empty.deinit();
    // Stop has no owned child after a restart. Recovery must still work.
    try std.testing.expectError(error.OwnershipUnknown, controller.command("stop-session",empty.value));
    config.host_pid = c.GetCurrentProcessId();
    try u.write(config_path, std.mem.asBytes(&config));
    try std.testing.expectError(error.StopEvidenceRequired, controller.command("recover",empty.value));
    config.host_pid = dead;
    try u.write(config_path, std.mem.asBytes(&config));
    var wrong = try u.parse("{\"ownerPid\":1,\"sessionId\":\"other\"}");
    defer wrong.deinit();
    try std.testing.expectError(error.OwnerConflict,controller.command("recover",wrong.value));
    const started = c.GetTickCount64();
    const recovered = try controller.command("recover",empty.value);
    defer a.free(recovered);
    var readback = try u.parse(recovered);
    defer readback.deinit();
    try std.testing.expectEqualStrings("stopped",try u.string(u.field(readback.value,"sessionState")));
    try std.testing.expect(u.field(readback.value,"error") == .null);
    const repeated = try controller.command("recover",empty.value);
    defer a.free(repeated);
    const status = try controller.command("status",empty.value);
    defer a.free(status);
    var observed = try u.parse(status);
    defer observed.deinit();
    try std.testing.expectEqualStrings("stopped",try u.string(u.field(observed.value,"sessionState")));
    var saved = try u.load(session_path);
    defer saved.deinit();
    try std.testing.expectEqualStrings("stopped",try u.string(u.field(saved.value,"state")));
    const report = try u.join(&.{home,"endpoint.json"});
    defer a.free(report);
    try std.testing.expect(!u.exists(report));
    const restarted = try Controller.init(home,home,"unused",0,0);
    try std.testing.expectEqualStrings("idle",restarted.state);
    std.debug.print("orphan recovery and repeated state readback: {d} ms\n", .{c.GetTickCount64()-started});

    // Exercise the actual control adapter against a replacement HTTP backend.
    // The owned fixture supplies audio-host absence without touching real audio.
    const lock_path = try u.join(&.{home,"owner.lock"});
    defer a.free(lock_path);
    const wide = try u.wide(lock_path);
    defer a.free(wide);
    const lock = c.CreateFileW(wide.ptr,c.GENERIC_READ | c.GENERIC_WRITE,0,null,c.OPEN_ALWAYS,c.FILE_ATTRIBUTE_NORMAL,null);
    try std.testing.expect(lock != c.INVALID_HANDLE_VALUE);
    defer _ = c.CloseHandle(lock);
    var server = try (try std.net.Address.parseIp4("127.0.0.1",0)).listen(.{.force_nonblocking=true});
    defer server.deinit();
    try u.writeJson(runtime,.{.pid=c.GetCurrentProcessId(),.scheduler="zig",.port=server.listen_address.getPort(),.token="owned-test"});
    const Server = struct {
        fn run(listener: *std.net.Server, manager: *Controller, stop: *std.atomic.Value(bool)) void {
            while(!stop.load(.acquire)) {
                const connection = listener.accept() catch { c.Sleep(1); continue; };
                defer connection.stream.close();
                _ = handle(manager,connection.stream,"owned-test") catch {};
            }
        }
    };
    for ([_][]const u8{"status","stop","emergency-stop","start"}) |mode| {
        controller.state="recovery-required";
        controller.err="Previous session requires confirmed stop and endpoint restore";
        try controller.save();
        var stop = std.atomic.Value(bool).init(false);
        const thread = try std.Thread.spawn(.{},Server.run,.{&server,&controller,&stop});
        defer { stop.store(true,.release); thread.join(); }
        const before = std.time.nanoTimestamp();
        if (std.mem.eql(u8,mode,"start")) {
            // An invalid new format stops before audio effects, after recovery.
            try std.testing.expectEqual(@as(u8,20),try @import("control.zig").run(&.{"scheduler",mode,"--owner-pid","1","--endpoint","fixture","--spatial-format","InvalidFormat"},home,home));
            const failure_path = try u.join(&.{home,"last-error.json"});
            defer a.free(failure_path);
            var failure = try u.load(failure_path);
            defer failure.deinit();
            try std.testing.expectEqualStrings("InvalidConfig",try u.string(u.field(failure.value,"error")));
        } else {
            try std.testing.expectEqual(@as(u8,10),try @import("control.zig").run(&.{"scheduler",mode,"--request-id","owned-recovery"},home,home));
        }
        for (0..3) |_| try std.testing.expectEqual(@as(u8,10),try @import("control.zig").run(&.{"scheduler","status"},home,home));
        const latest = try request(home,"status",struct{}{});
        defer a.free(latest);
        var checked = try u.parse(latest);
        defer checked.deinit();
        try std.testing.expectEqualStrings("stopped",try u.string(u.field(checked.value,"sessionState")));
        try std.testing.expect(u.field(checked.value,"error") == .null);
        var disk = try u.load(session_path);
        defer disk.deinit();
        try std.testing.expectEqualStrings("stopped",try u.string(u.field(disk.value,"state")));
        std.debug.print("{s} recovery + 3 adapter reads + backend read: {d} us\n", .{mode,@divTrunc(std.time.nanoTimestamp()-before,1000)});
    }
}
