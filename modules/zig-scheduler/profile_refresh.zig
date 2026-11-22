//! Shipped-table to cached-profile refresh.
//!
//! The runtime reads exactly one cached profile and never derives layout values
//! itself. `resolve.py` keeps that cache current from the table that ships with
//! the product. Two things used to be ambiguous: which table path is correct
//! (`<root>/profiles` when packaged, `<root>/build/nsp-product/profiles` in the
//! build tree), and whether the refresh ran at all. The table is now searched in
//! a fixed order and every outcome — including failures — is recorded in
//! `capture-profile-status.json` beside the cached profile, with the selector's
//! own output kept in `capture-profile-refresh.log`.
const std = @import("std");
const u = @import("common.zig");
const c = u.c;
const a = u.a;

/// The runtime target. The selector also accepts other images; the product only
/// ever adapts the audiodg instance it is about to inject.
pub const image = "C:\\Windows\\System32\\audiodg.exe";
pub const profile_file = "capture-profile.json";
pub const status_file = "capture-profile-status.json";
pub const log_file = "capture-profile-refresh.log";
/// Cold caches may need a public PDB download; the old helper used 40 s and a
/// timeout now becomes an explicit `selector-timeout` instead of silence.
pub const selector_timeout_ms = 40_000;
const detail_limit = 1024;

/// Table locations in precedence order. A packaged product keeps the table next
/// to its runtime; the build tree has it under `build/nsp-product/profiles`.
/// `root` is the product root in both layouts, so the order never depends on
/// which directory happens to exist first in the file system.
pub const table_locations = [_][]const u8{
    "profiles/capture-profiles.json",
    "build/nsp-product/profiles/capture-profiles.json",
};

pub const Reason = enum {
    ok,
    script_missing,
    table_missing,
    python_spawn,
    selector_timeout,
    selector_failed,
    profile_missing,
    profile_invalid,
    input_missing,
    internal_error,

    pub fn text(self: Reason) []const u8 {
        return switch (self) {
            .ok => "ok",
            .script_missing => "script-missing",
            .table_missing => "table-missing",
            .python_spawn => "python-spawn",
            .selector_timeout => "selector-timeout",
            .selector_failed => "selector-failed",
            .profile_missing => "profile-missing",
            .profile_invalid => "profile-invalid",
            .input_missing => "input-missing",
            .internal_error => "internal-error",
        };
    }
};

/// What asked for the update. Recorded in the status file so a failure can be
/// traced to the session start, a menu action or an offline import.
pub const Action = enum {
    refresh,
    resolve,
    import_profile,
    import_pdb,

    pub fn text(self: Action) []const u8 {
        return switch (self) {
            .refresh => "refresh-profile",
            .resolve => "profile-resolve",
            .import_profile => "profile-import",
            .import_pdb => "profile-import-pdb",
        };
    }
};

pub const ImportKind = enum { profile, pdb };

pub const Report = struct {
    action: Action = .refresh,
    ok: bool = false,
    reason: Reason = .script_missing,
    detail: []const u8 = "",
    python: []const u8 = "",
    script: []const u8 = "",
    table: ?[]const u8 = null,
    searched: []const []const u8 = &.{},
    profile: []const u8 = "",
    layout: ?[]const u8 = null,
    symbol_key: ?[]const u8 = null,
    tier: ?[]const u8 = null,
    selector_exit: ?u32 = null,
    elapsed_ms: u64 = 0,
};

pub const ProfileInfo = struct {
    layout: []const u8,
    symbol_key: []const u8,
    tier: ?[]const u8 = null,
};

pub fn profilePath() ![]u8 {
    const base = try std.process.getEnvVarOwned(a, "LOCALAPPDATA");
    defer a.free(base);
    return u.join(&.{ base, "NativeSpatialPipe", profile_file });
}

/// Refresh the cached profile. Never fails the caller: the outcome is written to
/// the status file and failures are printed. The hook's own assertions still
/// decide whether the cached profile may be used.
pub fn refresh(root: []const u8, python: []const u8) void {
    const profile = profilePath() catch |err| {
        std.debug.print("capture profile refresh skipped: {s}\n", .{@errorName(err)});
        return;
    };
    defer a.free(profile);
    const report = refreshAt(root, python, profile);
    if (!report.ok) std.debug.print("capture profile refresh: {s} (table {s})\n", .{
        report.reason.text(),
        report.table orelse "<none>",
    });
}

