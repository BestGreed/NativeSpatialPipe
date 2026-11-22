const std = @import("std");
const rtp = @import("aes67_rtp.zig");

test "RTP header uses version 2, dynamic payload type and big-endian fields" {
    var header: [rtp.header_bytes]u8 = undefined;
    rtp.writeHeader(&header, 0x1234, 0x89ABCDEF, 0x4E535036, 96);
    try std.testing.expectEqualSlices(u8, &.{ 0x80, 0x60, 0x12, 0x34, 0x89, 0xAB, 0xCD, 0xEF, 0x4E, 0x53, 0x50, 0x36 }, &header);
    rtp.writeHeader(&header, 0, 0, 0, 97);
    try std.testing.expectEqualSlices(u8, &.{ 0x80, 0x61, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 }, &header);
}

test "L24 encoding clamps, rounds and never emits NaN" {
    const cases = [_]struct { sample: f32, expected: [3]u8 }{
        .{ .sample = 0.0, .expected = .{ 0x00, 0x00, 0x00 } },
        .{ .sample = 0.5, .expected = .{ 0x40, 0x00, 0x00 } },
        .{ .sample = -0.5, .expected = .{ 0xC0, 0x00, 0x00 } },
        .{ .sample = 1.0, .expected = .{ 0x7F, 0xFF, 0xFF } },
        .{ .sample = -1.0, .expected = .{ 0x80, 0x00, 0x00 } },
        .{ .sample = 2.0, .expected = .{ 0x7F, 0xFF, 0xFF } },
        .{ .sample = -2.0, .expected = .{ 0x80, 0x00, 0x00 } },
        .{ .sample = std.math.nan(f32), .expected = .{ 0x00, 0x00, 0x00 } },
        .{ .sample = 1.0 / 8388608.0, .expected = .{ 0x00, 0x00, 0x01 } },
        .{ .sample = -1.0 / 8388608.0, .expected = .{ 0xFF, 0xFF, 0xFF } },
    };
    for (cases) |case| {
        var encoded: [3]u8 = undefined;
        rtp.encodeL24(case.sample, &encoded);
        try std.testing.expectEqualSlices(u8, &case.expected, &encoded);
    }
}

test "packet builder writes the exact RTP/L24 layout" {
    var pcm = [_]f32{0} ** (48 * 2);
    // Frame 0 L/R, frame 1 L/R, ... with distinct channel samples.
    for (0..48) |frame| {
        pcm[frame * 2] = @as(f32, @floatFromInt(frame)) / 96.0;
        pcm[frame * 2 + 1] = -@as(f32, @floatFromInt(frame)) / 96.0;
    }
    var packet: [rtp.header_bytes + 48 * 2 * 3]u8 = undefined;
    const size = try rtp.buildPacket(&packet, &pcm, 48, 2, 7, 480, 0x4E535036, 96);
    try std.testing.expectEqual(@as(usize, 12 + 288), size);
    try std.testing.expectEqual(@as(u8, 0x80), packet[0]);
    try std.testing.expectEqual(@as(u8, 96), packet[1]);
    try std.testing.expectEqual(@as(u8, 0), packet[2]);
    try std.testing.expectEqual(@as(u8, 7), packet[3]);
    // RTP timestamp 480 = 00 00 01 E0.
    try std.testing.expectEqualSlices(u8, &.{ 0x00, 0x00, 0x01, 0xE0 }, packet[4..8]);
    try std.testing.expectEqualSlices(u8, &.{ 0x4E, 0x53, 0x50, 0x36 }, packet[8..12]);
    // Sample 0 of frame 0: 0.0 -> 00 00 00; sample 1: 0.0 -> 00 00 00.
    try std.testing.expectEqualSlices(u8, &.{ 0, 0, 0, 0, 0, 0 }, packet[12..18]);
    // Frame 1 L = 2/96 -> L24 0x000AAA? (2/96*8388608 = 174762.67 -> 174763 = 0x02AAAB).
    var expected: [3]u8 = undefined;
    rtp.encodeL24(1.0 / 96.0, &expected);
    try std.testing.expectEqualSlices(u8, &expected, packet[12 + 48 * 2 * 3 - 288 + 6 ..][0..3]);
    // Last sample is frame 47 R = -47/96.
    rtp.encodeL24(-47.0 / 96.0, &expected);
    try std.testing.expectEqualSlices(u8, &expected, packet[size - 3 ..][0..3]);
}

test "silent packet has a valid header and all-zero payload" {
    var packet: [rtp.header_bytes + 48 * 12 * 3]u8 = undefined;
    const size = try rtp.buildSilentPacket(&packet, 48, 12, 0xFFFF, 0xFFFFFFD0, 0x01020304, 96);
    try std.testing.expectEqual(@as(usize, 12 + 1728), size);
    try std.testing.expectEqualSlices(u8, &.{ 0x80, 0x60, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xD0, 0x01, 0x02, 0x03, 0x04 }, packet[0..12]);
    for (packet[12..]) |byte| try std.testing.expectEqual(@as(u8, 0), byte);
}

