const std = @import("std");
const layout = @import("output_layout.zig");
test "device wiring maps each speaker semantic exactly once from OLR" {
    const map = try layout.sources("dvs-speakers");
    const names = try layout.labels("dvs-speakers");
    var seen = [_]bool{false} ** 12;
    for (map, names) |source, name| {
        try std.testing.expect(source < 12 and !seen[source]);
        try std.testing.expectEqualStrings(name, layout.olr_labels[source]);
        seen[source] = true;
    }
    // The LFE and top-rear-left wiring are explicitly user-confirmed.
    try std.testing.expectEqualStrings("LFE", names[7]);
    try std.testing.expectEqualStrings("Ltr", names[8]);
}
test "generic OLR order stays identity and unknown profiles fail" {
    const map = try layout.sources("olr");
    for (map, 0..) |source, i| try std.testing.expectEqual(i, source);
    try std.testing.expectError(error.UnknownOutputOrder, layout.sources("unknown"));
}
