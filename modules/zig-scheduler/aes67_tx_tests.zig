const std = @import("std");
const c = @import("common.zig").c;
const rtp = @import("aes67_rtp.zig");
const cfg = @import("aes67_config.zig");
const tx = @import("aes67_tx.zig");

fn ringValues(count: usize, channels: usize) []f32 {
    const values = std.testing.allocator.alloc(f32, count * channels) catch unreachable;
    for (0..count) |frame| {
        for (0..channels) |channel| values[frame * channels + channel] = @floatFromInt(frame * 10 + channel);
    }
    return values;
}

test "bounded ring preserves order, wraps and returns oldest frames on overflow" {
    var ring = try tx.FrameRing.init(std.testing.allocator, 96, 2);
    defer ring.deinit();
    const input = ringValues(96, 2);
    defer std.testing.allocator.free(input);
    try std.testing.expectEqual(@as(usize, 0), try ring.push(input[0 .. 48 * 2], 48, 2, &.{ 0, 1 }));
    var output = [_]f32{0} ** (48 * 2);
    try std.testing.expect(ring.popInto(&output, 24));
    try std.testing.expectEqual(@as(f32, 0), output[0]);
    try std.testing.expectEqual(@as(f32, 1), output[1]);
    try std.testing.expectEqual(@as(f32, 230), output[46]);
    try std.testing.expectEqual(@as(f32, 231), output[47]);
    // 48 - 24 = 24 buffered; push 48 more wraps the ring to 72 frames.
    try std.testing.expectEqual(@as(usize, 0), try ring.push(input[48 * 2 .. 96 * 2], 48, 2, &.{ 0, 1 }));
    try std.testing.expectEqual(@as(usize, 72), ring.buffered());
    try std.testing.expect(ring.popInto(&output, 48));
    try std.testing.expectEqual(@as(f32, 240), output[0]);
}

test "ring overflow discards the oldest frames and keeps bounded latency" {
    var ring = try tx.FrameRing.init(std.testing.allocator, 48, 2);
    defer ring.deinit();
    const input = ringValues(48, 2);
    defer std.testing.allocator.free(input);
    try std.testing.expectEqual(@as(usize, 0), try ring.push(input, 48, 2, &.{ 0, 1 }));
    const extra = ringValues(4, 2);
    defer std.testing.allocator.free(extra);
    try std.testing.expectEqual(@as(usize, 4), try ring.push(extra, 4, 2, &.{ 0, 1 }));
    try std.testing.expectEqual(@as(usize, 48), ring.buffered());
    var output = [_]f32{0} ** (48 * 2);
    try std.testing.expect(ring.popInto(&output, 1));
    try std.testing.expectEqual(@as(f32, 40), output[0]);
    try std.testing.expectEqual(@as(f32, 41), output[1]);
}

test "ring applies the caller channel map and rejects invalid maps" {
    var ring = try tx.FrameRing.init(std.testing.allocator, 48, 2);
    defer ring.deinit();
    const input = [_]f32{ 0.25, -0.25 } ** 48;
    try std.testing.expectEqual(@as(usize, 0), try ring.push(&input, 48, 2, &.{ 1, 0 }));
    var output = [_]f32{0} ** (48 * 2);
    try std.testing.expect(ring.popInto(&output, 48));
    try std.testing.expectEqual(@as(f32, -0.25), output[0]);
    try std.testing.expectEqual(@as(f32, 0.25), output[1]);
    try std.testing.expectEqual(@as(f32, -0.25), output[94]);
    try std.testing.expectError(error.Aes67ChannelMap, ring.push(&input, 48, 2, &.{ 0, 2 }));
    try std.testing.expectError(error.Aes67RingFrames, ring.push(&input, 49, 2, &.{ 0, 1 }));
    var small = [_]f32{0} ** (48 * 2);
    try std.testing.expect(!ring.popInto(&small, 49));
}

