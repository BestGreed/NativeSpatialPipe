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
        .srcInterfaceIp = "127.0.0.1",
        .prebufferFrames = 480,
        .ringPackets = 64,
        .flows = &.{.{ .destIp = "127.0.0.1", .port = receiver.port, .channels = 2 }},
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


/// Receive one RTP packet from a loopback receiver and decode the header fields.
const LoopbackPacket = struct {
    size: usize,
    sequence: u16,
    timestamp: u32,
    ssrc: u32,
    payload_type: u8,
    source_port: u16,
    payload_first_sample: [3]u8,
    payload_all_zero: bool,
};

fn recvLoopbackPacket(sock: c.SOCKET) !LoopbackPacket {
    var buffer: [2048]u8 = undefined;
    var from = std.mem.zeroes(c.sockaddr_in);
    var from_length: c_int = @sizeOf(c.sockaddr_in);
    const received = c.recvfrom(sock, &buffer, @intCast(buffer.len), 0, @ptrCast(&from), &from_length);
    if (received <= 0) return error.Aes67LoopbackTimeout;
    const size: usize = @intCast(received);
    if (size < 15) return error.Aes67LoopbackSize;
    var all_zero = true;
    for (buffer[12..size]) |byte| {
        if (byte != 0) {
            all_zero = false;
            break;
        }
    }
    return .{
        .size = size,
        .sequence = (@as(u16, buffer[2]) << 8) | buffer[3],
        .timestamp = (@as(u32, buffer[4]) << 24) | (@as(u32, buffer[5]) << 16) | (@as(u32, buffer[6]) << 8) | buffer[7],
        .ssrc = (@as(u32, buffer[8]) << 24) | (@as(u32, buffer[9]) << 16) | (@as(u32, buffer[10]) << 8) | buffer[11],
        .payload_type = buffer[1],
        .source_port = c.ntohs(from.sin_port),
        .payload_first_sample = .{ buffer[12], buffer[13], buffer[14] },
        .payload_all_zero = all_zero,
    };
}

