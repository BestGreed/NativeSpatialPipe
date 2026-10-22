//! N08 opt-in real callbacks; silence only, no endpoint/routing changes.
const std = @import("std");
const native = @import("native.zig");
const asio = @import("asio_control.zig");
const playback = @import("output_playback.zig");
const common = @import("common.zig");
const a = std.heap.page_allocator;
extern "avrt" fn AvSetMmThreadCharacteristicsW([*:0]const u16, *u32) callconv(.C) ?*anyopaque;
extern "avrt" fn AvSetMmThreadPriority(*anyopaque, c_int) callconv(.C) c_int;
extern "avrt" fn AvRevertMmThreadCharacteristics(*anyopaque) callconv(.C) c_int;
extern "kernel32" fn SetEnvironmentVariableW([*:0]const u16, ?[*:0]const u16) callconv(.C) c_int;
const SourceTask = struct {
    handle: ?*anyopaque,
    fn start() !SourceTask {
        var index: u32 = 0;
        const handle = AvSetMmThreadCharacteristicsW(std.unicode.utf8ToUtf16LeStringLiteral("Pro Audio"), &index) orelse return error.SourceMmcssRegistration;
        errdefer _ = AvRevertMmThreadCharacteristics(handle);
        if (AvSetMmThreadPriority(handle, 0) == 0) return error.SourceMmcssPriority;
        return .{ .handle = handle };
    }
    fn stop(self: *SourceTask) bool {
        const handle = self.handle orelse return true;
        if (AvRevertMmThreadCharacteristics(handle) == 0) return false;
        self.handle = null;
        return true;
    }
};
const Trace = struct {
    elapsed_ns: u64,
    callbacks: u64,
    source_frame: f64,
    buffered_frames: u64,
    starvation_frames: u64,
    rebuffers: u32,
    state: []const u8,
};
pub fn main() !void {
    const args = try std.process.argsAlloc(a);
    defer std.process.argsFree(a, args);
    if (args.len < 7 or args.len > 9) return error.Arguments;
    const seconds = if (args.len >= 8) try std.fmt.parseInt(u32, args[7], 10) else 3;
    if (seconds < 3 or seconds > 120) return error.Duration;
    const blocks: usize = seconds * 100;
    const rebuffer = std.mem.eql(u8, args[6], "rebuffer");
    const network_return = std.mem.eql(u8, args[6], "network-return");
    const output_order = if (args.len == 9) args[8] else if (network_return) "dvs-speakers" else "olr";
    if (SetEnvironmentVariableW(std.unicode.utf8ToUtf16LeStringLiteral("NSP_RENDER_MMCSS"), std.unicode.utf8ToUtf16LeStringLiteral("1")) == 0) return error.RendererSchedulingEnvironment;
    var source_task = try SourceTask.start();
    defer _ = source_task.stop();
    // Keep the instrumented module loaded through post-stop copy-out.
    var return_library: ?std.DynLib = if (network_return) try std.DynLib.open(args[2]) else null;
    defer if (return_library) |*lib| lib.close();
    const pcm_path = try std.fs.path.join(a, &.{ args[4], "renderer.f32" });
    defer a.free(pcm_path);
    var pipeline = try native.Pipeline.init(args[1], pcm_path, 64);
    // This probe owns the whole process. Keep sink storage alive until native
    // finish has joined callbacks, including all error exits.
    var owned_session: ?*playback.Session = null;
    defer {
        if (pipeline.handle != null) {
            if (pipeline.finish()) |parsed| {
                var value = parsed;
                value.deinit();
            } else |_| {
                // Do not release callback context before a proven native join.
                while (!pipeline.finished) std.time.sleep(std.time.ns_per_s);
            }
            pipeline.close();
        }
        if (owned_session) |value| {
            value.finish();
            value.destroy();
        }
    }
    // Keep the probed device rate, including 44.1/96 kHz; source stays 48 kHz.
    var config = try asio.Backend.Config.init(args[2], args[3]);
    try config.setOutputOrder(output_order);
    config.sample_rate = try std.fmt.parseFloat(f64, args[5]);
    const session = try playback.Session.create(a, config);
    owned_session = session;
    try pipeline.bindOutput(session.stream);
    std.time.sleep(200 * std.time.ns_per_ms);
    const before = session.stream.snapshot();
    var packet = std.mem.zeroes([13 * (32 + 480 * 4)]u8);
    const types = [_]u32{ 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096 };
    for (types, 0..) |typ, ch| {
        const fields = [_]u32{ @intCast(ch + 1), 480, typ, 5, 0, 0, @bitCast(@as(f32, -1)), @bitCast(@as(f32, 1)) };
        for (fields, 0..) |value, index| std.mem.writeInt(u32, packet[ch * 32 + index * 4 ..][0..4], value, .little);
    }
    var timer = try std.time.Timer.start();
    var timeline = std.ArrayList(Trace).init(a);
    defer timeline.deinit();
    var next: u64 = 0;
    var previous_feed_ns: u64 = 0;
    var max_feed_gap_ns: u64 = 0;
    var observed_rebuffer = false;
    for (0..blocks) |block| {
        const current = session.stream.snapshot();
        if (current.failure != null) break;
        const elapsed = timer.read();
        if (block > 0) max_feed_gap_ns = @max(max_feed_gap_ns, elapsed - previous_feed_ns);
        previous_feed_ns = elapsed;
        if (block % 100 == 0) try timeline.append(.{
            .elapsed_ns = elapsed, .callbacks = current.callbacks, .source_frame = current.source_frame,
            .buffered_frames = current.buffered_source_frames, .starvation_frames = current.starvation_frames,
            .rebuffers = current.rebuffers, .state = @tagName(current.state),
        });
        if (network_return) {
            for (0..480) |f| {
                const frame = block * 480 + f;
                var tone: f32 = 0;
                var active_channel: usize = 99;
                if (frame >= 24000 and frame < 24000 + 12 * 28800) {
                    const at = frame - 24000;
                    active_channel = at / 28800;
                    const within = at % 28800;
                    if (within < 19200) {
                        const hz: f64 = if (active_channel == 3) 80 else 300 + @as(f64, @floatFromInt(active_channel)) * 37;
                        const edge = @min(@min(within, 19199 - within), 960);
                        const fade = @as(f64, @floatFromInt(edge)) / 960;
                        tone = @floatCast(0.01 * fade * std.math.sin(2 * std.math.pi * hz * @as(f64, @floatFromInt(within)) / 48000));
                    }
                }
                for (0..13) |ch| {
                    const value: f32 = if (ch == active_channel + 1) tone else 0;
                    std.mem.writeInt(u32, packet[13 * 32 + (ch * 480 + f) * 4 ..][0..4], @bitCast(value), .little);
                }
            }
        }
        if (pipeline.submit(pipeline.handle, block, 13, &packet, packet.len) != 0) return error.PipelineSubmit;
        next += 10 * std.time.ns_per_ms;
        if (rebuffer and block == 149) {
            var stall = try std.time.Timer.start();
            while (session.stream.snapshot().state != .rebuffering and session.stream.snapshot().failure == null) {
                if (stall.read() > 2 * std.time.ns_per_s) break;
                std.time.sleep(std.time.ns_per_ms);
            }
            observed_rebuffer = session.stream.snapshot().state == .rebuffering;
            next = timer.read() + 10 * std.time.ns_per_ms;
        }
        const now = timer.read();
        if (next > now) std.time.sleep(next - now);
    }
    const feed_elapsed_ns = timer.read();
    var summary = try pipeline.finish();
    defer summary.deinit();
    const summary_path = try std.fs.path.join(a, &.{ args[4], "renderer-summary.json" });
    defer a.free(summary_path);
    const summary_file = try std.fs.cwd().createFile(summary_path, .{});
    defer summary_file.close();
    try std.json.stringify(summary.value, .{ .whitespace = .indent_2 }, summary_file.writer());
    pipeline.close();
    const source_reverted = source_task.stop();
    session.finish();
    if (return_library) |*lib| {
        const write = lib.lookup(*const fn ([*:0]const u8) callconv(.C) c_int, "nsp_test_return_write") orelse return error.ReturnExport;
        const directory = try a.dupeZ(u8, args[4]);
        defer a.free(directory);
        if (write(directory) != 0) return error.ReturnWrite;
    }
    const status = session.stream.snapshot();
    const owner_scheduling: ?u32 = if (return_library) |*lib| (lib.lookup(*const fn () callconv(.C) u32, "nsp_test_owner_mmcss") orelse return error.OwnerSchedulingExport)() else null;
    const detail = session.detail;
    session.destroy();
    owned_session = null;
    const renderer_scheduling = common.field(summary.value, "rendererScheduling");
    const writer_scheduling = common.field(summary.value, "outputHandoffScheduling");
    const scheduling_ok = common.yes(common.field(renderer_scheduling, "registered")) and
        common.yes(common.field(renderer_scheduling, "prioritySet")) and
        common.yes(common.field(renderer_scheduling, "reverted")) and
        common.yes(common.field(writer_scheduling, "registered")) and
        common.yes(common.field(writer_scheduling, "prioritySet")) and
        common.yes(common.field(writer_scheduling, "reverted")) and source_reverted and
        (owner_scheduling == null or owner_scheduling.? == 7);
    const passed = status.failure == null and status.state == .done and
        scheduling_ok and
        status.received_frames == blocks * 480 and status.submitted_frames == blocks * 480 and
        before.callbacks > 0 and status.callbacks > before.callbacks and before.source_frame == 0 and
        (if (rebuffer) (observed_rebuffer and status.rebuffers == 1) else status.rebuffers == 0);
    const path = try std.fs.path.join(a, &.{ args[4], "playback.json" });
    defer a.free(path);
    const file = try std.fs.cwd().createFile(path, .{});
    defer file.close();
    try std.json.stringify(.{ .passed = passed, .scenario = args[6], .driver = args[3],
        .outputOrder = output_order,
        .sourceMmcssRegistered = true, .sourceMmcssPrioritySet = true, .sourceMmcssReverted = source_reverted,
        .schedulingPassed = scheduling_ok,
        .asioOwnerSchedulingFlags = owner_scheduling,
        .outputChannelLabels = try @import("output_layout.zig").labels(output_order),
        .sourceChannelIndices = config.sources[0..12],
        .requestedSeconds = seconds, .feedElapsedNs = feed_elapsed_ns,
        .maxFeedGapNs = max_feed_gap_ns, .timeline = timeline.items,
        .state = @tagName(status.state), .callbacks = status.callbacks,
        .receivedFrames = status.received_frames, .submittedFrames = status.submitted_frames,
        .deviceSampleRate = status.device_sample_rate, .deviceBufferFrames = status.device_buffer_frames,
        .prebufferFrames = status.prebuffer_frames, .starvationFrames = status.starvation_frames,
        .rebuffers = status.rebuffers, .rebufferWaitNs = status.rebuffer_wait_ns,
        .silentStartCallbacks = before.callbacks, .silentStartSourceFrame = before.source_frame,
        .failure = if (status.failure) |err| @errorName(err) else @as(?[]const u8, null),
        .detail = std.mem.sliceTo(&detail, 0),
        .scope = if (network_return) "Actual OLR/output path with test-only real input 25..36 capture; -40 dBFS sequential tones" else "Actual OLR, common processor and owner worker; real ASIO callbacks; silent PCM, no physical return capture" },
        .{ .whitespace = .indent_2 }, file.writer());
    if (!passed) return error.RealDeviceCheckFailed;
}
