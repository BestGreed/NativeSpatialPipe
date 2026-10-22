const std = @import("std");
const p = @import("output_processing.zig");
const expect = std.testing.expect;
test "explicit bypass ignores configured effects without history or PCM writes" {
    var config=p.Config{.bypass=true,.master=0.25,.limiter=true,.lookahead_frames=100};
    config.delay_frames[0]=200;config.trim[0]=0.5;
    var processor=try p.Processor.init(std.testing.allocator,config);defer processor.deinit();
    var pcm=[_]f32{-0.0,2,-3,0.25,1,0,0,0,0,0,0,0};const before=pcm;
    try processor.process(&pcm);
    try std.testing.expectEqualSlices(u8,std.mem.asBytes(&before),std.mem.asBytes(&pcm));
    try expect(processor.delay.len==0 and processor.pending.len==0 and processor.peaks.len==0 and processor.timing().tail_frames==0);
    processor.finish();try expect(try processor.drain(&pcm)==0);
}

test "default bypass bit exact, no delay or limiter storage" {
    var processor = try p.Processor.init(std.testing.allocator, .{});
    defer processor.deinit();
    var samples = [_]f32{ -0.0, 0.0, 2, -3, 0.125, -0.5, 0.25, 1, -1, 0.001, 0.75, -0.25 };
    const before = samples;
    try processor.process(&samples);
    try std.testing.expectEqualSlices(u8, std.mem.asBytes(&before), std.mem.asBytes(&samples));
    try expect(processor.delay.len == 0 and processor.pending.len == 0 and processor.peaks.len == 0);
    processor.finish();
    try expect(try processor.drain(&samples) == 0);
}
test "master trim and independent calibration delay retain last impulse" {
    var config = p.Config{ .channels = 2, .master = 0.5 };
    config.trim[0] = 0.5; config.delay_frames[1] = 2;
    var processor = try p.Processor.init(std.testing.allocator, config);
    defer processor.deinit();
    var samples = [_]f32{ 1, 1, 0, 0, 1, 1 };
    try processor.process(&samples);
    try std.testing.expectEqualSlices(f32, &.{ 0.25, 0, 0, 0, 0.25, 0.5 }, &samples);
    try expect(processor.timing().tail_frames == 2);
    processor.finish(); processor.finish();
    var tail: [4]f32 = undefined;
    try expect(try processor.drain(&tail) == 2);
    try std.testing.expectEqualSlices(f32, &.{ 0, 0, 0, 0.5 }, &tail);
    try expect(try processor.drain(&tail) == 0);
    try std.testing.expectError(error.InputFinished, processor.process(&samples));
}
test "linked sample peak limiter without lookahead has no latency" {
    var processor = try p.Processor.init(std.testing.allocator, .{ .channels = 2, .limiter = true, .release_ms = 0, .ceiling = 0.5 });
    defer processor.deinit();
    var samples = [_]f32{ 2, -1, 0.25, -0.125 };
    try processor.process(&samples);
    try std.testing.expectEqualSlices(f32, &.{ 0.5, -0.25, 0.25, -0.125 }, &samples);
    processor.finish();
    try expect(try processor.drain(&samples) == 0);
}
test "lookahead reports delay, anticipates peak, drains all source frames" {
    var processor = try p.Processor.init(std.testing.allocator, .{ .channels = 1, .limiter = true, .release_ms = 0, .lookahead_frames = 2 });
    defer processor.deinit();
    var samples = [_]f32{ 0.25, 0.25, 2, 0.25 };
    try processor.process(&samples);
    try std.testing.expectEqualSlices(f32, &.{ 0, 0, 0.125, 0.125 }, &samples);
    processor.finish();
    var tail: [2]f32 = undefined;
    try expect(try processor.drain(&tail) == 2);
    try std.testing.expectEqualSlices(f32, &.{ 1, 0.25 }, &tail);
    try expect(processor.timing().lookahead_frames == 2);
}
test "disabled limiter ignores configured lookahead; empty stream has no tail" {
    var processor = try p.Processor.init(std.testing.allocator, .{ .channels = 1, .lookahead_frames = 100 });
    defer processor.deinit();
    var samples = [_]f32{2}; try processor.process(&samples);
    try expect(samples[0] == 2 and processor.timing().lookahead_frames == 0 and processor.pending.len == 0);
    var empty = try p.Processor.init(std.testing.allocator, .{ .channels = 1, .limiter = true, .lookahead_frames = 10 });
    defer empty.deinit(); empty.finish();
    try expect(try empty.drain(&samples) == 0);
}
test "chunk invariant with delay, release and lookahead" {
    var config = p.Config{ .channels = 2, .limiter = true, .ceiling = 0.8, .lookahead_frames = 3, .release_ms = 50 };
    config.delay_frames[0] = 2;
    var a = try p.Processor.init(std.testing.allocator, config); defer a.deinit();
    var b = try p.Processor.init(std.testing.allocator, config); defer b.deinit();
    var whole = [_]f32{ 0.25, 0.125, 4, 2, 0.5, -0.25, -3, 1.5, 1, 0.5, 0.2, 0.1 };
    var split = whole;
    try a.process(&whole);
    for (0..6) |frame| try b.process(split[frame * 2..][0..2]);
    try std.testing.expectEqualSlices(f32, &whole, &split);
    a.finish(); b.finish();
    var tail_a: [10]f32 = undefined; var tail_b: [10]f32 = undefined;
    try expect(try a.drain(&tail_a) == 5);
    for (0..5) |frame| try expect(try b.drain(tail_b[frame * 2..][0..2]) == 1);
    try std.testing.expectEqualSlices(f32, &tail_a, &tail_b);
    for (whole ++ tail_a) |sample| try expect(@abs(sample) <= 0.800001);
}
test "invalid block is atomic and config allocations are bounded" {
    var processor = try p.Processor.init(std.testing.allocator, .{ .channels = 2, .master = 0.5 }); defer processor.deinit();
    var bad = [_]f32{ 1, std.math.nan(f32) }; const old = bad;
    try std.testing.expectError(error.InvalidOutputSample, processor.process(&bad));
    try expect(processor.frame == 0 and !processor.has_input);
    try std.testing.expectEqualSlices(u8, std.mem.asBytes(&old), std.mem.asBytes(&bad));
    var config = p.Config{}; config.delay_frames[0] = 48001;
    try std.testing.expectError(error.InvalidOutputConfig, p.Processor.init(std.testing.allocator, config));
    config = .{ .lookahead_frames = 4801 };
    try std.testing.expectError(error.InvalidOutputConfig, p.Processor.init(std.testing.allocator, config));
}