test "multi-flow loopback sends 2 x 8ch with one shared timestamp timeline and per-flow seq/ssrc" {
    if (std.process.getEnvVarOwned(std.testing.allocator, "NSP_AES67_LOOPBACK_TEST")) |value| {
        std.testing.allocator.free(value);
    } else |_| return error.SkipZigTest;
    var ws: c.WSADATA = undefined;
    if (c.WSAStartup(0x0202, &ws) != 0) return error.Aes67LoopbackWinsock;
    defer _ = c.WSACleanup();
    const receiver0 = try bindLoopbackReceiver();
    defer _ = c.closesocket(receiver0.sock);
    const receiver1 = try bindLoopbackReceiver();
    defer _ = c.closesocket(receiver1.sock);
    const prepared = try cfg.prepare(.{
        .srcInterfaceIp = "127.0.0.1",
        .prebufferFrames = 480,
        .ringPackets = 64,
        .flows = &.{
            .{ .destIp = "127.0.0.1", .port = receiver0.port, .channels = 8, .channelOffset = 0 },
            .{ .destIp = "127.0.0.1", .port = receiver1.port, .channels = 8, .channelOffset = 8 },
        },
    }, 16);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    const sender = try tx.Tx.create(std.testing.allocator, prepared);
    defer sender.destroy();
    // One 10 ms block: wire channel k = 0.01 * (k+1); identity channel map.
    var input = [_]f32{0} ** (480 * 16);
    for (0..480) |frame| {
        for (0..16) |channel| input[frame * 16 + channel] = @as(f32, @floatFromInt(channel + 1)) * 0.01;
    }
    const identity = [_]u16{ 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 };
    try sender.submit(&input, 480, 16, &identity);
    try sender.start();

    var packets0: [24]LoopbackPacket = undefined;
    var packets1: [24]LoopbackPacket = undefined;
    for (0..24) |index| {
        packets0[index] = try recvLoopbackPacket(receiver0.sock);
        packets1[index] = try recvLoopbackPacket(receiver1.sock);
    }
    // Each flow carries exactly 48 frames x 8ch x 3 B + 12 B RTP header.
    for (packets0) |packet| try std.testing.expectEqual(@as(usize, 12 + 1152), packet.size);
    for (packets1) |packet| try std.testing.expectEqual(@as(usize, 12 + 1152), packet.size);
    // Same mediaTime packet from both flows must carry the SAME RTP timestamp.
    for (0..24) |index| {
        try std.testing.expectEqual(packets0[index].timestamp, packets1[index].timestamp);
        try std.testing.expectEqual(@as(u8, 96), packets0[index].payload_type);
        try std.testing.expectEqual(@as(u8, 96), packets1[index].payload_type);
    }
    // Flows have independent sequence numbers that are each contiguous.
    for (1..24) |index| {
        try std.testing.expectEqual(packets0[index - 1].sequence +% 1, packets0[index].sequence);
        try std.testing.expectEqual(packets1[index - 1].sequence +% 1, packets1[index].sequence);
    }
    // Independent SSRCs ("NSP6" and "NSP6"+1 by default).
    try std.testing.expectEqual(cfg.default_ssrc, packets0[0].ssrc);
    try std.testing.expectEqual(cfg.default_ssrc + 1, packets1[0].ssrc);
    // Wire channel mapping: flow0 ch0..7 = source 0..7, flow1 ch0..7 = source 8..15.
    var expected: [3]u8 = undefined;
    rtp.encodeL24(0.01, &expected);
    try std.testing.expectEqualSlices(u8, &expected, &packets0[0].payload_first_sample);
    rtp.encodeL24(0.09, &expected);
    try std.testing.expectEqualSlices(u8, &expected, &packets1[0].payload_first_sample);
    // Only the first 10 packets carry submitted audio; the rest is L24 silence
    // (per flow, never a half block).
    for (10..24) |index| {
        try std.testing.expect(packets0[index].payload_all_zero);
        try std.testing.expect(packets1[index].payload_all_zero);
    }
    sender.stop();
    const stats = sender.snapshot();
    try std.testing.expectEqual(@as(u8, 2), stats.flow_count);
    try std.testing.expectEqual(@as(u32, 16), stats.channels);
    try std.testing.expect(stats.flows[0].packets_sent >= 12);
    try std.testing.expect(stats.flows[1].packets_sent >= 12);
    try std.testing.expectEqual(@as(u64, 0), stats.send_errors);
    try std.testing.expectEqual(@as(u32, packets0[23].sequence), stats.flows[0].last_seq);
    try std.testing.expectEqual(packets0[23].timestamp, stats.flows[1].last_timestamp);
}
const active = @import("aes67_route.zig");
const Backend = @import("aes67_control.zig").Backend;
const Stream = @import("output_stream.zig").Stream;

fn releaseTestLease(token: ?*anyopaque) callconv(.C) void {
    const count: *usize = @ptrCast(@alignCast(token.?));
    count.* += 1;
}

const PairReceiver = struct {
    sockets: [2]c.SOCKET,
    last: ?[2]LoopbackPacket = null,

    fn read(self: *PairReceiver) ![2]LoopbackPacket {
        const packets = [2]LoopbackPacket{ try recvLoopbackPacket(self.sockets[0]), try recvLoopbackPacket(self.sockets[1]) };
        try std.testing.expectEqual(packets[0].timestamp, packets[1].timestamp);
        for (packets, 0..) |packet, index| {
            try std.testing.expectEqual(rtp.packetBytes(48, 1), packet.size);
            try std.testing.expectEqual(cfg.default_ssrc + @as(u32, @intCast(index)), packet.ssrc);
            if (self.last) |last| {
                try std.testing.expectEqual(last[index].sequence +% 1, packet.sequence);
                try std.testing.expectEqual(last[index].timestamp +% 48, packet.timestamp);
                try std.testing.expectEqual(last[index].source_port, packet.source_port);
            }
        }
        self.last = packets;
        return packets;
    }

    fn expectSample(packets: [2]LoopbackPacket, samples: [2]f32) !void {
        for (packets, samples) |packet, sample| {
            var expected: [3]u8 = undefined;
            rtp.encodeL24(sample, &expected);
            try std.testing.expectEqualSlices(u8, &expected, &packet.payload_first_sample);
        }
    }
};