/// Testable core: `profile` names the single cached profile the runtime reads,
/// which also fixes where the sidecar status and log files go. Returns the same
/// outcome it records; a status write failure never changes the report.
pub fn refreshAt(root: []const u8, python: []const u8, profile: []const u8) Report {
    return perform(root, python, profile, .refresh, null, &.{"--reuse-pdb-profile"});
}

/// Explicit user intent: regenerate the cache for the running image, resolving
/// from the symbol server when the table has no member for this build. `extra`
/// forwards selector options such as `--pick <layout>` or `--offline`.
pub fn resolveAt(root: []const u8, python: []const u8, profile: []const u8, extra: []const []const u8) Report {
    return perform(root, python, profile, .resolve, null, extra);
}

/// Offline import. A single-profile file is validated as-is; a PDB replaces the
/// symbol-server download for the selector.
pub fn importAt(root: []const u8, python: []const u8, profile: []const u8, file: []const u8, kind: ImportKind) Report {
    return perform(root, python, profile, if (kind == .pdb) .import_pdb else .import_profile, file, &.{});
}

pub fn perform(root: []const u8, python: []const u8, profile: []const u8, action: Action, file: ?[]const u8, extra: []const []const u8) Report {
    var timer = std.time.Timer.start() catch null;
    var report = Report{ .action = action, .python = python, .profile = profile };
    evaluate(root, profile, file, extra, &report);
    if (timer) |*value| report.elapsed_ms = value.read() / std.time.ns_per_ms;
    writeStatus(profile, report) catch |err| {
        std.debug.print("capture profile status write failed: {s}\n", .{@errorName(err)});
    };
    return report;
}

fn evaluate(root: []const u8, profile: []const u8, file: ?[]const u8, extra: []const []const u8, report: *Report) void {
    // Both candidates are always recorded, even when the selector is missing, so
    // the status file answers "where did it look" for every failure.
    const table = resolveTable(root, report) catch {
        fail(report, .internal_error, "cannot enumerate table candidates");
        return;
    };
    report.script = u.join(&.{ root, "tools/capture-profile/resolve.py" }) catch "";
    if (report.script.len == 0 or !u.exists(report.script)) {
        fail(report, .script_missing, report.script);
        return;
    }
    const import_file = if (report.action == .import_profile or report.action == .import_pdb)
        file orelse {
            fail(report, .input_missing, "no import file");
            return;
        }
    else
        null;
    if (import_file) |path| {
        if (!u.exists(path)) {
            fail(report, .input_missing, path);
            return;
        }
    }
    // Only a PDB import needs the table: resolving a new build creates the layout
    // in memory, while a profile import is validated on its own.
    if (table == null and report.action != .import_profile) {
        fail(report, .table_missing, "no shipped table under root");
        return;
    }
    const log = u.join(&.{ std.fs.path.dirname(profile) orelse ".", log_file }) catch {
        fail(report, .internal_error, "cannot name the selector log");
        return;
    };
    defer a.free(log);
    var args = std.ArrayList([]const u8).init(a);
    defer args.deinit();
    args.appendSlice(&.{ report.python, report.script, "--select", image }) catch {
        fail(report, .internal_error, "cannot build selector arguments");
        return;
    };
    if (table) |path| args.appendSlice(&.{ "--table", path }) catch return;
    args.appendSlice(&.{ "--out", profile }) catch return;
    if (import_file) |path| {
        const flag = if (report.action == .import_pdb) "--pdb" else "--profile";
        args.appendSlice(&.{ flag, path }) catch return;
    }
    if (extra.len != 0) args.appendSlice(extra) catch return;
    var child = u.spawnLog(args.items, log) catch |err| {
        fail(report, .python_spawn, @errorName(err));
        return;
    };
    defer child.close();
    if (c.WaitForSingleObject(child.handle, selector_timeout_ms) != c.WAIT_OBJECT_0) {
        _ = c.TerminateProcess(child.handle, 1);
        _ = c.WaitForSingleObject(child.handle, 5_000);
        fail(report, .selector_timeout, logTail(log));
        return;
    }
    const code = u.done(&child) catch null;
    report.selector_exit = code;
    if (code == null or code.? != 0) {
        fail(report, .selector_failed, logTail(log));
        return;
    }
    const bytes = u.read(profile) catch {
        fail(report, .profile_missing, profile);
        return;
    };
    defer a.free(bytes);
    const info = inspectProfile(bytes) catch |err| {
        fail(report, .profile_invalid, @errorName(err));
        return;
    };
    report.ok = true;
    report.reason = .ok;
    report.layout = info.layout;
    report.symbol_key = info.symbol_key;
    report.tier = info.tier;
    report.detail = logTail(log);
}

