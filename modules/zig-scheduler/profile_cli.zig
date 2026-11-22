//! Scheduler-side entry points for user-initiated capture-profile work.
//!
//! FSA only passes an intent (resolve for this build, or import an offline file);
//! path resolution, selector invocation and the status sidecar stay in
//! `profile_refresh` so the product has one implementation of the contract.
const std = @import("std");
const u = @import("common.zig");
const a = u.a;
const refresh = @import("profile_refresh.zig");

/// Exit code used by the scheduler for a failed control action. Zero means the
/// cached profile is now usable for the running image.
const failed: u8 = 20;

pub fn resolve(args: []const []const u8, root: []const u8) !u8 {
    var extra = std.ArrayList([]const u8).init(a);
    defer extra.deinit();
    if (u.option(args, "--pick")) |layout| {
        if (std.mem.indexOfAny(u8, layout, "\r\n") != null) return error.InvalidLayout;
        try extra.appendSlice(&.{ "--pick", layout });
    } else {
        try extra.append("--verify-pdb");
    }
    if (u.flag(args, "--offline")) try extra.append("--offline");
    const python = u.option(args, "--python") orelse "python.exe";
    const profile = try refresh.profilePath();
    defer a.free(profile);
    return report(refresh.resolveAt(root, python, profile, extra.items));
}

pub fn importProfile(args: []const []const u8, root: []const u8) !u8 {
    const file = u.option(args, "--file") orelse return error.ImportFileRequired;
    if (!std.fs.path.isAbsolute(file)) return error.ImportFileRequiresAbsolutePath;
    const kind = kindOf(file) orelse return error.UnsupportedImportFile;
    const python = u.option(args, "--python") orelse "python.exe";
    const profile = try refresh.profilePath();
    defer a.free(profile);
    return report(refresh.importAt(root, python, profile, file, kind));
}

fn kindOf(path: []const u8) ?refresh.ImportKind {
    if (endsWith(path, ".pdb")) return .pdb;
    if (endsWith(path, ".json")) return .profile;
    return null;
}

fn endsWith(path: []const u8, suffix: []const u8) bool {
    if (path.len < suffix.len) return false;
    return std.ascii.eqlIgnoreCase(path[path.len - suffix.len ..], suffix);
}

/// Print the outcome on stdout as one JSON object; the same outcome is already in
/// `capture-profile-status.json`, so callers may use either channel.
fn report(outcome: refresh.Report) !u8 {
    const bytes = try u.json(.{
        .ok = outcome.ok,
        .action = outcome.action.text(),
        .reason = outcome.reason.text(),
        .detail = outcome.detail,
        .table = outcome.table,
        .layout = outcome.layout,
        .symbolKey = outcome.symbol_key,
        .tier = outcome.tier,
        .profile = outcome.profile,
        .selectorExit = outcome.selector_exit,
        .elapsedMs = outcome.elapsed_ms,
    });
    defer a.free(bytes);
    try std.io.getStdOut().writer().print("{s}\n", .{bytes});
    return if (outcome.ok) 0 else failed;
}