test "active route sends one source across both RTP flows, silences loss, and resumes in session" {
    if (std.process.getEnvVarOwned(std.testing.allocator, "NSP_AES67_LOOPBACK_TEST")) |value| {
        std.testing.allocator.free(value);
    } else |_| return error.SkipZigTest;
    var ws: c.WSADATA = undefined;
    if (c.WSAStartup(0x0202, &ws) != 0) return error.Aes67LoopbackWinsock;
    defer _ = c.WSACleanup();
    const first = try bindLoopbackReceiver();
    defer _ = c.closesocket(first.sock);
    const second = try bindLoopbackReceiver();
    defer _ = c.closesocket(second.sock);
    var pairs = PairReceiver{ .sockets = .{ first.sock, second.sock } };

    const prepared = try cfg.prepare(.{
        .srcInterfaceIp = "127.0.0.1",
        .prebufferFrames = 480,
        .ringPackets = 64,
        .flows = &.{
            .{ .destIp = "127.0.0.1", .port = first.port, .channels = 1, .channelOffset = 0 },
            .{ .destIp = "127.0.0.1", .port = second.port, .channels = 1, .channelOffset = 1 },
        },
    }, 2);
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const dir_path = try tmp.dir.realpathAlloc(std.testing.allocator, ".");
    defer std.testing.allocator.free(dir_path);
    const sdp_path = try std.fs.path.join(std.testing.allocator, &.{ dir_path, "test.sdp" });
    defer std.testing.allocator.free(sdp_path);
    const stream = try Stream.create(std.testing.allocator);
    defer stream.destroy();
    var route: active.Route = undefined;
    route.init(2, 500);
    var config = Backend.Config{ .prepared = prepared, .stream = stream, .route = &route, .drain_timeout_ms = 500 };
    @memcpy(config.sdp_path[0..sdp_path.len], sdp_path);
    const session_name = "NativeSpatialPipe AES67 test output";
    @memcpy(config.session_name[0..session_name.len], session_name);
    var backend = Backend{};
    try backend.open(config);
    defer backend.close() catch unreachable;
    defer backend.stop() catch unreachable;
    const pcm_a = [_]f32{ 0.25, 0.125 } ** 480;
    const pcm_b = [_]f32{ -0.25, -0.125 } ** 480;
    var released: usize = 0;
    const expect = std.testing.expectEqual;

    var routes = @import("routing.zig").Routes{};
    route.select(routes.update(0, .{ 0, 0, null, null }));
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[0], 0, &pcm_a, 480, 2, 0, &released, releaseTestLease));
    try expect(@as(usize, 1), released);
    route.select(routes.update(100, .{ 1, 0, null, null }));
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[0], 480, &pcm_a, 480, 2, 0, &released, releaseTestLease));
    try backend.pump();
    try expect(@as(?u32, 0), stream.snapshot().net_selected_source);
    try expect(@as(u64, 480), backend.tx.?.snapshot().buffered_frames);
    route.select(routes.update(200, .{ 2, 1, null, null }));
    // Leave a descriptor from A pending as well as the earlier A PCM in Tx.
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[0], 960, &pcm_a, 480, 2, 0, &released, releaseTestLease));
    route.select(routes.update(300, .{ 3, 2, null, null }));
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[1], 0, &pcm_b, 480, 2, 0, &released, releaseTestLease));
    try backend.pump();
    try expect(@as(?u32, 1), stream.snapshot().net_selected_source);
    try expect(@as(u64, 480), backend.tx.?.snapshot().ring_discarded_frames);
    try expect(@as(u64, 0), backend.tx.?.snapshot().ring_dropped_frames);
    try expect(@as(u64, 480), backend.tx.?.snapshot().buffered_frames);
    try backend.start();
    try PairReceiver.expectSample(try pairs.read(), .{ -0.25, -0.125 });
    route.select(routes.update(1400, .{ 3, 2, null, null }));
    try backend.pump();
    try expect(@as(?u32, null), stream.snapshot().net_selected_source);
    for (0..10) |_| {
        const packets = try pairs.read();
        if (packets[0].payload_all_zero) {
            try std.testing.expect(packets[1].payload_all_zero);
            break;
        }
        try PairReceiver.expectSample(packets, .{ -0.25, -0.125 });
    } else return error.Aes67MissingRoutedSilence;
    route.select(routes.update(1500, .{ 4, 2, null, null }));
    // Input B can end while A is selected. It must not stop the sender.
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[1], 480, &pcm_b, 48, 2, 1, &released, releaseTestLease));
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[0], 1440, &pcm_a, 480, 2, 0, &released, releaseTestLease));
    try backend.pump();
    try expect(@as(?u32, 0), stream.snapshot().net_selected_source);
    try std.testing.expect(!route.ended());
    for (0..128) |_| {
        const packets = try pairs.read();
        if (!packets[0].payload_all_zero) {
            try PairReceiver.expectSample(packets, .{ 0.25, 0.125 });
            break;
        }
        try std.testing.expect(packets[1].payload_all_zero);
    } else return error.Aes67MissingRecoveredInput;
    // Deliberately leave a partial RTP packet after the selected tail. The
    // complete audio packets drain before the owner drops only the remainder.
    try expect(@as(c_int, 0), active.publishPool(&route.inputs[0], 1920, &pcm_a, 49, 2, 1, &released, releaseTestLease));
    route.finishing.store(true, .release);
    var drain_timer = try std.time.Timer.start();
    while (stream.snapshot().state != .done and drain_timer.read() < std.time.ns_per_s) {
        try backend.pump();
        std.time.sleep(std.time.ns_per_ms);
    }
    try expect(@import("output_stream.zig").State.done, stream.snapshot().state);
    try expect(@as(u64, 0), backend.tx.?.snapshot().buffered_frames);
    try expect(@as(usize, 7), released);
    try expect(route.stats().received, route.stats().released);
}