/// Locate the shipped table for `root` (packaged layout first, build tree
/// second). Returns every candidate it consulted so the status file can show
/// what was searched when none exists.
pub fn tablePath(root: []const u8, searched: *[]const []const u8) !?[]const u8 {
    const paths = try a.alloc([]const u8, table_locations.len);
    var found: ?[]const u8 = null;
    for (table_locations, 0..) |relative, index| {
        paths[index] = u.join(&.{ root, relative }) catch relative;
        if (found == null and u.exists(paths[index])) found = paths[index];
    }
    searched.* = paths;
    return found;
}

fn resolveTable(root: []const u8, report: *Report) !?[]const u8 {
    const found = try tablePath(root, &report.searched);
    report.table = found;
    return found;
}

/// Minimal invariants the runtime enforces again in `applyProfile`: both
/// identities present, a real entry RVA, at least one accepted vtable, and a
/// prologue that can be decoded. An empty prologue is the address-level
/// (bootstrap) profile, not an error.
pub fn inspectProfile(bytes: []const u8) !ProfileInfo {
    var parsed = try u.parse(bytes);
    defer parsed.deinit();
    const layout = u.string(u.field(parsed.value, "layout")) catch return error.MissingLayout;
    const symbol_key = u.string(u.field(parsed.value, "symbolKey")) catch return error.MissingSymbolKey;
    if (layout.len == 0 or symbol_key.len == 0) return error.MissingIdentity;
    const capture = u.field(parsed.value, "capture");
    if (capture != .object) return error.MissingCapture;
    if ((u.number(u.field(capture, "entryRva")) catch 0) == 0) return error.MissingEntryRva;
    const vtables = u.field(capture, "vtableRva");
    if (vtables != .array or vtables.array.items.len == 0) return error.MissingVtable;
    if (u.field(capture, "prologue") != .string) return error.MissingPrologue;
    const prologue = u.field(capture, "prologue").string;
    if (prologue.len > 64 or prologue.len % 2 != 0) return error.BadPrologue;
    const tier = if (u.field(parsed.value, "identitySource") == .string)
        try u.fmt("{s}", .{u.field(parsed.value, "identitySource").string})
    else
        null;
    return .{
        .layout = try u.fmt("{s}", .{layout}),
        .symbol_key = try u.fmt("{s}", .{symbol_key}),
        .tier = tier,
    };
}

/// Record one refresh attempt. Overwritten on every session start so a stale
/// success cannot hide a later failure.
pub fn writeStatus(profile: []const u8, report: Report) !void {
    const path = try u.join(&.{ std.fs.path.dirname(profile) orelse ".", status_file });
    defer a.free(path);
    const status = .{
        .schema = 1,
        .action = report.action.text(),
        .checkedAt = std.time.timestamp(),
        .ok = report.ok,
        .reason = report.reason.text(),
        .detail = report.detail,
        .python = report.python,
        .image = image,
        .script = report.script,
        .table = report.table,
        .searched = report.searched,
        .profile = report.profile,
        .layout = report.layout,
        .symbolKey = report.symbol_key,
        .tier = report.tier,
        .selectorExit = report.selector_exit,
        .elapsedMs = report.elapsed_ms,
    };
    try u.writeJson(path, status);
}

fn fail(report: *Report, reason: Reason, detail: []const u8) void {
    report.reason = reason;
    report.detail = detail;
}

/// The last `detail_limit` bytes of the selector output, trimmed and copied so
/// the report outlives the read buffer. The full log stays on disk.
fn logTail(path: []const u8) []const u8 {
    const bytes = u.read(path) catch return "";
    defer a.free(bytes);
    const slice = if (bytes.len > detail_limit) bytes[bytes.len - detail_limit ..] else bytes;
    const text = std.mem.trim(u8, slice, " \t\r\n\x00");
    return u.fmt("{s}", .{text}) catch "";
}
