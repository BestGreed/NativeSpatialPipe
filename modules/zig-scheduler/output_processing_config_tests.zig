const std = @import("std");
const p = @import("output_processing.zig");

test "versioned Rust DSP configuration and timing contract" {
    var config = p.Config{ .channels = 2, .limiter = true, .lookahead_frames = 48 };
    config.delay_frames[1] = 96;
    try config.validate();
    const native = p.NativeConfig.from(config);
    try std.testing.expectEqual(@as(usize, 440), @sizeOf(p.NativeConfig));
    try std.testing.expectEqual(@as(u32, 1), native.version);
    try std.testing.expectEqual(@as(u32, 144), config.timing().tail_frames);
    config.bypass = true;
    try std.testing.expectEqual(@as(u32, 0), config.timing().tail_frames);
}

test "invalid configuration rejected before native creation" {
    var config = p.Config{ .channels = 0 };
    try std.testing.expectError(error.InvalidOutputConfig, config.validate());
    config.channels = 2;
    config.delay_frames[1] = 48001;
    try std.testing.expectError(error.InvalidOutputConfig, config.validate());
}