test "multicast flow applies TTL/egress options and requires an explicit source" {
    const prepared = try cfg.prepare(.{
        .payloadType = 96,
        .srcInterfaceIp = "127.0.0.1",
        .ringPackets = 64,
        .prebufferFrames = 480,
        .ttl = 32,
        .flows = &.{.{ .destIp = "239.1.0.7", .port = 5008, .channels = 7 }},
    }, 7);
    try std.testing.expect(prepared.multicast);
    const sender = try tx.Tx.create(std.testing.allocator, prepared);
    defer sender.destroy();
    const stats = sender.snapshot();
    try std.testing.expect(stats.flows[0].multicast);
    try std.testing.expectEqual(@as(u8, 32), stats.flows[0].ttl);
    // A multicast flow without a resolved egress interface must fail closed
    // before any media is sent (the desktop can have several NICs). Egress is
    // resolved per flow, so both the session mirror and the flow's own value
    // must be cleared to hit the check.
    var missing = prepared;
    missing.src_interface_ip = null;
    missing.flows[0].source_ip = null;
    try std.testing.expectError(error.Aes67MulticastRequiresSourceInterface, tx.Tx.create(std.testing.allocator, missing));
}

test "per-flow egress resolves each flow socket from its own source interface" {
    // Plan A shape: two multicast groups share the default port; flow 0
    // inherits the session egress, flow 1 carries its own per-flow override.
    const prepared = try cfg.prepare(.{
        .srcInterfaceIp = "127.0.0.1",
        .ringPackets = 64,
        .prebufferFrames = 480,
        .ttl = 32,
        .flows = &.{
            .{ .destIp = "239.1.0.1", .channels = 8, .channelOffset = 0 },
            .{ .destIp = "239.1.0.2", .channels = 8, .channelOffset = 8, .srcInterfaceIp = "127.0.0.1" },
        },
    }, 16);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[1].port);
    try std.testing.expectEqual(@as(?[4]u8, .{ 127, 0, 0, 1 }), prepared.flows[0].source_ip);
    try std.testing.expectEqual(@as(?[4]u8, .{ 127, 0, 0, 1 }), prepared.flows[1].source_ip);
    const sender = try tx.Tx.create(std.testing.allocator, prepared);
    defer sender.destroy();
    const stats = sender.snapshot();
    try std.testing.expectEqual(@as(u8, 2), stats.flow_count);
    try std.testing.expect(stats.flows[0].multicast and stats.flows[1].multicast);
    try std.testing.expectEqual(@as(u8, 32), stats.flows[0].ttl);
    try std.testing.expectEqual(@as(u8, 32), stats.flows[1].ttl);
    // Clearing only one flow's resolved egress still fails the whole plan.
    var missing = prepared;
    missing.flows[1].source_ip = null;
    try std.testing.expectError(error.Aes67MulticastRequiresSourceInterface, tx.Tx.create(std.testing.allocator, missing));
}
