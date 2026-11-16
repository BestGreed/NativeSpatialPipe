//! FSA invokes this control path. No media-controller work runs on audio threads.
const std = @import("std");
const u = @import("common.zig");
const profile = @import("desktop_playback.zig");

pub fn update(args: []const []const u8, home: []const u8) !void {
    try std.fs.cwd().makePath(home);
    const status = try u.join(&.{ home, "mft-status.json" });
    defer u.a.free(status);
    const controller = u.option(args, "--mft-controller") orelse return error.MftControllerRequired;
    const enabled = desired(args) catch |err| {
        try u.writeJson(status, .{ .enabled = @as(?bool, null), .state = "invalid-config", .errorName = @errorName(err) });
        return err;
    };
    if (!enabled) {
        std.fs.cwd().access(controller, .{}) catch |err| {
            if (err != error.FileNotFound) return err;
            try u.writeJson(status, .{ .enabled = false, .state = "disabled" });
            return;
        };
    }
    var child = std.process.Child.init(&.{ controller, if (enabled) "ensure" else "disable" }, u.a);
    child.stdin_behavior = .Ignore;
    child.stdout_behavior = .Ignore;
    child.stderr_behavior = .Ignore;
    const result = child.spawnAndWait() catch |err| {
        try u.writeJson(status, .{ .enabled = enabled, .state = "unavailable", .errorName = @errorName(err) });
        return err;
    };
    const exit_code: ?u32 = switch (result) { .Exited => |code| code, else => null };
    const ok = exit_code != null and exit_code.? == 0;
    try u.writeJson(status, .{ .enabled = enabled, .state = if (!ok) "error" else if (enabled) "watch-ready" else "disabled", .exitCode = exit_code });
    if (!ok) return error.MftControllerFailed;
}

fn desired(args: []const []const u8) !bool {
    if (u.flag(args, "--disable")) return false;
    const path = u.option(args, "--playback-config") orelse return false;
    const bytes = try u.read(path);
    defer u.a.free(bytes);
    var parsed = try profile.parse(u.a, bytes);
    defer parsed.deinit();
    return if (parsed.value.mediaFoundation) |mft| mft.enabled else false;
}
