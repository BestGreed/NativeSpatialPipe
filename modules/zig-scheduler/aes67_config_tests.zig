const std = @import("std");
const cfg = @import("aes67_config.zig");
const playback = @import("desktop_playback.zig");

fn file() cfg.File {
    return .{ .destIp = "192.168.50.20" };
}

test "transport defaults resolve fixed AES67 L24/48k/1ms settings" {
    const prepared = try cfg.prepare(file(), 12);
    try std.testing.expectEqual([4]u8{ 192, 168, 50, 20 }, prepared.dest_ip);
    try std.testing.expectEqual(@as(u16, 5004), prepared.port);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 96), prepared.payload_type);
    try std.testing.expectEqual(cfg.default_ssrc, prepared.ssrc);
    try std.testing.expect(prepared.src_interface_ip == null);
    try std.testing.expect(prepared.ptp_gmid == null);
    try std.testing.expectEqual(@as(u16, 0), prepared.ptp_domain);
    try std.testing.expectEqual(@as(u16, 64), prepared.ring_packets);
    try std.testing.expectEqual(@as(u32, 480), prepared.prebuffer_frames);
    try std.testing.expectEqual(@as(u16, 12), prepared.channels);
}

test "configured address, PTP and sender parameters are validated and normalized" {
    var value = file();
    value.port = 6000;
    value.payloadType = 97;
    value.ssrc = 0x11223344;
    value.srcInterfaceIp = "192.168.50.1";
    value.ptpGmid = "00-11-22-ff-fe-33-44-55";
    value.ptpDomain = 3;
    value.ringPackets = 128;
    value.prebufferFrames = 960;
    const prepared = try cfg.prepare(value, 8);
    try std.testing.expectEqual(@as(?[4]u8, .{ 192, 168, 50, 1 }), prepared.src_interface_ip);
    try std.testing.expectEqualSlices(u8, "00-11-22-FF-FE-33-44-55", &prepared.ptp_gmid.?);
    try std.testing.expectEqual(@as(u16, 3), prepared.ptp_domain);
    try std.testing.expectEqual(@as(u32, 0x11223344), prepared.ssrc);
    try std.testing.expectEqual(@as(u16, 128), prepared.ring_packets);
    try std.testing.expectEqual(@as(u32, 960), prepared.prebuffer_frames);
    try std.testing.expectEqual(@as(u16, 8), prepared.channels);
}

test "invalid transport settings are rejected before any socket exists" {
    try std.testing.expectError(error.Aes67ChannelCount, cfg.prepare(file(), 0));
    try std.testing.expectError(error.Aes67ChannelCount, cfg.prepare(file(), 17));
    var value = file();
    value.port = 0;
    try std.testing.expectError(error.InvalidAes67Port, cfg.prepare(value, 12));
    value = file();
    value.framesPerPacket = 96;
    try std.testing.expectError(error.UnsupportedAes67PacketFrames, cfg.prepare(value, 12));
    value = file();
    value.payloadType = 95;
    try std.testing.expectError(error.InvalidAes67PayloadType, cfg.prepare(value, 12));
    value = file();
    value.payloadType = 128;
    try std.testing.expectError(error.InvalidAes67PayloadType, cfg.prepare(value, 12));
    value = file();
    value.ptpDomain = 128;
    try std.testing.expectError(error.InvalidAes67PtpDomain, cfg.prepare(value, 12));
    value = file();
    value.ringPackets = 7;
    try std.testing.expectError(error.InvalidAes67RingPackets, cfg.prepare(value, 12));
    value = file();
    value.ringPackets = 1025;
    try std.testing.expectError(error.InvalidAes67RingPackets, cfg.prepare(value, 12));
    value = file();
    value.prebufferFrames = 47;
    try std.testing.expectError(error.InvalidAes67Prebuffer, cfg.prepare(value, 12));
    value = file();
    value.ringPackets = 8;
    value.prebufferFrames = 8 * 48 + 1;
    try std.testing.expectError(error.InvalidAes67Prebuffer, cfg.prepare(value, 12));
}

test "destination must be an explicit unicast IPv4 address" {
    var value = file();
    value.destIp = "";
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 12));
    value.destIp = "0.0.0.0";
    try std.testing.expectError(error.Aes67DestinationMustBeUnicast, cfg.prepare(value, 12));
    value.destIp = "255.255.255.255";
    try std.testing.expectError(error.Aes67DestinationMustBeUnicast, cfg.prepare(value, 12));
    value.destIp = "239.1.1.1";
    try std.testing.expectError(error.Aes67DestinationMustBeUnicast, cfg.prepare(value, 12));
    value.destIp = "127.0.0.1";
    _ = try cfg.prepare(value, 12); // Loopback is a valid unicast lab destination.
    value.destIp = "192.168.1.256";
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 12));
    value.destIp = "192.168.1";
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 12));
    value.destIp = "receiver.local";
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 12));
}

test "source interface and PTP grandmaster identity fail closed" {
    var value = file();
    value.srcInterfaceIp = "0.0.0.0";
    try std.testing.expectError(error.InvalidAes67SourceInterface, cfg.prepare(value, 12));
    value.srcInterfaceIp = "239.1.1.1";
    try std.testing.expectError(error.InvalidAes67SourceInterface, cfg.prepare(value, 12));
    value.srcInterfaceIp = "10.0.0";
    try std.testing.expectError(error.InvalidAes67SourceInterface, cfg.prepare(value, 12));
    value.srcInterfaceIp = "";
    value.ptpGmid = "00-11-22-FF-FE-33-44";
    try std.testing.expectError(error.InvalidAes67PtpGmid, cfg.prepare(value, 12));
    value.ptpGmid = "00-11-22-FF-FE-33-44-ZZ";
    try std.testing.expectError(error.InvalidAes67PtpGmid, cfg.prepare(value, 12));
}

