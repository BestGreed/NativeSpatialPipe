const std = @import("std");
const r = @import("profile_refresh.zig");
const u = @import("common.zig");

fn tempRoot(tmp: *std.testing.TmpDir) ![]u8 {
    return tmp.dir.realpathAlloc(std.testing.allocator, ".");
}

fn profileUnder(allocator: std.mem.Allocator, root: []const u8) ![]u8 {
    return std.fmt.allocPrint(allocator, "{s}/cache/capture-profile.json", .{root});
}

fn readStatus(allocator: std.mem.Allocator, profile: []const u8) !std.json.Parsed(std.json.Value) {
    const dir = std.fs.path.dirname(profile) orelse ".";
    const path = try std.fs.path.join(allocator, &.{ dir, r.status_file });
    defer allocator.free(path);
    const bytes = try std.fs.cwd().readFileAlloc(allocator, path, 64 * 1024);
    defer allocator.free(bytes);
    return std.json.parseFromSlice(std.json.Value, allocator, bytes, .{});
}

fn field(value: std.json.Value, key: []const u8) std.json.Value {
    return value.object.get(key) orelse .null;
}

/// Compare path suffixes without depending on the platform separator that
/// `std.fs.path.resolve` produced.
fn slashed(path: []const u8, buffer: []u8) []const u8 {
    const length = @min(path.len, buffer.len);
    for (path[0..length], 0..) |ch, index| buffer[index] = if (ch == '\\') '/' else ch;
    return buffer[0..length];
}

test "table resolution prefers the packaged layout over the build tree" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const root = try tempRoot(&tmp);
    defer std.testing.allocator.free(root);
    try tmp.dir.makePath("build/nsp-product/profiles");
    try tmp.dir.writeFile(.{ .sub_path = "build/nsp-product/profiles/capture-profiles.json", .data = "{}" });

    {
        var searched: []const []const u8 = &.{};
        defer u.a.free(searched);
        const found = try r.tablePath(root, &searched);
        try std.testing.expect(found != null);
        var buffer: [512]u8 = undefined;
        try std.testing.expect(std.mem.endsWith(u8, slashed(found.?, &buffer), "build/nsp-product/profiles/capture-profiles.json"));
        try std.testing.expectEqual(@as(usize, 2), searched.len);
    }
    try tmp.dir.makePath("profiles");
    try tmp.dir.writeFile(.{ .sub_path = "profiles/capture-profiles.json", .data = "{}" });
    {
        var searched: []const []const u8 = &.{};
        defer u.a.free(searched);
        const found = try r.tablePath(root, &searched);
        try std.testing.expect(found != null);
        var buffer: [512]u8 = undefined;
        const normalized = slashed(found.?, &buffer);
        try std.testing.expect(std.mem.endsWith(u8, normalized, "/profiles/capture-profiles.json"));
        try std.testing.expect(!std.mem.endsWith(u8, normalized, "/build/nsp-product/profiles/capture-profiles.json"));
    }
    try tmp.dir.deleteFile("profiles/capture-profiles.json");
    try tmp.dir.deleteFile("build/nsp-product/profiles/capture-profiles.json");
    {
        var searched: []const []const u8 = &.{};
        defer u.a.free(searched);
        try std.testing.expect((try r.tablePath(root, &searched)) == null);
    }
}

test "a missing selector is recorded instead of skipped silently" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const root = try tempRoot(&tmp);
    defer std.testing.allocator.free(root);
    const profile = try profileUnder(std.testing.allocator, root);
    defer std.testing.allocator.free(profile);
    try tmp.dir.makePath("cache");

    const report = r.refreshAt(root, "python.exe", profile);
    try std.testing.expectEqual(r.Reason.script_missing, report.reason);
    try std.testing.expect(!report.ok);
    try std.testing.expect(report.selector_exit == null);

    var status = try readStatus(std.testing.allocator, profile);
    defer status.deinit();
    try std.testing.expectEqual(false, field(status.value, "ok").bool);
    try std.testing.expectEqualStrings("script-missing", field(status.value, "reason").string);
    try std.testing.expectEqualStrings("refresh-profile", field(status.value, "action").string);
    try std.testing.expectEqualStrings(profile, field(status.value, "profile").string);
    try std.testing.expect(field(status.value, "checkedAt").integer > 0);
    try std.testing.expectEqual(@as(usize, 2), field(status.value, "searched").array.items.len);
    try std.testing.expect(field(status.value, "table") == .null);
}