test "absolute pacer sends whole periods, catches up in bounded bursts and never drifts" {
    var pacer = tx.Pacer.init(1_000_000, 1_000_000);
    try std.testing.expectEqual(@as(u32, 0), pacer.due(999_999));
    try std.testing.expectEqual(@as(u32, 1), pacer.due(1_000_000));
    try std.testing.expectEqual(@as(u32, 0), pacer.due(1_500_000));
    try std.testing.expectEqual(@as(u32, 1), pacer.due(2_000_000));
    var late = tx.Pacer.init(0, 1_000_000);
    try std.testing.expectEqual(@as(u32, 4), late.due(3_400_000));
    try std.testing.expectEqual(@as(u64, 0), late.late_ticks);
    var stalled = tx.Pacer.init(0, 1_000_000);
    try std.testing.expectEqual(@as(u32, 64), stalled.due(100_000_000));
    try std.testing.expectEqual(@as(u64, 1), stalled.late_ticks);
    try std.testing.expectEqual(@as(u32, 0), stalled.due(100_000_000));
    try std.testing.expectEqual(@as(u32, 1), stalled.due(101_000_000));
    // OS wake-up jitter inside one period must not accumulate into drift.
    var steady = tx.Pacer.init(0, 1_000_000);
    var sent: u64 = 0;
    var now: u64 = 0;
    for (0..1000) |index| {
        now = @as(u64, index + 1) * 1_000_000 + @as(u64, (index * 137) % 400_000);
        sent += steady.due(now);
    }
    try std.testing.expectEqual(now / 1_000_000 + 1, sent);
    try std.testing.expectEqual(@as(u64, 0), steady.late_ticks);
}

test "fractional tick periods keep the average cadence exact" {
    // QPC frequency 3579545 Hz is not a multiple of 1000: 3579 ticks plus a
    // 545/1000 remainder per millisecond. After 1000 periods the deadline must
    // be start + 3579545 ticks, not start + 3579000.
    var pacer = tx.Pacer.initWithFraction(0, 3579, 545, 1000);
    var sent: u64 = 0;
    for (0..1000) |_| {
        sent += pacer.due(pacer.deadline);
    }
    try std.testing.expectEqual(@as(u64, 1000), sent);
    try std.testing.expectEqual(@as(u64, 3_579_545), pacer.deadline);
    try std.testing.expectEqual(@as(u64, 0), pacer.late_ticks);
}

fn addressValue(address: [4]u8) u32 {
    return @as(u32, address[0]) | (@as(u32, address[1]) << 8) | (@as(u32, address[2]) << 16) | (@as(u32, address[3]) << 24);
}

fn bindLoopbackReceiver() !struct { sock: c.SOCKET, port: u16 } {
    const sock = c.socket(c.AF_INET, c.SOCK_DGRAM, c.IPPROTO_UDP);
    if (sock == c.INVALID_SOCKET) return error.Aes67LoopbackSocket;
    errdefer _ = c.closesocket(sock);
    var address = std.mem.zeroes(c.sockaddr_in);
    address.sin_family = @intCast(c.AF_INET);
    address.sin_port = 0;
    address.sin_addr.S_un.S_addr = addressValue(.{ 127, 0, 0, 1 });
    if (c.bind(sock, @ptrCast(&address), @sizeOf(c.sockaddr_in)) != 0) return error.Aes67LoopbackBind;
    var length: c_int = @sizeOf(c.sockaddr_in);
    if (c.getsockname(sock, @ptrCast(&address), &length) != 0) return error.Aes67LoopbackName;
    const timeout: c_int = 500;
    _ = c.setsockopt(sock, c.SOL_SOCKET, c.SO_RCVTIMEO, @ptrCast(&timeout), @intCast(@sizeOf(@TypeOf(timeout))));
    return .{ .sock = sock, .port = c.ntohs(address.sin_port) };
}