test "timeline advances the sequence by one and timestamp by frames with wraparound" {
    var timeline = rtp.Timeline{};
    try std.testing.expectEqual(@as(u16, 0), timeline.seq);
    try std.testing.expectEqual(@as(u32, 0), timeline.timestamp);
    timeline.advance(48);
    try std.testing.expectEqual(@as(u16, 1), timeline.seq);
    try std.testing.expectEqual(@as(u32, 48), timeline.timestamp);
    timeline.seq = 0xFFFF;
    timeline.timestamp = 0xFFFFFFD0;
    timeline.advance(48);
    try std.testing.expectEqual(@as(u16, 0), timeline.seq);
    try std.testing.expectEqual(@as(u32, 0), timeline.timestamp);
    try std.testing.expectEqual(@as(u16, 1), rtp.ptimeMs(48));
}

test "packet size table matches RTP plus interleaved L24 for every channel count" {
    for (1..17) |channels| {
        const count: u16 = @intCast(channels);
        try std.testing.expectEqual(@as(usize, 12) + 48 * channels * 3, rtp.packetBytes(48, count));
    }
}

test "SDP declares a unicast L24/1ms sendonly stream with PTP clock" {
    const labels = [_][]const u8{ "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs", "Ltf", "Rtf", "Ltr", "Rtr" };
    var text: [rtp.sdp_max_bytes]u8 = undefined;
    const size = try rtp.writeSdp(&text, .{
        .dest_ip = "192.168.50.20",
        .port = 5004,
        .channels = 12,
        .frames_per_packet = 48,
        .payload_type = 96,
        .ssrc = 0x4E535036,
        .ptp_gmid = "00-11-22-FF-FE-33-44-55",
        .ptp_domain = 4,
        .layout_id = "7.1.4",
        .channel_labels = &labels,
    });
    const expected =
        "v=0\r\n" ++
        "o=- 1314082870 1314082870 IN IP4 0.0.0.0\r\n" ++
        "s=NativeSpatialPipe AES67 output\r\n" ++
        "c=IN IP4 192.168.50.20\r\n" ++
        "t=0 0\r\n" ++
        "m=audio 5004 RTP/AVP 96\r\n" ++
        "a=rtpmap:96 L24/48000/12\r\n" ++
        "a=ptime:1\r\n" ++
        "a=sendonly\r\n" ++
        "a=ts-refclk:ptp=IEEE1588-2008:00-11-22-FF-FE-33-44-55:4\r\n" ++
        "a=mediaclk:direct=0\r\n" ++
        "a=fmtp:96 channel-order=SMPTE2110.(71,4)\r\n" ++
        "a=comment:layout=7.1.4\r\n" ++
        "a=comment:channel-labels=L,R,C,LFE,Ls,Rs,Lrs,Rrs,Ltf,Rtf,Ltr,Rtr\r\n";
    try std.testing.expectEqualStrings(expected, text[0..size]);
}

test "SDP localmac fallback and 2.0 layout omit normative-looking extras" {
    var text: [rtp.sdp_max_bytes]u8 = undefined;
    const size = try rtp.writeSdp(&text, .{
        .dest_ip = "127.0.0.1",
        .channels = 2,
        .ssrc = 1,
        .layout_id = "2.0",
        .channel_labels = &.{ "L", "R" },
    });
    const sdp = text[0..size];
    try std.testing.expect(std.mem.indexOf(u8, sdp, "a=ts-refclk:localmac=02-4E-53-50-00-01\r\n") != null);
    try std.testing.expect(std.mem.indexOf(u8, sdp, "a=fmtp:") == null);
    try std.testing.expect(std.mem.indexOf(u8, sdp, "a=rtpmap:96 L24/48000/2\r\n") != null);
    try std.testing.expect(std.mem.indexOf(u8, sdp, "c=IN IP4 127.0.0.1\r\n") != null);
}

test "SDP rejects inconsistent channel and label counts" {
    var text: [rtp.sdp_max_bytes]u8 = undefined;
    try std.testing.expectError(error.InvalidAes67Sdp, rtp.writeSdp(&text, .{
        .dest_ip = "192.168.50.20",
        .channels = 2,
        .ssrc = 1,
        .channel_labels = &.{ "L", "R", "C" },
    }));
    try std.testing.expectError(error.InvalidAes67Sdp, rtp.writeSdp(&text, .{
        .dest_ip = "192.168.50.20",
        .channels = 17,
        .ssrc = 1,
    }));
    var tiny: [32]u8 = undefined;
    try std.testing.expectError(error.SdpBufferTooSmall, rtp.writeSdp(&tiny, .{
        .dest_ip = "192.168.50.20",
        .channels = 12,
        .ssrc = 1,
    }));
}

test "public layout ids map to informative SMPTE channel-order tokens" {
    try std.testing.expectEqualStrings("SMPTE2110.(51,2)", rtp.smpteChannelOrder("5.1.2").?);
    try std.testing.expectEqualStrings("SMPTE2110.(71,4)", rtp.smpteChannelOrder("7.1.4").?);
    try std.testing.expectEqualStrings("SMPTE2110.(71,6)", rtp.smpteChannelOrder("7.1.6").?);
    try std.testing.expectEqualStrings("SMPTE2110.(91,6)", rtp.smpteChannelOrder("9.1.6").?);
    try std.testing.expect(rtp.smpteChannelOrder("2.0") == null);
    try std.testing.expect(rtp.smpteChannelOrder("4.1.2") == null);
}
