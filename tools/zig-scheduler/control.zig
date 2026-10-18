const std = @import("std");
const u = @import("common.zig");
const backend = @import("backend.zig");
const n = @import("native.zig");
const a = u.a;
const c = u.c;
pub fn ensure(args: []const []const u8, root: []const u8, home: []const u8) !void {
    const status = backend.request(home, "status", .{}) catch null;
    if (status) |bytes| {
        a.free(bytes);
        return;
    }
    const runtime = try u.join(&.{ home, "runtime.json" });
    defer a.free(runtime);
    // Never silently take over an existing Python host or another backend.
    if (u.exists(runtime)) {
        var previous = try u.load(runtime);
        defer previous.deinit();
        const pid: u32 = @intCast(try u.number(u.field(previous.value, "pid")));
        if (try u.processIdentity(pid) != null) return error.ExistingBackendMustBeStoppedExplicitly;
    }
    try std.fs.cwd().makePath(home);
    const exe = try std.fs.selfExePathAlloc(a);
    defer a.free(exe);
    const log = try u.join(&.{ home, "backend.log" });
    defer a.free(log);
    var child = try u.spawnLog(&.{ exe, "serve", "--root", root, "--home", home, "--port", u.option(args, "--port") orelse "8765", "--telemetry-port", u.option(args, "--telemetry-port") orelse "8766", "--python", u.option(args, "--python") orelse "python.exe" }, log);
    defer child.close();
    const deadline = c.GetTickCount64() + 10000;
    while (c.GetTickCount64() < deadline) {
        if (try u.done(&child) != null) return error.BackendStart;
        const reply = backend.request(home, "status", .{}) catch {
            c.Sleep(50);
            continue;
        };
        defer a.free(reply);
        var parsed = try u.parse(reply);
        defer parsed.deinit();
        if ((try u.number(u.field(parsed.value, "pid"))) == child.pid) return;
    }
    return error.BackendTimeout;
}
fn inspect(root: []const u8, home: []const u8, output: []const u8, operation: []const u8) !std.json.Parsed(u.Value) {
    const path = try u.join(&.{ output, "observed/hook-session.json" });
    defer a.free(path);
    var marker = try u.load(path);
    defer marker.deinit();
    if ((try u.number(u.field(marker.value, "hookGeneration"))) != 2) return error.LegacyHookNeedsPythonRecovery;
    const pid: u32 = @intCast(try u.number(u.field(marker.value, "pid")));
    const created = try u.number(u.field(marker.value, "created"));
    try u.debugPrivilege();
    if ((try u.processIdentity(pid)) != created) return u.parse("{\"state\":2,\"targetExited\":true}");
    var hook = try n.Hook.init(root, home, pid, 0, created, false);
    defer {
        u.remove(hook.path);
        a.free(hook.path);
    }
    var actual = try hook.call(operation);
    if (std.mem.eql(u8, operation, "status") and (u.number(u.field(actual.value, "state")) catch 0) == 0) {
        actual.deinit();
        return hook.call("baseline");
    }
    return actual;
}
pub fn observe(root: []const u8, home: []const u8) !u8 {
    const path = try u.join(&.{ home, "session.json" });
    defer a.free(path);
    if (!u.exists(path)) return 10;
    var session = try u.load(path);
    defer session.deinit();
    const output = try u.string(u.field(session.value, "outputDirectory"));
    const marker = try u.join(&.{ output, "observed/hook-session.json" });
    defer a.free(marker);
    const reply = try backend.request(home, "status", .{});
    defer a.free(reply);
    var status = try u.parse(reply);
    defer status.deinit();
    const state = try u.string(u.field(status.value, "sessionState"));
    if (!u.exists(marker)) {
        if (std.mem.eql(u8, state, "starting") or std.mem.eql(u8, state, "stopping")) return 11;
        return if (std.mem.eql(u8, state, "idle") or std.mem.eql(u8, state, "stopped")) 10 else 20;
    }
    var actual = try inspect(root, home, output, "status");
    defer actual.deinit();
    const v = actual.value;
    if ((u.number(u.field(v, "state")) catch 0) == 1 and u.yes(u.field(v, "hookEnabled"))) return if (std.mem.eql(u8, state, "running")) 0 else if (std.mem.eql(u8, state, "starting") or std.mem.eql(u8, state, "stopping")) 11 else 20;
    const off = u.yes(u.field(v, "targetExited")) or u.yes(u.field(v, "baselineVerified")) or ((u.number(u.field(v, "state")) catch 0) == 2 and u.yes(u.field(v, "hookBytesRestored")));
    if (off) {
        if (std.mem.eql(u8, state, "idle") or std.mem.eql(u8, state, "stopped")) return 10;
        if (std.mem.eql(u8, state, "starting") or std.mem.eql(u8, state, "stopping")) return 11;
        const userStop = try u.fmt("{s}.user-stop", .{output});
        defer a.free(userStop);
        if (u.exists(userStop)) {
            const recovered = backend.request(home, "recover", .{}) catch return 20;
            a.free(recovered);
            return 10;
        }
    }
    return 20;
}
fn action(args: []const []const u8, root: []const u8, home: []const u8) !u8 {
    const command = args[1];
    if (std.mem.eql(u8, command, "preflight")) {
        const pid = try u.audioPid();
        const created = try u.checkTarget(pid);
        _ = try u.checkModules(pid);
        for ([_][]const u8{ "build/native-hook-managed-v2/nsp_hook_control.exe", "build/native-hook-managed-v2/nsp_managed_hook_v2.dll", "build/native-send/nsp_native_send.dll", "build/capture-scheduler-mmcss/nsp_native_capture.dll", "build/native-receiver-routing/release/nsp_native_receiver.dll", "build/telemetry-objects/release/nsp_roar_bridge.dll" }) |relative| {
            const path = try u.join(&.{ root, relative });
            defer a.free(path);
            if (!u.exists(path)) return error.MissingRuntimeDependency;
        }
        var hook = try n.Hook.init(root, home, pid, 0, created, false);
        defer {
            u.remove(hook.path);
            a.free(hook.path);
        }
        var baseline = try hook.call("baseline");
        defer baseline.deinit();
        if (!u.yes(u.field(baseline.value, "baselineVerified"))) return error.BaselineNotVerified;
        return 12;
    }
    if (std.mem.eql(u8, command, "start")) {
        const owner = try std.fmt.parseInt(u32, u.option(args, "--owner-pid") orelse return error.OwnerRequired, 10);
        const endpoint = u.option(args, "--endpoint") orelse return error.EndpointRequired;
        try ensure(args, root, home);
        const reply = try backend.request(home, "start-session", .{ .ownerPid = owner, .endpoint = endpoint });
        a.free(reply);
        return 11;
    }
    if (std.mem.eql(u8, command, "stop") or std.mem.eql(u8, command, "emergency-stop")) {
        const path = try u.join(&.{ home, "session.json" });
        defer a.free(path);
        if (!u.exists(path)) return 10;
        var session = try u.load(path);
        defer session.deinit();
        const output = try u.string(u.field(session.value, "outputDirectory"));
        const stop = try u.fmt("{s}.stop", .{output});
        defer a.free(stop);
        try u.write(stop, "stop");
        const userStop = try u.fmt("{s}.user-stop", .{output});
        defer a.free(userStop);
        try u.write(userStop, "stop");
        if (std.mem.eql(u8, command, "emergency-stop")) {
            var actual = try inspect(root, home, output, "status");
            defer actual.deinit();
            if ((u.number(u.field(actual.value, "state")) catch 0) == 1) {
                var result = try inspect(root, home, output, "stop");
                result.deinit();
            }
        }
        const result = backend.request(home, "stop-session", .{}) catch null;
        if (result) |reply| a.free(reply);
        return observe(root, home);
    }
    if (std.mem.eql(u8, command, "status")) return observe(root, home);
    return error.UnsupportedCommand;
}
pub fn run(args: []const []const u8, root: []const u8, home: []const u8) !u8 {
    var failure: ?[]const u8 = null;
    const code = action(args, root, home) catch |err| blk: {
        failure = @errorName(err);
        break :blk @as(u8, 20);
    };
    const checked = std.time.timestamp();
    const marker = try u.join(&.{ home, "hook-status.json" });
    defer a.free(marker);
    const value = .{ .code = code, .checkedAt = checked, .action = args[1], .scheduler = "zig", .@"error" = failure };
    try u.writeJson(marker, value);
    if (code == 20) {
        const path = try u.join(&.{ home, "last-error.json" });
        defer a.free(path);
        try u.writeJson(path, value);
    }
    const ident = u.option(args, "--request-id") orelse "manual";
    if (std.mem.indexOfAny(u8, ident, "\r\n") != null) return error.InvalidRequestId;
    const text = try u.fmt("[NSP]\nCode={d}\nCheckedAt={d}\nRequestId={s}\n", .{ code, checked, ident });
    defer a.free(text);
    const disk = try u.join(&.{ home, "hook-status.ini" });
    defer a.free(disk);
    try u.write(disk, text);
    const bytes = try u.json(value);
    defer a.free(bytes);
    try std.io.getStdOut().writer().print("{s}\n", .{bytes});
    return code;
}
