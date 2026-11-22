const std = @import("std");
const u = @import("common.zig");
const n = @import("native.zig");
const a = u.a;
const c = u.c;

// Callers must hold owner.lock, either as the serving controller or offline.
// Global runtime.json belongs to the current controller, not the old session.
pub fn retirable(home: []const u8, session: u.Value, output: []const u8) !bool {
    const owner = std.math.cast(u32, u.number(u.field(session, "orchestratorPid")) catch return false) orelse return false;
    if (owner == 0 or try u.inspectProcessIdentity(owner) != null) return false;
    _ = home; // The caller holds owner.lock; runtime.json describes the current manager.
    const observed = try u.join(&.{ output, "observed" });
    defer a.free(observed);
    const marker_path = try u.join(&.{ observed, "hook-session.json" });
    defer a.free(marker_path);
    var marker = try u.load(marker_path);
    defer marker.deinit();
    if (try u.number(u.field(marker.value, "hookGeneration")) != 2) return false;
    const target = std.math.cast(u32, try u.number(u.field(marker.value, "pid"))) orelse return false;
    const created = try u.number(u.field(marker.value, "created"));
    if (target == 0 or (try u.inspectProcessIdentity(target)) == created) return false;
    const telemetry_path = try u.join(&.{ observed, "telemetry-service.json" });
    defer a.free(telemetry_path);
    if (u.exists(telemetry_path)) {
        var telemetry = try u.load(telemetry_path);
        defer telemetry.deinit();
        const pid = std.math.cast(u32, try u.number(u.field(telemetry.value, "pid"))) orelse return false;
        if (pid == 0 or try u.inspectProcessIdentity(pid) != null) return false;
    }
    var dir = try std.fs.cwd().openDir(observed, .{ .iterate = true });
    defer dir.close();
    var entries = dir.iterate();
    var host_proof = false;
    while (try entries.next()) |entry| {
        if (entry.kind != .file or !std.mem.startsWith(u8, entry.name, "hook-config-") or !std.mem.endsWith(u8, entry.name, ".bin")) continue;
        const bytes = try dir.readFileAlloc(a, entry.name, @sizeOf(c.HookConfig));
        defer a.free(bytes);
        if (bytes.len != @sizeOf(c.HookConfig)) return false;
        var config: c.HookConfig = undefined;
        @memcpy(std.mem.asBytes(&config), bytes);
        if (config.magic != c.NSP_HOOK_MAGIC or config.size != @sizeOf(c.HookConfig) or config.fixture != 0 or config.target_created != created or config.host_pid == 0) return false;
        if (try u.inspectProcessIdentity(config.host_pid) != null) return false;
        host_proof = true;
    }
    return host_proof;
}
pub fn proveWithAudio(root: []const u8, home: []const u8, session: u.Value, audio_pid: *const fn () anyerror!u32) !bool {
    const started = c.GetTickCount64();
    const output = try u.string(u.field(session, "outputDirectory"));
    if (!try retirable(home, session, output)) return false;
    // Do not trust the old target's death alone: observe the current audiodg.
    const pid = audio_pid() catch |err| switch (err) {
        error.NoAudioHost => @as(u32, 0),
        else => return err,
    };
    var created: u64 = 0;
    if (pid != 0) {
        created = try u.checkTarget(pid);
        _ = try u.checkModules(pid);
        var hook = try n.Hook.init(root, home, pid, 0, created, false);
        defer {
            u.remove(hook.path);
            a.free(hook.path);
        }
        var baseline = try hook.call("baseline");
        defer baseline.deinit();
        if (!u.yes(u.field(baseline.value, "baselineVerified"))) return false;
    }
    // Recheck before publishing; never manufacture a clean endpoint report.
    if (!try retirable(home, session, output)) return false;
    if (pid != 0) {
        if (try audio_pid() != pid or (try u.inspectProcessIdentity(pid)) != created) return false;
    } else {
        // No host is a valid idle Windows state. Inventory errors and multiple
        // hosts must not be mistaken for absence; recheck to catch a new host.
        const current = audio_pid() catch |err| switch (err) {
            error.NoAudioHost => @as(u32, 0),
            else => return err,
        };
        if (current != 0) return false;
    }
    const proof_path = try u.join(&.{ output, "orphan-recovery.json" });
    defer a.free(proof_path);
    try u.writeJson(proof_path, .{ .checkedAt = std.time.timestamp(), .sessionId = try u.string(u.field(session, "id")), .elapsedMs = c.GetTickCount64() - started, .currentTargetPid = pid, .currentTargetCreated = created, .baselineVerified = pid != 0, .audioHostAbsent = pid == 0, .oldProcessesExited = true, .endpointSnapshotRestored = false, .reason = "Orphaned session retired; current endpoint preserved" });
    return true;
}
