//! Named device wiring; does not alter renderer or common DSP channel order.
const std = @import("std");
pub const presets=@import("layout_presets.zig");
pub const identity=[_]u32{0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
pub const dvs_sources=[_]u32{0,2,1,5,4,7,6,3,10,8,9,11};
pub const olr_labels = [12][]const u8{ "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltr", "Rtr" };
pub const dvs_labels = [12][]const u8{ "L", "C", "R", "Rs", "Ls", "Rrs", "Lrs", "LFE", "Ltr", "Ltf", "Rtf", "Rtr" };
pub fn sources(name: []const u8) ![12]u32 {
    if (std.mem.eql(u8, name, "olr")) return .{ 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
    if (std.mem.eql(u8, name, "dvs-speakers")) return .{ 0, 2, 1, 5, 4, 7, 6, 3, 10, 8, 9, 11 };
    return error.UnknownOutputOrder;
}
pub fn labels(name: []const u8) ![12][]const u8 {
    _ = try sources(name);
    return if (std.mem.eql(u8, name, "olr")) olr_labels else dvs_labels;
}
