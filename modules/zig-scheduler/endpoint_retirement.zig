const std = @import("std");
const u = @import("common.zig");
const a = u.a;

const absence_ms = 7_000;

fn expired(first: u64, last: u64, now: u64) bool {
    // A long gap or reboot breaks the sequence of missing observations.
    return now >= last and now - last <= 5_000 and now -| first >= absence_ms;
}

fn exited(pid: u64) !bool {
    const value = std.math.cast(u32, pid) orelse return false;
    return value != 0 and try u.inspectProcessIdentity(value) == null;
}

fn completedCapture(output: []const u8, session: u.Value) !?u32 {
    const owner = try u.number(u.field(session, "orchestratorPid"));
    if (!try exited(owner)) return null;
    const path = try u.join(&.{ output, "endpoint.json" });
    defer a.free(path);
    var report = u.load(path) catch return null;
    defer report.deinit();
    if (u.yes(u.field(report.value, "restored")) or !u.yes(u.field(report.value, "hookCompleted")) or
        (u.number(u.field(report.value, "hookExitCode")) catch 255) != 0) return null;
    const host = try u.number(u.field(report.value, "hostPid"));
    if (!try exited(host)) return null;
    const telemetry = try u.join(&.{ output, "observed", "telemetry-service.json" });
    defer a.free(telemetry);
    if (u.exists(telemetry)) {
        var service = try u.load(telemetry);
        defer service.deinit();
        const pid = try u.number(u.field(service.value, "pid"));
        if (!try exited(pid)) return null;
    }
    return @intCast(host);
}

/// Only the caller that has just proved the old Hook is off may publish this.
pub fn observe(output: []const u8, session: u.Value, now: u64, endpoint_missing: bool, recheck: *const fn ([]const u8) anyerror!bool) !bool {
    const pending = try u.join(&.{ output, "endpoint-absence.json" });
    defer a.free(pending);
    const endpoint = try u.string(u.field(session, "endpoint"));
    if (!endpoint_missing) {
        u.remove(pending);
        return false;
    }
    const host = (try completedCapture(output, session)) orelse {
        u.remove(pending);
        return false;
    };
    var first = now;
    var ready = false;
    if (u.load(pending)) |loaded| {
        var previous = loaded;
        defer previous.deinit();
        const last = u.number(u.field(previous.value, "last")) catch 0;
        if (now >= last and now - last <= 5_000) {
            first = u.number(u.field(previous.value, "first")) catch now;
            ready = expired(first, last, now);
        }
    } else |_| {}
    try u.writeJson(pending, .{ .first = first, .last = now });
    if (!ready or (try completedCapture(output, session)) != host) return false;
    if (!(recheck(endpoint) catch false)) {
        u.remove(pending);
        return false;
    }
    const proof = try u.join(&.{ output, "endpoint-retirement.json" });
    defer a.free(proof);
    try u.writeJson(proof, .{
        .sessionId = try u.string(u.field(session, "id")),
        .endpoint = endpoint,
        .hostPid = host,
        .endpointSnapshotRestored = false,
        .reason = "Original render endpoint remained absent after verified capture teardown",
    });
    return true;
}

pub fn proven(output: []const u8, session: u.Value) bool {
    const host = (completedCapture(output, session) catch return false) orelse return false;
    const path = u.join(&.{ output, "endpoint-retirement.json" }) catch return false;
    defer a.free(path);
    var proof = u.load(path) catch return false;
    defer proof.deinit();
    const id = u.string(u.field(session, "id")) catch return false;
    const endpoint = u.string(u.field(session, "endpoint")) catch return false;
    return std.mem.eql(u8, id, u.string(u.field(proof.value, "sessionId")) catch return false) and
        std.ascii.eqlIgnoreCase(endpoint, u.string(u.field(proof.value, "endpoint")) catch return false) and
        (u.number(u.field(proof.value, "hostPid")) catch 0) == host and
        u.field(proof.value, "endpointSnapshotRestored") == .bool and !u.yes(u.field(proof.value, "endpointSnapshotRestored"));
}

test "endpoint absence grace requires continuing observations" {
    try std.testing.expect(!expired(1_000, 7_000, 7_999));
    try std.testing.expect(expired(1_000, 7_000, 8_000));
    try std.testing.expect(!expired(1_000, 2_999, 8_000));
    try std.testing.expect(!expired(20_000, 25_000, 16_000));
}

fn stillMissing(_: []const u8) !bool {
    return true;
}

test "a missing endpoint retires only after capture exits, without replacing the restore report" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const output = try tmp.dir.realpathAlloc(a, ".");
    defer a.free(output);
    const report = try u.join(&.{ output, "endpoint.json" });
    defer a.free(report);
    var completed = try u.spawn(&.{ "C:\\Windows\\System32\\cmd.exe", "/c", "exit", "0" });
    defer completed.close();
    try std.testing.expectEqual(u.c.WAIT_OBJECT_0, u.c.WaitForSingleObject(completed.handle, 5000));
    const data = try u.json(.{ .id = "test", .endpoint = "hdmi", .orchestratorPid = completed.pid });
    defer a.free(data);
    var session = try u.parse(data);
    defer session.deinit();
    try u.writeJson(report, .{ .hostPid = u.c.GetCurrentProcessId(), .restored = false, .hookCompleted = true, .hookExitCode = 0 });
    try std.testing.expect(!try observe(output, session.value, 1000, true, stillMissing));
    try u.writeJson(report, .{ .hostPid = completed.pid, .restored = false, .hookCompleted = true, .hookExitCode = 0 });
    const original = try u.read(report);
    defer a.free(original);
    for ([_]u64{ 1_000, 4_000, 7_000 }) |tick| {
        try std.testing.expect(!try observe(output, session.value, tick, true, stillMissing));
    }
    try std.testing.expect(!try observe(output, session.value, 8_000, false, stillMissing));
    for ([_]u64{ 9_000, 12_000, 15_000, 15_999 }) |tick| {
        try std.testing.expect(!try observe(output, session.value, tick, true, stillMissing));
    }
    try std.testing.expect(try observe(output, session.value, 16_000, true, stillMissing));
    try std.testing.expect(proven(output, session.value));
    const unchanged = try u.read(report);
    defer a.free(unchanged);
    try std.testing.expectEqualStrings(original, unchanged);
}