test "loopback sender emits contiguous RTP/L24 packets with the mapped channels and silence on underrun" {
    if (std.process.getEnvVarOwned(std.testing.allocator, "NSP_AES67_LOOPBACK_TEST")) |value| {
        std.testing.allocator.free(value);
    } else |_| return error.SkipZigTest;
    var ws: c.WSADATA = undefined;
    if (c.WSAStartup(0x0202, &ws) != 0) return error.Aes67LoopbackWinsock;
    defer _ = c.WSACleanup();
    const receiver = try bindLoopbackReceiver();
    defer _ = c.closesocket(receiver.sock);
    const prepared = try cfg.prepare(.{
        .destIp = "127.0.0.1",
        .port = receiver.port,
        .srcInterfaceIp = "127.0.0.1",
        .prebufferFrames = 480,
        .ringPackets = 64,
    }, 2);
    const sender = try tx.Tx.create(std.testing.allocator, prepared);
    defer sender.destroy();
    var input = [_]f32{0} ** (48 * 10 * 2);
    for (0..480) |frame| {
        input[frame * 2] = 0.25;
        input[frame * 2 + 1] = -0.25;
    }
    try sender.submit(&input, 480, 2, &.{ 1, 0 });
    try sender.start();
    var packets: usize = 0;
    var first_seq: ?u16 = null;
    var last_seq: ?u16 = null;
    var first_arrival: u64 = 0;
    var last_arrival: u64 = 0;
    var timer = try std.time.Timer.start();
    var first_payload_checked = false;
    var silent_seen = false;
    var buffer: [2048]u8 = undefined;
    while (packets < 32) {
        var from = std.mem.zeroes(c.sockaddr_in);
        var from_length: c_int = @sizeOf(c.sockaddr_in);
        const received = c.recvfrom(receiver.sock, &buffer, @intCast(buffer.len), 0, @ptrCast(&from), &from_length);
        if (received <= 0) break;
        const size: usize = @intCast(received);
        if (size != rtp.packetBytes(48, 2)) return error.Aes67LoopbackSize;
        if (buffer[0] != 0x80 or buffer[1] != 96) return error.Aes67LoopbackHeader;
        if (!std.mem.eql(u8, buffer[8..12], &.{ 0x4E, 0x53, 0x50, 0x36 })) return error.Aes67LoopbackSsrc;
        const sequence = (@as(u16, buffer[2]) << 8) | buffer[3];
        const timestamp = (@as(u32, buffer[4]) << 24) | (@as(u32, buffer[5]) << 16) | (@as(u32, buffer[6]) << 8) | buffer[7];
        if (first_seq) |_| {
            if (last_seq) |last| if (sequence != last +% 1) return error.Aes67LoopbackSequence;
        } else {
            first_seq = sequence;
            first_arrival = timer.read();
        }
        last_seq = sequence;
        last_arrival = timer.read();
        try std.testing.expectEqual(@as(u32, @as(u32, sequence) * 48), timestamp);
        if (!first_payload_checked) {
            // Transmitted channel 0 is source channel 1: -0.25 -> E0 00 00.
            try std.testing.expectEqualSlices(u8, &.{ 0xE0, 0x00, 0x00 }, buffer[12..15]);
            try std.testing.expectEqualSlices(u8, &.{ 0x20, 0x00, 0x00 }, buffer[15..18]);
            first_payload_checked = true;
        } else if (packets >= 10) {
            for (buffer[12..size]) |byte| if (byte != 0) return error.Aes67LoopbackSilence;
            silent_seen = true;
        }
        packets += 1;
    }
    try std.testing.expect(packets >= 12);
    try std.testing.expect(first_payload_checked);
    try std.testing.expect(silent_seen);
    const elapsed_ns = last_arrival - first_arrival;
    const average_ns = elapsed_ns / packets;
    try std.testing.expect(average_ns >= 300 * std.time.ns_per_us and average_ns <= 3 * std.time.ns_per_ms);
    sender.stop();
    const stats = sender.snapshot();
    try std.testing.expect(stats.packets_sent >= 12);
    try std.testing.expect(stats.silent_packets > 0);
    try std.testing.expect(stats.frames_submitted == 480);
    try std.testing.expect(stats.send_errors == 0);
    try std.testing.expect(stats.last_seq == @as(u32, last_seq.?));
    try std.testing.expect(stats.buffered_frames == 0);
}