test "FSA playback profile selects AES67 without ASIO device fields" {
    var value = playback.Config{
        .bridgeDll = "C:\\fixture\\bridge.dll",
        .outputBackend = "aes67",
        .outputLayout = "7.1.4",
        .aes67 = .{ .destIp = "192.168.50.20", .srcInterfaceIp = "192.168.50.1" },
    };
    try value.validate();
    try std.testing.expect(value.asioDll.len == 0 and value.driver.len == 0);
    try std.testing.expectEqual(@as(usize, 12), value.channelCount());
    try std.testing.expectEqual(@as(u32, 714), (try value.preset()).code);
    const labels = try value.labels();
    try std.testing.expectEqualStrings("L", labels[0]);
    try std.testing.expectEqualStrings("Rtr", labels[11]);
    const sources = try value.sources();
    try std.testing.expectEqual(@as(u32, 0), sources[0]);
    try std.testing.expectEqual(@as(u32, 11), sources[11]);
    value.sourcePolicy = "active";
    try std.testing.expectError(error.Aes67RequiresFixedSource, value.validate());
    value.sourcePolicy = "fixed";
    value.outputLayout = "9.1.6";
    try value.validate();
    try std.testing.expectEqual(@as(usize, 16), value.channelCount());
}

test "AES67 backend requires transport config, 48 kHz and valid values" {
    var value = playback.Config{ .bridgeDll = "C:\\fixture\\bridge.dll", .outputBackend = "aes67" };
    try std.testing.expectError(error.Aes67ConfigRequired, value.validate());
    value.aes67 = .{ .destIp = "192.168.50.20" };
    try value.validate();
    value.sampleRate = 96000;
    try std.testing.expectError(error.Aes67Requires48000, value.validate());
    value.sampleRate = 48000;
    value.aes67.?.framesPerPacket = 96;
    try std.testing.expectError(error.UnsupportedAes67PacketFrames, value.validate());
    value.aes67.?.framesPerPacket = 48;
    value.aes67.?.destIp = "239.1.1.1";
    try std.testing.expectError(error.Aes67DestinationMustBeUnicast, value.validate());
    value.aes67.?.destIp = "192.168.50.20";
    value.aes67.?.ringPackets = 4;
    try std.testing.expectError(error.InvalidAes67RingPackets, value.validate());
}

test "inactive AES67 block is still validated so switching cannot hit bad settings" {
    var value = playback.Config{
        .bridgeDll = "C:\\fixture\\bridge.dll",
        .asioDll = "C:\\fixture\\asio.dll",
        .driver = "asio:test",
        .aes67 = .{ .destIp = "192.168.50.20" },
    };
    try value.validate();
    value.aes67.?.destIp = "not-an-address";
    try std.testing.expectError(error.InvalidAes67Destination, value.validate());
    value.aes67.?.destIp = "192.168.50.20";
    value.aes67.?.payloadType = 90;
    try std.testing.expectError(error.InvalidAes67PayloadType, value.validate());
}

test "strict JSON parsing rejects unknown AES67 fields and missing destination" {
    const allocator = std.testing.allocator;
    try std.testing.expectError(error.UnknownField, playback.parse(allocator,
        \\{"bridgeDll":"C:\\\\fixture\\\\bridge.dll","outputBackend":"aes67","aes67":{"destIp":"127.0.0.1","typo":1}}
    ));
    try std.testing.expectError(error.MissingField, playback.parse(allocator,
        \\{"bridgeDll":"C:\\\\fixture\\\\bridge.dll","outputBackend":"aes67","aes67":{"port":5004}}
    ));
}

test "playback JSON with a full AES67 block validates and keeps the transport defaults" {
    const allocator = std.testing.allocator;
    const text =
        \\{"bridgeDll":"C:\\\\fixture\\\\bridge.dll","outputBackend":"aes67","outputLayout":"7.1.4",
        \\"aes67":{"destIp":"127.0.0.1","port":5004,"framesPerPacket":48,"payloadType":96,
        \\"srcInterfaceIp":"127.0.0.1","ptpGmid":"00-11-22-FF-FE-33-44-55","ptpDomain":0,
        \\"ringPackets":64,"prebufferFrames":480}}
    ;
    var parsed = try playback.parse(allocator, text);
    defer parsed.deinit();
    const prepared = try cfg.prepare(parsed.value.aes67.?, parsed.value.channelCount());
    try std.testing.expectEqual(cfg.default_ssrc, prepared.ssrc);
    try std.testing.expectEqual([4]u8{ 127, 0, 0, 1 }, prepared.dest_ip);
}

test "routing source order is preserved for AES67 channel labels" {
    const allocator = std.testing.allocator;
    const text =
        \\{"bridgeDll":"C:\\\\fixture\\\\bridge.dll","outputBackend":"aes67","outputLayout":"5.1.2",
        \\"channelOrder":"custom","routing":{"sourceChannelIndices":[0,1,4,5,2,3,6,7]},
        \\"aes67":{"destIp":"192.168.50.20"}}
    ;
    var parsed = try playback.parse(allocator, text);
    defer parsed.deinit();
    const labels = try parsed.value.labels();
    try std.testing.expectEqualStrings("L", labels[0]);
    try std.testing.expectEqualStrings("Ls", labels[2]);
    try std.testing.expectEqualStrings("C", labels[4]);
    const sources = try parsed.value.sources();
    try std.testing.expectEqual(@as(u32, 4), sources[2]);
}
