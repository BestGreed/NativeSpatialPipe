const std = @import("std");
const u = @import("common.zig");
const backend = @import("backend.zig");
const n = @import("native.zig");
const endpoint_retirement = @import("endpoint_retirement.zig");
const endpoint_presence = @import("endpoint_presence.zig");
const a = u.a;
const c = u.c;
// Anonymous .{} is an empty tuple and encodes as [], not the HTTP API's {}.
const empty_request = struct {}{};
pub fn ensure(args: []const []const u8, root: []const u8, home: []const u8) !void {
    const status = backend.request(home, "status", empty_request) catch null;
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
        const reply = backend.request(home, "status", empty_request) catch {
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
    if ((try u.inspectProcessIdentity(pid)) != created) return u.parse("{\"state\":2,\"targetExited\":true}");
    try u.debugPrivilege();
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
    const reply = backend.request(home, "status", empty_request) catch null;
    defer if (reply) |bytes| a.free(bytes);
    var status = if (reply) |bytes| try u.parse(bytes) else null;
    defer if (status) |*value| value.deinit();
    const state = if (status) |value| try u.string(u.field(value.value, "sessionState")) else "unreachable";
    if (!u.exists(marker)) {
        if (std.mem.eql(u8, state, "starting") or std.mem.eql(u8, state, "stopping")) return 11;
        if (!std.mem.eql(u8, state, "idle") and !std.mem.eql(u8, state, "stopped") and
            (try @import("stop_proof.zig").unmarked(root, home, session.value, output))) return 10;
        return if (std.mem.eql(u8, state, "idle") or std.mem.eql(u8, state, "stopped")) 10 else 20;
    }
    var actual = try inspect(root, home, output, "status");
    defer actual.deinit();
    const v = actual.value;
    if ((u.number(u.field(v, "state")) catch 0) == 1 and u.yes(u.field(v, "hookEnabled"))) return if (std.mem.eql(u8, state, "running")) 0 else if (std.mem.eql(u8, state, "starting") or std.mem.eql(u8, state, "stopping")) 11 else 20;
    const off = u.yes(u.field(v, "targetExited")) or u.yes(u.field(v, "baselineVerified")) or ((u.number(u.field(v, "state")) catch 0) == 2 and u.yes(u.field(v, "hookBytesRestored")));
    if (off) {
        const recovering = std.mem.eql(u8, state, "faulted") or std.mem.eql(u8, state, "recovery-required");
        if (recovering) {
            // The serving controller owns the recovery transaction and updates
            // its in-memory state together with session.json.
            const recovered = backend.request(home, "recover", .{
                .ownerPid = try u.number(u.field(session.value, "ownerPid")),
                .sessionId = try u.string(u.field(session.value, "id")),
            }) catch null;
            if (recovered) |bytes| {
                defer a.free(bytes);
                var readback = try u.parse(bytes);
                defer readback.deinit();
                if (std.mem.eql(u8, u.string(u.field(readback.value, "sessionState")) catch "", "stopped")) return 10;
            }
            const endpoint = u.string(u.field(session.value, "endpoint")) catch "";
            const absent = endpoint_presence.absent(endpoint) catch false;
            if (!absent) reconfirmEndpoint(root, home, session.value, output) catch {};
            _ = endpoint_retirement.observe(output, session.value, c.GetTickCount64(), absent, endpoint_presence.absent) catch false;
        }
        // Session validation failure is not the same as a live Hook. Release
        // UI/mode interlocks only with current Hook-off AND completed teardown.
        if (try teardownComplete(session.value, output) or (recovering and endpoint_retirement.proven(output, session.value))) {
            if (recovering) {
                const recovered = try backend.request(home, "recover", empty_request);
                a.free(recovered);
            }
            return 10;
        }
        if (std.mem.eql(u8, state, "idle") or std.mem.eql(u8, state, "stopped")) return 10;
        if (std.mem.eql(u8, state, "starting") or std.mem.eql(u8, state, "stopping")) return 11;
        const orphan_proof = try u.join(&.{ output, "orphan-recovery.json" });
        defer a.free(orphan_proof);
        if (std.mem.eql(u8, state, "unreachable") and u.exists(orphan_proof) and
            std.mem.eql(u8, u.string(u.field(session.value, "state")) catch "", "stopped") and
            (retireOrphan(root, home) catch false)) return 10;
        const userStop = try u.fmt("{s}.user-stop", .{output});
        defer a.free(userStop);
        if (u.exists(userStop)) {
            const recovered = backend.request(home, "recover", empty_request) catch return 20;
            a.free(recovered);
            return 10;
        }
    }
    return 20;
}
// Sleep can invalidate an endpoint temporarily while capture shuts down cleanly.
// Reconfirm against the original snapshot; never turn a failed restore into a
// success unless current native Hook baseline and endpoint queries both agree.
fn reconfirmEndpoint(root: []const u8, home: []const u8, session: u.Value, output: []const u8) !void {
    const owner = std.math.cast(u32, u.number(u.field(session, "orchestratorPid")) catch return) orelse return;
    if (owner == 0 or try u.inspectProcessIdentity(owner) != null) return;
    const report_path = try u.join(&.{ output, "endpoint.json" });
    defer a.free(report_path);
    var report = try u.load(report_path);
    defer report.deinit();
    if (u.yes(u.field(report.value, "restored")) or !u.yes(u.field(report.value, "hookCompleted")) or
        (u.number(u.field(report.value, "hookExitCode")) catch 255) != 0) return;
    const attempt_path = try u.join(&.{ output, "endpoint-reconfirmation-attempt.json" });
    defer a.free(attempt_path);
    if (u.exists(attempt_path)) {
        var previous = try u.load(attempt_path);
        defer previous.deinit();
        const now: u64 = @intCast(@max(0, std.time.timestamp()));
        if (now -| (u.number(u.field(previous.value, "checkedAt")) catch 0) < 15) return;
    }
    try u.writeJson(attempt_path, .{ .checkedAt = std.time.timestamp() });
    const helper = try u.join(&.{ root, "tools/zig-scheduler/Confirm-EndpointRestore.ps1" });
    defer a.free(helper);
    const session_path = try u.join(&.{ home, "session.json" });
    defer a.free(session_path);
    const executable = try std.fs.selfExePathAlloc(a);
    defer a.free(executable);
    const result = try u.run(&.{ "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, "-SessionFile", session_path, "-Scheduler", executable, "-Root", root });
    a.free(result);
}
fn teardownComplete(session: u.Value, output: []const u8) !bool {
    const owner = u.number(u.field(session, "orchestratorPid")) catch return false;
    const pid = std.math.cast(u32, owner) orelse return false;
    if (pid == 0 or try u.processIdentity(pid) != null) return false;
    const path = try u.join(&.{ output, "endpoint.json" });
    defer a.free(path);
    var report = u.load(path) catch return false;
    defer report.deinit();
    return u.yes(u.field(report.value, "restored")) and u.yes(u.field(report.value, "hookCompleted"));
}
const orphan = @import("orphan_recovery.zig");
const orphanRetirable = orphan.retirable;
fn retireOrphan(root: []const u8, home: []const u8) !bool {
    return retireOrphanWithAudio(root, home, u.audioPid);
}
fn retireOrphanWithAudio(root: []const u8, home: []const u8, audio_pid: *const fn () anyerror!u32) !bool {
    const lock_path = try u.join(&.{ home, "owner.lock" });
    defer a.free(lock_path);
    const wide = try u.wide(lock_path);
    defer a.free(wide);
    const lock = c.CreateFileW(wide.ptr, c.GENERIC_READ | c.GENERIC_WRITE, 0, null, c.OPEN_ALWAYS, c.FILE_ATTRIBUTE_NORMAL, null);
    if (lock == c.INVALID_HANDLE_VALUE) return false;
    defer _ = c.CloseHandle(lock);
    const path = try u.join(&.{ home, "session.json" });
    defer a.free(path);
    var session = try u.load(path);
    defer session.deinit();
    if (!try orphan.proveWithAudio(root, home, session.value, audio_pid)) return false;
    try session.value.object.put("state", .{ .string = "stopped" });
    try session.value.object.put("error", .null);
    try u.writeJson(path, session.value);
    return true;
}
fn action(args: []const []const u8, root: []const u8, home: []const u8) !u8 {
    const command = args[1];
    if (std.mem.eql(u8, command, "preflight")) {
        const pid = try u.audioPid();
        _ = try u.checkTarget(pid);
        _ = try u.checkModules(pid);
        for ([_][]const u8{ "build/native-hook-managed-v2/nsp_hook_control.exe", "build/native-hook-managed-v2/nsp_managed_hook_v2.dll", "build/native-send/nsp_native_send.dll", "build/capture-scheduler-mmcss/nsp_native_capture.dll", "build/nsp-product/receiver/release/nsp_native_receiver.dll", n.default_render_bridge }) |relative| {
            const path = try u.join(&.{ root, relative });
            defer a.free(path);
            if (!u.exists(path)) return error.MissingRuntimeDependency;
        }
        // The host resolves the current profile before proving the live Hook
        // baseline. FSA must be able to reach that step with an empty cache.
        return 12;
    }
    if (std.mem.eql(u8, command, "start")) {
        const owner = try std.fmt.parseInt(u32, u.option(args, "--owner-pid") orelse return error.OwnerRequired, 10);
        const endpoint = u.option(args, "--endpoint") orelse return error.EndpointRequired;
        const Playback=@import("desktop_playback.zig").Config;
        var playback:?std.json.Parsed(Playback)=null;
        defer if(playback) |*settings| settings.deinit();
        if(u.option(args,"--playback-config")) |path| {
            if(!std.fs.path.isAbsolute(path))return error.PlaybackProfileRequiresAbsolutePath;
            const bytes=try u.read(path);defer a.free(bytes);
            playback=try @import("desktop_playback.zig").parse(a,bytes);
        }
        try ensure(args, root, home);
        const prior = try backend.request(home, "status", empty_request);
        defer a.free(prior);
        var prior_status = try u.parse(prior);
        defer prior_status.deinit();
        const prior_state = try u.string(u.field(prior_status.value, "sessionState"));
        if (std.mem.eql(u8, prior_state, "recovery-required") or std.mem.eql(u8, prior_state, "faulted")) {
            // A user-requested start may acknowledge a fully retired session,
            // but cannot bypass current Hook-off and teardown verification.
            if (try observe(root, home) != 10) return error.StopEvidenceRequired;
            const recovered = try backend.request(home, "recover", empty_request);
            a.free(recovered);
        }
        const frontend = u.option(args,"--spatial-format") orelse "WindowsSonic";
        if (!backend.validFormat(frontend)) return error.InvalidConfig;
        const reply = try backend.request(home, "start-session", .{ .ownerPid = owner, .endpoint = endpoint, .spatialFormat = frontend, .playback = if(playback) |settings| @as(?Playback,settings.value) else null });
        a.free(reply);
        return 11;
    }
    if (std.mem.eql(u8, command, "stop") or std.mem.eql(u8, command, "emergency-stop") or std.mem.eql(u8, command, "shutdown")) {
        const path = try u.join(&.{ home, "session.json" });
        defer a.free(path);
        if (!u.exists(path)) {
            if (std.mem.eql(u8, command, "shutdown")) {
                const exiting = backend.request(home, "exit", empty_request) catch null;
                if (exiting) |bytes| a.free(bytes);
            }
            return 10;
        }
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
            const hook_marker = try u.join(&.{ output, "observed/hook-session.json" });
            defer a.free(hook_marker);
            if (u.exists(hook_marker)) {
                var actual = try inspect(root, home, output, "status");
                defer actual.deinit();
                if ((u.number(u.field(actual.value, "state")) catch 0) == 1) {
                    var result = try inspect(root, home, output, "stop");
                    result.deinit();
                }
            } else if (!try @import("stop_proof.zig").unmarked(root, home, session.value, output)) {
                return error.StopEvidenceRequired;
            }
        }
        const result = backend.request(home, "stop-session", empty_request) catch null;
        if (result) |reply| a.free(reply);
        var code = try observe(root, home);
        if (code == 20 and (retireOrphan(root, home) catch false)) code = 10;
        if (code == 10 and std.mem.eql(u8, command, "shutdown")) {
            // Best-effort normal manager exit. Never force-kill an unresponsive
            // manager; verified audio teardown is sufficient to release FSA.
            const recovery = backend.request(home, "recover", empty_request) catch null;
            if (recovery) |bytes| a.free(bytes);
            const exiting = backend.request(home, "exit", empty_request) catch null;
            if (exiting) |bytes| a.free(bytes);
        }
        return code;
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

test "orphan retirement ignores replacement runtime and requires dead session processes" {
    var temp = std.testing.tmpDir(.{});
    defer temp.cleanup();
    const home = try temp.dir.realpathAlloc(a, ".");
    defer a.free(home);
    try temp.dir.makePath("observed");
    const dead: u32 = 0xfffffffc;
    const live = c.GetCurrentProcessId();
    var session = try u.parse("{\"orchestratorPid\":4294967292}");
    defer session.deinit();
    const marker = try u.join(&.{ home, "observed/hook-session.json" });
    defer a.free(marker);
    const config_path = try u.join(&.{ home, "observed/hook-config-test.bin" });
    defer a.free(config_path);
    const runtime = try u.join(&.{ home, "runtime.json" });
    defer a.free(runtime);
    const telemetry = try u.join(&.{ home, "observed/telemetry-service.json" });
    defer a.free(telemetry);
    try u.writeJson(marker, .{ .hookGeneration = 2, .pid = dead, .created = @as(u64, 1) });
    // Missing host identity cannot establish that teardown has finished.
    try std.testing.expect(!try orphanRetirable(home, session.value, home));
    var config = std.mem.zeroes(c.HookConfig);
    config.magic = c.NSP_HOOK_MAGIC;
    config.size = @sizeOf(c.HookConfig);
    config.host_pid = dead;
    config.target_created = 1;
    try u.write(config_path, std.mem.asBytes(&config));
    try std.testing.expect(try orphanRetirable(home, session.value, home));
    try session.value.object.put("orchestratorPid", .{ .integer = live });
    try std.testing.expect(!try orphanRetirable(home, session.value, home));
    try session.value.object.put("orchestratorPid", .{ .integer = dead });
    try u.writeJson(runtime, .{ .pid = live });
    try std.testing.expect(try orphanRetirable(home, session.value, home));
    try u.writeJson(runtime, .{ .pid = dead });
    config.host_pid = live;
    try u.write(config_path, std.mem.asBytes(&config));
    try std.testing.expect(!try orphanRetirable(home, session.value, home));
    config.host_pid = dead;
    try u.write(config_path, std.mem.asBytes(&config));
    try u.writeJson(telemetry, .{ .pid = live });
    try std.testing.expect(!try orphanRetirable(home, session.value, home));
    try u.writeJson(telemetry, .{ .pid = dead });
    try u.writeJson(marker, .{ .hookGeneration = 2, .pid = live, .created = (try u.processIdentity(live)).? });
    try std.testing.expect(!try orphanRetirable(home, session.value, home));
    try u.writeJson(marker, .{ .hookGeneration = 2, .pid = dead, .created = @as(u64, 1) });
    try std.testing.expect(try orphanRetirable(home, session.value, home));
    config.target_created = 2;
    try u.write(config_path, std.mem.asBytes(&config));
    try std.testing.expect(!try orphanRetirable(home, session.value, home));
    config.target_created = 1;
    try u.write(config_path, std.mem.asBytes(&config));
    try session.value.object.put("id", .{ .string = "orphan-test" });
    try session.value.object.put("outputDirectory", .{ .string = home });
    try session.value.object.put("state", .{ .string = "running" });
    const session_path = try u.join(&.{ home, "session.json" });
    defer a.free(session_path);
    try u.writeJson(session_path, session.value);
    const Audio = struct {
        var calls: usize = 0;
        fn absent() anyerror!u32 { return error.NoAudioHost; }
        fn inventory_error() anyerror!u32 { return error.ProcessInventory; }
        fn multiple() anyerror!u32 { return error.MultipleAudioHosts; }
        fn appears() anyerror!u32 {
            calls += 1;
            if (calls == 1) return error.NoAudioHost;
            return 1234;
        }
    };
    try std.testing.expectError(error.ProcessInventory, retireOrphanWithAudio(home, home, Audio.inventory_error));
    try std.testing.expectError(error.MultipleAudioHosts, retireOrphanWithAudio(home, home, Audio.multiple));
    try std.testing.expect(!try retireOrphanWithAudio(home, home, Audio.appears));
    // Full retirement and subsequent status revalidation must both work when
    // Windows has no audio host. No native Hook API is called in this case.
    try std.testing.expect(try retireOrphanWithAudio(home, home, Audio.absent));
    try std.testing.expect(try retireOrphanWithAudio(home, home, Audio.absent));
    var saved = try u.load(session_path);
    defer saved.deinit();
    try std.testing.expectEqualStrings("stopped", try u.string(u.field(saved.value, "state")));
    const proof_path = try u.join(&.{ home, "orphan-recovery.json" });
    defer a.free(proof_path);
    var proof = try u.load(proof_path);
    defer proof.deinit();
    try std.testing.expect(u.yes(u.field(proof.value, "audioHostAbsent")));
    try std.testing.expect(!u.yes(u.field(proof.value, "baselineVerified")));
}