test "a missing shipped table names every location it searched" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const root = try tempRoot(&tmp);
    defer std.testing.allocator.free(root);
    const profile = try profileUnder(std.testing.allocator, root);
    defer std.testing.allocator.free(profile);
    try tmp.dir.makePath("cache");
    try tmp.dir.makePath("tools/capture-profile");
    try tmp.dir.writeFile(.{ .sub_path = "tools/capture-profile/resolve.py", .data = "" });

    const report = r.refreshAt(root, "python.exe", profile);
    try std.testing.expectEqual(r.Reason.table_missing, report.reason);
    try std.testing.expect(!report.ok);

    var status = try readStatus(std.testing.allocator, profile);
    defer status.deinit();
    try std.testing.expectEqualStrings("table-missing", field(status.value, "reason").string);
    const searched = field(status.value, "searched").array.items;
    try std.testing.expectEqual(@as(usize, 2), searched.len);
    var buffer: [512]u8 = undefined;
    try std.testing.expect(std.mem.endsWith(u8, slashed(searched[0].string, &buffer), "profiles/capture-profiles.json"));
    try std.testing.expect(std.mem.endsWith(u8, slashed(searched[1].string, &buffer), "build/nsp-product/profiles/capture-profiles.json"));
}

test "an unrunnable selector is recorded as python-spawn" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const root = try tempRoot(&tmp);
    defer std.testing.allocator.free(root);
    const profile = try profileUnder(std.testing.allocator, root);
    defer std.testing.allocator.free(profile);
    try tmp.dir.makePath("cache");
    try tmp.dir.makePath("tools/capture-profile");
    try tmp.dir.writeFile(.{ .sub_path = "tools/capture-profile/resolve.py", .data = "" });
    try tmp.dir.makePath("profiles");
    try tmp.dir.writeFile(.{ .sub_path = "profiles/capture-profiles.json", .data = "{}" });

    const missing = try std.fmt.allocPrint(std.testing.allocator, "{s}/missing-python.exe", .{root});
    defer std.testing.allocator.free(missing);
    const report = r.refreshAt(root, missing, profile);
    try std.testing.expectEqual(r.Reason.python_spawn, report.reason);
    try std.testing.expect(!report.ok);
    try std.testing.expect(report.selector_exit == null);

    var status = try readStatus(std.testing.allocator, profile);
    defer status.deinit();
    try std.testing.expectEqualStrings("python-spawn", field(status.value, "reason").string);
    try std.testing.expectEqualStrings("ChildSpawn", field(status.value, "detail").string);
    try std.testing.expectEqualStrings(missing, field(status.value, "python").string);
}

test "resolve and import record their action and reject missing inputs" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const root = try tempRoot(&tmp);
    defer std.testing.allocator.free(root);
    const profile = try profileUnder(std.testing.allocator, root);
    defer std.testing.allocator.free(profile);
    try tmp.dir.makePath("cache");
    try tmp.dir.makePath("tools/capture-profile");
    try tmp.dir.writeFile(.{ .sub_path = "tools/capture-profile/resolve.py", .data = "" });
    // A profile import is validated on its own and must not depend on the table.
    const resolve_report = r.resolveAt(root, "python.exe", profile, &.{});
    try std.testing.expectEqual(r.Reason.table_missing, resolve_report.reason);
    try std.testing.expectEqual(r.Action.resolve, resolve_report.action);

    const import_report = r.importAt(root, "python.exe", profile, "/no/such/profile.json", .profile);
    try std.testing.expectEqual(r.Reason.input_missing, import_report.reason);
    try std.testing.expectEqual(r.Action.import_profile, import_report.action);

    var status = try readStatus(std.testing.allocator, profile);
    defer status.deinit();
    try std.testing.expectEqualStrings("profile-import", field(status.value, "action").string);
    try std.testing.expectEqualStrings("input-missing", field(status.value, "reason").string);
}

