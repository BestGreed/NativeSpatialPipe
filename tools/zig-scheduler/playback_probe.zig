//! Owned end-to-end OLR -> common processing -> worker -> fake ASIO callback.
const std = @import("std");
const n = @import("native.zig");
const asio = @import("asio_control.zig");
const playback = @import("output_playback.zig");
const processing = @import("output_processing.zig");
const a = std.heap.page_allocator;
fn failUnless(value: bool) !void {
    if (!value) return error.CheckFailed;
}
pub fn main() !void {
    const args = try std.process.argsAlloc(a);
    defer std.process.argsFree(a, args);
    if (args.len != 5 and args.len != 6) return error.Arguments;
    const out = args[3];
    const scenario = args[4];
    const drain_stall = std.mem.eql(u8, scenario, "drain-stall");
    const idle_stop = std.mem.eql(u8, scenario, "idle-stop");
    try std.fs.cwd().makePath(out);
    var fake = try std.DynLib.open(args[2]);
    defer fake.close();
    const tick = fake.lookup(*const fn (u32) callconv(.C) void, "nsp_test_tick") orelse return error.TickExport;
    const copy = fake.lookup(*const fn (?[*]f32, u64) callconv(.C) u64, "nsp_test_copy") orelse return error.CopyExport;
    const reset = fake.lookup(*const fn () callconv(.C) void, "nsp_test_reset") orelse return error.ResetExport;
    const pcm_path = try std.fs.path.join(a, &.{ out, "renderer.f32" });
    defer a.free(pcm_path);
    var pipeline = try n.Pipeline.initBackend(args[1], pcm_path, 64, false, 1.0, if (args.len == 6) args[5] else null);
    var config = try asio.Backend.Config.init(args[2], "asio:test");
    config.clock_sync = std.mem.endsWith(u8, scenario, "sync");
    config.processing.master = 0.5;
    // Legacy finite limits must no longer turn normal source absence into EOF.
    config.rebuffer_timeout_ms = 30;
    config.startup_timeout_ms = 30;
    config.max_rebuffers = 1;
    if (drain_stall) config.drain_timeout_ms=30;
    if (std.mem.eql(u8, scenario, "tail")) {
        config.processing.delay_frames[0] = 7;
        config.processing.limiter = true;
        config.processing.lookahead_frames = 13;
    }
    const session = try playback.Session.create(a, config);
    try pipeline.bindOutput(session.stream);
    tick(2); // Pre-Prime callbacks must remain silent and not consume source.
    const idle_gap = std.mem.startsWith(u8, scenario, "idle-gap");
    const idle_start = std.mem.startsWith(u8, scenario, "idle-start") or idle_stop;
    const repeat = std.mem.eql(u8, scenario, "repeat");
    const stall = std.mem.eql(u8, scenario, "rebuffer") or std.mem.eql(u8, scenario, "rebuffer-sync") or idle_gap or repeat;
    const device_fault = std.mem.eql(u8, scenario, "device-fault");
    var packet: [13 * (32 + 480 * 4)]u8 = undefined;
    const types = [_]u32{ 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096 };
    const blocks: u64 = if (idle_stop) 0 else 16;
    if (idle_start) {
        var waiting = try std.time.Timer.start();
        while (waiting.read() < 80 * std.time.ns_per_ms) {
            tick(1);std.time.sleep(std.time.ns_per_ms);
            try failUnless(session.stream.snapshot().failure == null);
        }
        const idle=session.stream.snapshot();
        try failUnless(idle.state==.waiting and idle.received_frames==0 and idle.source_frame==0 and idle.callbacks>0);
        const count=copy(null,0);const samples=try a.alloc(f32,@intCast(count));defer a.free(samples);
        try failUnless(copy(samples.ptr,count)==count);
        for(samples) |sample| try failUnless(sample==0);
    }
    for (0..@intCast(blocks)) |block| {
        for (types, 0..) |typ, ch| {
            const at = ch * 32;
            const fields = [_]u32{ @intCast(ch + 1), 480, typ, if (block == 0) 5 else 4, @bitCast(@as(f32, 2)), @bitCast(@as(f32, 1)), @bitCast(@as(f32, -3)), @bitCast(@as(f32, 1)) };
            for (fields, 0..) |value, index| std.mem.writeInt(u32, packet[at + index * 4 ..][0..4], value, .little);
            for (0..480) |f| {
                const value: f32 = 0.01 + @as(f32, @floatFromInt(ch)) * 0.002 + @as(f32, @floatFromInt(block * 480 + f)) * 0.000001;
                std.mem.writeInt(u32, packet[13 * 32 + (ch * 480 + f) * 4 ..][0..4], @bitCast(value), .little);
            }
        }
        if (pipeline.submit(pipeline.handle, block, 13, &packet, packet.len) != 0) return error.PipelineSubmit;
        // Let the real renderer/writer complete its borrowed callback before checking.
        var timer = try std.time.Timer.start();
        while (session.stream.snapshot().received_frames < (block + 1) * 480) {
            if (timer.read() > 3 * std.time.ns_per_s) return error.SinkTimeout;
            std.time.sleep(std.time.ns_per_ms);
        }
        if (stall and (block == 5 or (repeat and (block == 9 or block == 13)))) {
            timer = try std.time.Timer.start();
            while (session.stream.snapshot().state == .rebuffering) {
                if (timer.read() > 2 * std.time.ns_per_s) return error.ResumeNotObserved;
                std.time.sleep(std.time.ns_per_ms);
            }
        }
        if (stall and (block == 3 or (repeat and (block == 7 or block == 11)))) {
            timer = try std.time.Timer.start();
            while (session.stream.snapshot().state != .rebuffering and session.stream.snapshot().failure == null) {
                tick(1);
                std.time.sleep(std.time.ns_per_ms);
                if (timer.read() > 3 * std.time.ns_per_s) return error.RebufferNotObserved;
            }
            if (idle_gap or repeat) {
                const begin=copy(null,0);
                const prior=session.stream.snapshot();
                timer=try std.time.Timer.start();
                while (timer.read() < 80 * std.time.ns_per_ms) {
                    tick(1);
                    std.time.sleep(std.time.ns_per_ms);
                    try failUnless(session.stream.snapshot().failure==null);
                }
                const idle=session.stream.snapshot();
                try failUnless(idle.state==.rebuffering and idle.callbacks>prior.callbacks and idle.submitted_frames==prior.submitted_frames);
                const count=copy(null,0);const samples=try a.alloc(f32,@intCast(count));defer a.free(samples);
                try failUnless(copy(samples.ptr,count)==count);
                for(samples[@intCast(begin)..]) |sample| try failUnless(sample==0);
            }
            if (session.stream.snapshot().failure != null) break;
        }
        if (device_fault and block == 3) {
            reset();
            break;
        }
    }
    var summary = try pipeline.finish();
    defer summary.deinit();
    pipeline.close();
    session.stream.finish();
    var timer = try std.time.Timer.start();
    while (session.stream.snapshot().state != .done and session.stream.snapshot().failure == null) {
        if (!drain_stall) tick(1);
        std.time.sleep(std.time.ns_per_ms);
        if (timer.read() > 6 * std.time.ns_per_s) return error.PlaybackTimeout;
    }
    // Native stop waits for its already queued ring. Keep callbacks alive while
    // cleanup runs, protected by the fake driver's lifetime lock.
    const Ticker = struct {
        fn run(stop: *std.atomic.Value(bool), f: *const fn (u32) callconv(.C) void) void {
            while (!stop.load(.acquire)) {
                f(1);
                std.time.sleep(std.time.ns_per_ms);
            }
        }
    };
    var stop = std.atomic.Value(bool).init(false);
    const ticker = try std.Thread.spawn(.{}, Ticker.run, .{ &stop, tick });
    session.finish();
    stop.store(true, .release);
    ticker.join();
    const status = session.stream.snapshot();
    try failUnless(status.pool_blocks_received == status.pool_blocks_released);
    session.destroy();
    std.debug.print("{s}: {s}, received={d}, submitted={d}, rebuffers={d}, failure={s}\n", .{ scenario, @tagName(status.state), status.received_frames, status.submitted_frames, status.rebuffers, if (status.failure) |err| @errorName(err) else "none" });
    const want_fault = device_fault or drain_stall;
    try failUnless((status.failure != null) == want_fault);
    if (!want_fault) try failUnless(status.state == .done and status.received_frames == blocks * 480 + @as(u64, if (args.len == 6) 512 else 0));
    if (std.mem.eql(u8, scenario, "rebuffer") or std.mem.eql(u8, scenario, "rebuffer-sync")) try failUnless(status.rebuffers == 1 and status.rebuffer_wait_ns > 0);
    if (repeat) try failUnless(status.rebuffers >= 3 and status.rebuffers > config.max_rebuffers);
    if (drain_stall) try failUnless(status.failure.?==error.OutputTimeout);
    const size = copy(null, 0);
    const recorded = try a.alloc(f32, @intCast(size));
    defer a.free(recorded);
    try failUnless(copy(recorded.ptr, size) == size);
    const captured_path = try std.fs.path.join(a, &.{ out, "callback.f32" });
    defer a.free(captured_path);
    try std.fs.cwd().writeFile(.{ .sub_path = captured_path, .data = std.mem.sliceAsBytes(recorded) });
    if (!want_fault and !config.clock_sync and blocks>0) {
        const raw = try std.fs.cwd().readFileAlloc(a, pcm_path, (16 * 480 + 512) * 12 * 4);
        defer a.free(raw);
        const expected = try a.alloc(f32, raw.len / 4);
        defer a.free(expected);
        @memcpy(std.mem.sliceAsBytes(expected), raw);
        var processor = try processing.Processor.init(a, config.processing);
        defer processor.deinit();
        try processor.process(expected);
        processor.finish();
        const tail = try a.alloc(f32, processor.timing().tail_frames * 12);
        defer a.free(tail);
        if (tail.len > 0) try failUnless(try processor.drain(tail) == tail.len / 12);
        // Compare all non-padding source frames exactly. This deterministic
        // fixture has nonzero beds; inserted callback silence is counted separately.
        var r: usize = 0;
        var expected_frames: usize = 0;
        for ([_][]f32{ expected, tail }) |part| {
            var i: usize = 0;
            while (i < part.len) : (i += 12) {
                const frame = part[i..][0..12];
                var nonzero = false;
                for (frame) |v| {
                    if (v != 0) nonzero = true;
                }
                if (!nonzero) continue;
                while (r < recorded.len) {
                    var has = false;
                    for (recorded[r..][0..12]) |v| {
                        if (v != 0) has = true;
                    }
                    if (has) break;
                    r += 12;
                }
                try failUnless(r + 12 <= recorded.len);
                try failUnless(std.mem.eql(u8, std.mem.sliceAsBytes(frame), std.mem.sliceAsBytes(recorded[r..][0..12])));
                r += 12;
                expected_frames += 1;
            }
        }
        while (r < recorded.len) : (r += 12) for (recorded[r..][0..12]) |v| try failUnless(v == 0);
        try failUnless(expected_frames > 0);
    }
    const report = try std.fs.path.join(a, &.{ out, "playback.json" });
    defer a.free(report);
    const file = try std.fs.cwd().createFile(report, .{});
    defer file.close();
    try std.json.stringify(.{ .scenario = scenario, .passed = true, .state = @tagName(status.state), .receivedFrames = status.received_frames, .submittedFrames = status.submitted_frames, .callbacks = status.callbacks, .starvationFrames = status.starvation_frames, .rebuffers = status.rebuffers, .failure = if (status.failure) |err| @errorName(err) else @as(?[]const u8, null), .deviceSampleRate = status.device_sample_rate, .deviceBufferFrames = status.device_buffer_frames, .calibrationMaxFrames = status.calibration_max_frames, .lookaheadFrames = status.lookahead_frames, .poolBlocksReceived=status.pool_blocks_received,.poolBlocksReleased=status.pool_blocks_released,.dspTailFrames=status.dsp_tail_frames, .prebufferFrames = status.prebuffer_frames, .rebufferWaitNs = status.rebuffer_wait_ns, .exactPcmCompared = !want_fault and !config.clock_sync and blocks>0, .scope = "Owned renderer pipeline and fake ASIO callback only" }, .{ .whitespace = .indent_2 }, file.writer());
}
