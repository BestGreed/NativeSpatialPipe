//! Recovery of a failed startup with no Hook identity marker. Absence is never
//! evidence: require exited owners, completed restore AND a fresh live baseline.
const std = @import("std");
const u = @import("common.zig");
const n = @import("native.zig");
const Baseline = struct { pid: u32, created: u64 };
fn baseline(root: []const u8, home: []const u8) !?Baseline {
    // Inventory failure/multiple hosts/absence stay fail-closed in this path.
    const pid = try u.audioPid();
    const created = try u.checkTarget(pid);
    _ = try u.checkModules(pid);
    var hook = try n.Hook.init(root, home, pid, 0, created, false);
    defer { u.remove(hook.path); u.a.free(hook.path); }
    var actual = try hook.call("baseline");
    defer actual.deinit();
    if (!u.yes(u.field(actual.value, "baselineVerified"))) return null;
    if ((try u.inspectProcessIdentity(pid)) != created) return null;
    return .{ .pid = pid, .created = created };
}
pub fn unmarked(root: []const u8, home: []const u8, session: u.Value, output: []const u8) !bool {
    return check(root, home, session, output, baseline);
}
fn check(root: []const u8, home: []const u8, session: u.Value, output: []const u8, verify: *const fn([]const u8, []const u8) anyerror!?Baseline) !bool {
    const marker = try u.join(&.{ output, "observed/hook-session.json" });
    defer u.a.free(marker);
    if (u.exists(marker)) return false; // Marked sessions retain the original path.
    const manager = std.math.cast(u32, u.number(u.field(session, "orchestratorPid")) catch return false) orelse return false;
    if (manager == 0 or try u.processIdentity(manager) != null) return false;
    const path = try u.join(&.{ output, "endpoint.json" });
    defer u.a.free(path);
    var endpoint = u.load(path) catch return false;
    defer endpoint.deinit();
    if (!u.yes(u.field(endpoint.value, "restored")) or !u.yes(u.field(endpoint.value, "hookCompleted"))) return false;
    const host = std.math.cast(u32, u.number(u.field(endpoint.value, "hostPid")) catch return false) orelse return false;
    if (host == 0 or try u.processIdentity(host) != null) return false;
    const telemetry_path = try u.join(&.{ output, "observed/telemetry-service.json" });
    defer u.a.free(telemetry_path);
    if (u.exists(telemetry_path)) {
        var telemetry = try u.load(telemetry_path);
        defer telemetry.deinit();
        const telemetry_pid = std.math.cast(u32, try u.number(u.field(telemetry.value, "pid"))) orelse return false;
        if (telemetry_pid == 0 or try u.processIdentity(telemetry_pid) != null) return false;
    }
    const current = (try verify(root, home)) orelse return false;
    const proof = try u.join(&.{ output, "startup-recovery.json" });
    defer u.a.free(proof);
    try u.writeJson(proof, .{ .checkedAt = std.time.timestamp(), .sessionId = try u.string(u.field(session, "id")),
        .oldHostPid = host, .oldOrchestratorPid = manager, .oldProcessesExited = true, .endpointRestored = true,
        .currentTargetPid = current.pid, .currentTargetCreated = current.created, .baselineVerified = true,
        .reason = "Unmarked startup failure retired after fresh live baseline; original failure retained" });
    return true;
}

var calls: u32 = 0;
fn accept(_: []const u8, _: []const u8) !?Baseline { calls += 1; return .{ .pid = 42, .created = 77 }; }
fn reject(_: []const u8, _: []const u8) !?Baseline { calls += 1; return null; }
test "unmarked failure requires dead manager and host, restored endpoint and fresh baseline" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const output = try tmp.dir.realpathAlloc(u.a, ".");
    defer u.a.free(output);
    var child = try u.spawn(&.{ "C:\\Windows\\System32\\cmd.exe", "/c", "exit", "0" });
    defer child.close();
    _ = u.c.WaitForSingleObject(child.handle, 5000);
    try std.testing.expect((try u.done(&child)) != null);
    const bytes = try u.json(.{ .id = "fixture", .orchestratorPid = child.pid });
    defer u.a.free(bytes);
    var session = try u.parse(bytes);
    defer session.deinit();
    const path = try u.join(&.{ output, "endpoint.json" });
    defer u.a.free(path);
    calls = 0;
    try std.testing.expect(!try check("", "", session.value, output, accept));
    try u.writeJson(path, .{ .hostPid = child.pid, .restored = false, .hookCompleted = true });
    try std.testing.expect(!try check("", "", session.value, output, accept));
    try u.writeJson(path, .{ .hostPid = u.c.GetCurrentProcessId(), .restored = true, .hookCompleted = true });
    try std.testing.expect(!try check("", "", session.value, output, accept));
    try std.testing.expectEqual(@as(u32, 0), calls);
    try u.writeJson(path, .{ .hostPid = child.pid, .restored = true, .hookCompleted = true, .hookExitCode = 20 });
    try std.testing.expect(!try check("", "", session.value, output, reject));
    try std.testing.expect(try check("", "", session.value, output, accept));
    const marker = try u.join(&.{ output, "observed/hook-session.json" });
    defer u.a.free(marker);
    try u.write(marker, "{}");
    calls = 0;
    try std.testing.expect(!try check("", "", session.value, output, accept));
    try std.testing.expectEqual(@as(u32, 0), calls);
}