test "a PDB import needs the shipped table but a profile import does not" {
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const root = try tempRoot(&tmp);
    defer std.testing.allocator.free(root);
    const profile = try profileUnder(std.testing.allocator, root);
    defer std.testing.allocator.free(profile);
    try tmp.dir.makePath("cache");
    try tmp.dir.makePath("tools/capture-profile");
    try tmp.dir.writeFile(.{ .sub_path = "tools/capture-profile/resolve.py", .data = "" });
    const pdb = try std.fmt.allocPrint(std.testing.allocator, "{s}/input.pdb", .{root});
    defer std.testing.allocator.free(pdb);
    try tmp.dir.writeFile(.{ .sub_path = "input.pdb", .data = "not a real pdb" });
    const json = try std.fmt.allocPrint(std.testing.allocator, "{s}/input.json", .{root});
    defer std.testing.allocator.free(json);
    try tmp.dir.writeFile(.{ .sub_path = "input.json", .data = "{}" });

    const pdb_report = r.importAt(root, "python.exe", profile, pdb, .pdb);
    try std.testing.expectEqual(r.Reason.table_missing, pdb_report.reason);
    try std.testing.expectEqual(r.Action.import_pdb, pdb_report.action);

    // The image has no table, so a profile import must still reach the selector.
    const missing = try std.fmt.allocPrint(std.testing.allocator, "{s}/missing-python.exe", .{root});
    defer std.testing.allocator.free(missing);
    const profile_report = r.importAt(root, missing, profile, json, .profile);
    try std.testing.expectEqual(r.Reason.python_spawn, profile_report.reason);
}

test "selector cache inspection allows bootstrap metadata" {
    const anchored = try r.inspectProfile(
        \\{"layout":"L31930_9F4F8","symbolKey":"882DC2C4101A0C7A6008528B0BB73AED1","capture":{"entryRva":203056,"vtableRva":[652536],"prologue":"48895c240848896c2410488974241857"}}
    );
    try std.testing.expectEqualStrings("L31930_9F4F8", anchored.layout);
    try std.testing.expectEqualStrings("882DC2C4101A0C7A6008528B0BB73AED1", anchored.symbol_key);
    try std.testing.expect(anchored.tier == null);
    const fingerprinted = try r.inspectProfile(
        \\{"layout":"L31930_9F4F8","symbolKey":"882DC2C4101A0C7A6008528B0BB73AED1","identitySource":"fingerprint","capture":{"entryRva":203056,"vtableRva":[652536],"prologue":"48895c240848896c2410488974241857"}}
    );
    try std.testing.expectEqualStrings("fingerprint", fingerprinted.tier.?);
    const bootstrap = try r.inspectProfile(
        \\{"layout":"L6D650_76870","symbolKey":"D08CEAC0E33AD1959564D4192BE43BA51","capture":{"entryRva":448080,"vtableRva":[485488],"prologue":""}}
    );
    try std.testing.expectEqualStrings("L6D650_76870", bootstrap.layout);
    try std.testing.expectError(error.MissingVtable, r.inspectProfile(
        \\{"layout":"L1","symbolKey":"A","capture":{"entryRva":1,"vtableRva":[],"prologue":""}}
    ));
    try std.testing.expectError(error.MissingEntryRva, r.inspectProfile(
        \\{"layout":"L1","symbolKey":"A","capture":{"entryRva":0,"vtableRva":[4096],"prologue":""}}
    ));
    try std.testing.expectError(error.BadPrologue, r.inspectProfile(
        \\{"layout":"L1","symbolKey":"A","capture":{"entryRva":1,"vtableRva":[4096],"prologue":"abc"}}
    ));
    try std.testing.expectError(error.MissingIdentity, r.inspectProfile(
        \\{"layout":"","symbolKey":"A","capture":{"entryRva":1,"vtableRva":[4096],"prologue":""}}
    ));
}
test {
    _ = @import("native.zig");
}
