//! Contract tests for the generated audiodg build-profile table. The table is
//! generated from contracts/audiodg-builds.json; these tests pin the behaviour
//! every consumer relies on: select by full-file SHA-256, distinguish builds
//! that share RVAs, and fail closed with the actual digest plus the supported
//! list for unknown images.
const std = @import("std");
const builds = @import("audiodg_builds.zig");

const build_9444 = "3dd24fe6faf0b56f99d5b00f24687b5f3b3e76e15b219e23f0963114a0dce9a5";
const build_9974 = "017628fc0ace806163950bb3019709ca3820531d7f19c1103429e09b4c54f6ca";
const build_10068 = "e55b5365056273c133954ca7c44b8d4f1c3139a4b3fa766a94ffe58c2621a8b3";

fn digest(comptime hex: *const [64]u8) [32]u8 {
    var value: [32]u8 = undefined;
    _ = std.fmt.hexToBytes(&value, hex) catch unreachable;
    return value;
}

fn selected(comptime hex: *const [64]u8) !*const builds.Profile {
    const value = digest(hex);
    return builds.match(&value) orelse error.UnknownBuild;
}

test "each supported build selects its own runtime layout" {
    const p9444 = try selected(build_9444);
    try std.testing.expectEqualStrings("win-26100-9444", p9444.id);
    try std.testing.expectEqual(@as(u32, 0x31930), p9444.target_rva);
    try std.testing.expectEqual(@as(u32, 474), p9444.target_size);
    try std.testing.expectEqual(@as(u32, 0x9f4f8), p9444.vt1_rva);
    try std.testing.expectEqual(@as(u32, 0xa2d58), p9444.vt2_rva);

    const p9974 = try selected(build_9974);
    try std.testing.expectEqualStrings("win-26100-9974", p9974.id);
    try std.testing.expectEqual(@as(u32, 0x32c40), p9974.target_rva);
    try std.testing.expectEqual(@as(u32, 474), p9974.target_size);
    try std.testing.expectEqual(@as(u32, 0x9e4e8), p9974.vt1_rva);
    // 9974 and 10068 share target/vt1 RVAs but their data layout differs:
    // the compiled-in reference for the second block vtable moved by 0x40.
    try std.testing.expectEqual(@as(u32, 0xa1be0), p9974.vt2_rva);

    const p10068 = try selected(build_10068);
    try std.testing.expectEqualStrings("win-26100-10068", p10068.id);
    try std.testing.expectEqual(@as(u32, 0x32c40), p10068.target_rva);
    try std.testing.expectEqual(@as(u32, 0x9e4e8), p10068.vt1_rva);
    try std.testing.expectEqual(@as(u32, 0xa1c20), p10068.vt2_rva);
}

test "same-layout builds stay separate table entries" {
    const p9974 = try selected(build_9974);
    const p10068 = try selected(build_10068);
    try std.testing.expectEqual(p9974.target_rva, p10068.target_rva);
    try std.testing.expectEqual(p9974.vt1_rva, p10068.vt1_rva);
    try std.testing.expect(std.mem.eql(u8, &p9974.prologue, &p10068.prologue));
    try std.testing.expect(!std.mem.eql(u8, &p9974.function_sha256, &p10068.function_sha256));
    try std.testing.expect(!std.mem.eql(u8, &p9974.sha256, &p10068.sha256));
}

test "unknown build fails closed with actual digest and supported list" {
    const unknown = [_]u8{0xab} ** 32;
    try std.testing.expect(builds.match(&unknown) == null);
    var buffer: [1400]u8 = undefined;
    const message = builds.unsupportedMessage(&buffer, &unknown);
    try std.testing.expect(std.mem.indexOf(u8, message, "Unsupported audiodg build") != null);
    try std.testing.expect(std.mem.indexOf(u8, message, "abababab") != null);
    try std.testing.expect(std.mem.indexOf(u8, message, "3dd24fe6") != null);
    try std.testing.expect(std.mem.indexOf(u8, message, "017628fc") != null);
    try std.testing.expect(std.mem.indexOf(u8, message, "e55b5365") != null);
    try std.testing.expect(std.mem.indexOf(u8, message, "contracts/audiodg-builds.json") != null);
}

test "every profile carries the runtime fields the hook needs" {
    for (builds.profiles) |profile| {
        try std.testing.expect(profile.id.len != 0);
        try std.testing.expect(profile.label.len != 0);
        try std.testing.expect(profile.target_rva != 0);
        try std.testing.expect(profile.target_size != 0);
        try std.testing.expect(profile.vt1_rva != 0);
        try std.testing.expect(profile.vt2_rva != 0);
        var prologue_sum: u32 = 0;
        for (profile.prologue) |byte| prologue_sum += byte;
        try std.testing.expect(prologue_sum != 0);
        var function_sum: u32 = 0;
        for (profile.function_sha256) |byte| function_sum += byte;
        try std.testing.expect(function_sum != 0);
    }
}