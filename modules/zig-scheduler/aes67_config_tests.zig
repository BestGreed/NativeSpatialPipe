const std = @import("std");
const cfg = @import("aes67_config.zig");
const playback = @import("desktop_playback.zig");

/// 12ch reference config: two explicit flows (8+4) on one unicast host with
/// unique ports - the flows-only shape every layout must use now that the
/// single-flow mirror format is gone.
fn file() cfg.File {
    return .{ .flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 8 },
    } };
}

test "transport defaults resolve fixed AES67 L24/48k/1ms settings" {
    const prepared = try cfg.prepare(file(), 12);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 96), prepared.payload_type);
    try std.testing.expectEqual(cfg.default_ssrc, prepared.ssrc);
    try std.testing.expect(prepared.src_interface_ip == null);
    try std.testing.expect(prepared.ptp_gmid == null);
    try std.testing.expectEqual(@as(u16, 0), prepared.ptp_domain);
    try std.testing.expectEqual(@as(u16, 64), prepared.ring_packets);
    try std.testing.expectEqual(@as(u32, 480), prepared.prebuffer_frames);
    try std.testing.expectEqual(@as(u16, 12), prepared.channels);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 8), prepared.flows[0].channels);
    try std.testing.expectEqual(@as(u16, 0), prepared.flows[0].channel_offset);
    try std.testing.expectEqual(@as(u16, 5006), prepared.flows[1].port);
    try std.testing.expectEqual(@as(u16, 4), prepared.flows[1].channels);
    try std.testing.expectEqual(@as(u16, 8), prepared.flows[1].channel_offset);
    // Per-flow SSRCs stay unique by construction (base + flow index).
    try std.testing.expectEqual(cfg.default_ssrc, prepared.flows[0].ssrc);
    try std.testing.expectEqual(cfg.default_ssrc + 1, prepared.flows[1].ssrc);
}

test "configured address, PTP and sender parameters are validated and normalized" {
    var value = cfg.File{ .flows = &.{.{ .destIp = "192.0.2.20", .port = 6000, .channels = 8 }} };
    value.payloadType = 97;
    value.ssrc = 0x11223344;
    value.srcInterfaceIp = "192.0.2.1";
    value.ptpDomain = 3;
    value.ringPackets = 128;
    value.prebufferFrames = 960;
    const prepared = try cfg.prepare(value, 8);
    try std.testing.expectEqual(@as(?[4]u8, .{ 192, 0, 2, 1 }), prepared.src_interface_ip);
    try std.testing.expectEqual(@as(u16, 3), prepared.ptp_domain);
    try std.testing.expectEqual(@as(u32, 0x11223344), prepared.ssrc);
    try std.testing.expectEqual(@as(u32, 0x11223344), prepared.flows[0].ssrc);
    try std.testing.expectEqual(@as(u16, 128), prepared.ring_packets);
    try std.testing.expectEqual(@as(u32, 960), prepared.prebuffer_frames);
    try std.testing.expectEqual(@as(u16, 8), prepared.channels);
    try std.testing.expectEqual(@as(u16, 6000), prepared.port);
    try std.testing.expectEqual([4]u8{ 192, 0, 2, 20 }, prepared.dest_ip);
}

test "invalid transport settings are rejected before any socket exists" {
    try std.testing.expectError(error.Aes67ChannelCount, cfg.prepare(file(), 0));
    try std.testing.expectError(error.Aes67ChannelCount, cfg.prepare(file(), 17));
    var value = file();
    value.framesPerPacket = 96;
    try std.testing.expectError(error.UnsupportedAes67PacketFrames, cfg.prepare(value, 12));
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
    value = file();
    value.ttl = 0;
    try std.testing.expectError(error.InvalidAes67Ttl, cfg.prepare(value, 12));
    value = file();
    value.payloadType = 95;
    try std.testing.expectError(error.InvalidAes67PayloadType, cfg.prepare(value, 12));
    value.payloadType = 128;
    try std.testing.expectError(error.InvalidAes67PayloadType, cfg.prepare(value, 12));
    // Flow-level: zero port, empty flow list and out-of-range flow width.
    value = file();
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .port = 0, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 8 },
    };
    try std.testing.expectError(error.InvalidAes67Port, cfg.prepare(value, 12));
    value.flows = &.{};
    try std.testing.expectError(error.InvalidAes67FlowCount, cfg.prepare(value, 12));
    value.flows = &.{.{ .destIp = "192.0.2.20", .channels = 12 }};
    try std.testing.expectError(error.InvalidAes67FlowChannels, cfg.prepare(value, 12));
}

test "destination accepts explicit unicast or multicast IPv4 with multicast safety" {
    var value = cfg.File{ .flows = &.{.{ .destIp = "", .channels = 8 }} };
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
    value.flows = &.{.{ .destIp = "0.0.0.0", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
    value.flows = &.{.{ .destIp = "255.255.255.255", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
    // 240/4 is reserved, not unicast and not class-D multicast.
    value.flows = &.{.{ .destIp = "240.0.0.1", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
    // Multicast egress is explicit (multi-NIC desktop): srcInterfaceIp required.
    value.flows = &.{.{ .destIp = "239.1.0.4", .channels = 8 }};
    try std.testing.expectError(error.Aes67MulticastRequiresSourceInterface, cfg.prepare(value, 8));
    value.srcInterfaceIp = "192.0.2.1";
    const prepared = try cfg.prepare(value, 8);
    try std.testing.expect(prepared.multicast);
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 4 }, prepared.dest_ip);
    try std.testing.expectEqual(cfg.default_ttl, prepared.flows[0].ttl);
    value.ttl = 8;
    const ttl8 = try cfg.prepare(value, 8);
    try std.testing.expectEqual(@as(u8, 8), ttl8.flows[0].ttl);
    // Unicast stays valid; loopback is a lab destination.
    value = cfg.File{ .flows = &.{.{ .destIp = "127.0.0.1", .channels = 8 }} };
    const local = try cfg.prepare(value, 8);
    try std.testing.expect(!local.multicast);
    try std.testing.expect(!local.flows[0].multicast);
    value.flows = &.{.{ .destIp = "192.0.2.256", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
    value.flows = &.{.{ .destIp = "192.0.2", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
    value.flows = &.{.{ .destIp = "receiver.invalid", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, cfg.prepare(value, 8));
}

test "flows are mandatory: the legacy mirror format is rejected loudly" {
    // No flows at all - rejected for every layout and channel count.
    var value = cfg.File{};
    try std.testing.expectError(error.Aes67RequiresFlows, cfg.prepare(value, 2));
    try std.testing.expectError(error.Aes67RequiresFlows, cfg.prepare(value, 7));
    try std.testing.expectError(error.Aes67RequiresFlows, cfg.prepare(value, 12));
    try std.testing.expectError(error.Aes67RequiresFlows, cfg.prepare(value, 16));
    // The removed top-level destIp/port pair fails with the dedicated legacy
    // error even when flows are present.
    value.destIp = "239.1.0.1";
    try std.testing.expectError(error.Aes67LegacyMirrorFormat, cfg.prepare(value, 16));
    value.destIp = null;
    value.port = 5010;
    try std.testing.expectError(error.Aes67LegacyMirrorFormat, cfg.prepare(value, 16));
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5010, .channels = 8, .channelOffset = 0, .ttl = 32 },
        .{ .destIp = "239.1.0.2", .port = 5012, .channels = 8, .channelOffset = 8, .ttl = 32 },
    };
    value.srcInterfaceIp = "192.0.2.63";
    try std.testing.expectError(error.Aes67LegacyMirrorFormat, cfg.prepare(value, 16));
    value.destIp = null;
    value.port = null;
    // With both legacy fields removed the very same flow rows prepare fine.
    const prepared = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    // The document-level validation used by the WebUI save path agrees.
    var doc = playback.Config{
        .bridgeDll = "C:/fixture/bridge.dll",
        .outputBackend = "aes67",
        .outputLayout = "9.1.6",
        .aes67 = .{ .destIp = "239.1.0.1", .port = 5010, .srcInterfaceIp = "192.0.2.63", .ttl = 32, .flows = value.flows },
    };
    try std.testing.expectError(error.Aes67LegacyMirrorFormat, doc.validate());
    doc.aes67.?.destIp = null;
    doc.aes67.?.port = null;
    try doc.validate();
}

test "multicast multi-flow keeps per-flow group/port/ttl" {
    var value = file();
    value.srcInterfaceIp = "192.0.2.94";
    value.ttl = 32;
    value.flows = &.{
        .{ .destIp = "239.1.0.10", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.11", .port = 5012, .channels = 4, .channelOffset = 8, .ttl = 8 },
    };
    const prepared = try cfg.prepare(value, 12);
    try std.testing.expect(prepared.multicast);
    try std.testing.expect(prepared.flows[0].multicast and prepared.flows[1].multicast);
    try std.testing.expectEqual(@as(u8, 32), prepared.flows[0].ttl);
    try std.testing.expectEqual(@as(u8, 8), prepared.flows[1].ttl);
    value.flows = &.{
        .{ .destIp = "239.1.0.10", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.11", .port = 5012, .channels = 4, .channelOffset = 8 },
    };
    value.srcInterfaceIp = "";
    try std.testing.expectError(error.Aes67MulticastRequiresSourceInterface, cfg.prepare(value, 12));
}

test "source interface fails closed and a claimed PTP lock without one is rejected" {
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
    // A well-formed GMID still fails in Phase 1: the sender has no PTP lock
    // source, so the SDP must not claim a=ts-refclk:ptp (it advertises
    // a=ts-refclk:local + a=mediaclk:sender instead).
    value.ptpGmid = "00-11-22-FF-FE-33-44-55";
    try std.testing.expectError(error.Aes67PtpGmidRequiresLock, cfg.prepare(value, 12));
    value.ptpGmid = "";
    const clean = try cfg.prepare(value, 12);
    try std.testing.expect(clean.ptp_gmid == null);
}

test "FSA playback profile selects AES67 without ASIO device fields" {
    var value = playback.Config{
        .bridgeDll = "C:/fixture/bridge.dll",
        .outputBackend = "aes67",
        .outputLayout = "7.1.4",
        .aes67 = .{ .flows = &.{
            .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
            .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 8 },
        } },
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
    try value.validate();
    value.sourcePolicy = "unified";
    try value.validate();
    value.sourcePolicy = "fixed";
    value.outputLayout = "9.1.6";
    // 16ch splits into 8+8; a single 16ch flow exceeds the per-flow budget.
    value.aes67.?.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 8, .channelOffset = 8 },
    };
    try value.validate();
    try std.testing.expectEqual(@as(usize, 16), value.channelCount());
    value.aes67.?.flows = &.{.{ .destIp = "192.0.2.20", .channels = 16 }};
    try std.testing.expectError(error.InvalidAes67FlowChannels, value.validate());
}

test "AES67 backend requires transport config, 48 kHz and valid values" {
    var value = playback.Config{ .bridgeDll = "C:/fixture/bridge.dll", .outputBackend = "aes67", .outputLayout = "5.1.2" };
    try std.testing.expectError(error.Aes67ConfigRequired, value.validate());
    value.aes67 = .{};
    try std.testing.expectError(error.Aes67RequiresFlows, value.validate());
    value.aes67.?.flows = &.{.{ .destIp = "192.0.2.20", .channels = 8 }};
    try value.validate();
    value.sampleRate = 96000;
    try std.testing.expectError(error.Aes67Requires48000, value.validate());
    value.sampleRate = 48000;
    value.aes67.?.framesPerPacket = 96;
    try std.testing.expectError(error.UnsupportedAes67PacketFrames, value.validate());
    value.aes67.?.framesPerPacket = 48;
    value.aes67.?.flows = &.{.{ .destIp = "239.1.0.4", .channels = 8 }};
    try std.testing.expectError(error.Aes67MulticastRequiresSourceInterface, value.validate());
    value.aes67.?.srcInterfaceIp = "192.0.2.1";
    try value.validate(); // Multicast destination is accepted with explicit egress.
    value.aes67.?.ringPackets = 4;
    try std.testing.expectError(error.InvalidAes67RingPackets, value.validate());
}

test "inactive AES67 block is still validated so switching cannot hit bad settings" {
    var value = playback.Config{
        .bridgeDll = "C:/fixture/bridge.dll",
        .asioDll = "C:/fixture/asio.dll",
        .driver = "asio:test",
        .outputLayout = "5.1.2",
        .aes67 = .{ .flows = &.{.{ .destIp = "192.0.2.20", .channels = 8 }} },
    };
    try value.validate();
    value.aes67.?.flows = &.{.{ .destIp = "not-an-address", .channels = 8 }};
    try std.testing.expectError(error.InvalidAes67Destination, value.validate());
    value.aes67.?.flows = &.{.{ .destIp = "192.0.2.20", .channels = 8, .payloadType = 90 }};
    try std.testing.expectError(error.InvalidAes67PayloadType, value.validate());
}

test "strict JSON parsing rejects unknown AES67 fields and missing flows" {
    const allocator = std.testing.allocator;
    try std.testing.expectError(error.UnknownField, playback.parse(allocator, "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"aes67\":{\"flows\":[{\"destIp\":\"127.0.0.1\",\"channels\":2}],\"typo\":1}}"));
    // An aes67 object without flows fails the document validation that
    // playback.parse runs inline, with the clear mandatory-flows error.
    try std.testing.expectError(error.Aes67RequiresFlows, playback.parse(allocator, "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"outputLayout\":\"5.1.2\",\"aes67\":{}}"));
}

test "playback parses and validates PTP settings without changing flow settings" {
    const allocator = std.testing.allocator;
    const prefix = "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"outputLayout\":\"5.1.2\",\"aes67\":{\"flows\":[{\"destIp\":\"192.0.2.20\",\"channels\":8}],\"ptp\":";
    var defaults = try playback.parse(allocator, prefix ++ "{}}}");
    defer defaults.deinit();
    try std.testing.expectEqual(cfg.Ptp{}, defaults.value.aes67.?.ptp.?);
    var custom = try playback.parse(allocator, prefix ++ "{\"domain\":127,\"lockTimeoutMs\":2500,\"timestamping\":\"hardware\"}}}");
    defer custom.deinit();
    try std.testing.expectEqual(cfg.Ptp{ .domain = 127, .lockTimeoutMs = 2500, .timestamping = .hardware }, custom.value.aes67.?.ptp.?);
    try std.testing.expectError(error.InvalidAes67PtpDomain, playback.parse(allocator, prefix ++ "{\"domain\":128}}}"));
    try std.testing.expectError(error.InvalidAes67PtpLockTimeout, playback.parse(allocator, prefix ++ "{\"lockTimeoutMs\":0}}}"));
    try std.testing.expectError(error.InvalidEnumTag, playback.parse(allocator, prefix ++ "{\"timestamping\":\"other\"}}}"));
}

test "playback JSON with a full AES67 block validates and keeps the transport defaults" {
    const allocator = std.testing.allocator;
    const text = "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"outputLayout\":\"7.1.4\",\"aes67\":{\"framesPerPacket\":48,\"payloadType\":96,\"srcInterfaceIp\":\"127.0.0.1\",\"ptpDomain\":0,\"ringPackets\":64,\"prebufferFrames\":480,\"ttl\":32,\"flows\":[{\"destIp\":\"127.0.0.1\",\"port\":5004,\"channels\":8,\"channelOffset\":0},{\"destIp\":\"127.0.0.1\",\"port\":5006,\"channels\":4,\"channelOffset\":8}]}}";
    var parsed = try playback.parse(allocator, text);
    defer parsed.deinit();
    const prepared = try cfg.prepare(parsed.value.aes67.?, parsed.value.channelCount());
    try std.testing.expectEqual(cfg.default_ssrc, prepared.ssrc);
    try std.testing.expectEqual([4]u8{ 127, 0, 0, 1 }, prepared.dest_ip);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5006), prepared.flows[1].port);
}

test "routing source order is preserved for AES67 channel labels" {
    const allocator = std.testing.allocator;
    const text = "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"outputLayout\":\"5.1.2\",\"channelOrder\":\"custom\",\"routing\":{\"sourceChannelIndices\":[0,1,4,5,2,3,6,7]},\"aes67\":{\"flows\":[{\"destIp\":\"192.0.2.20\",\"channels\":8}]}}";
    var parsed = try playback.parse(allocator, text);
    defer parsed.deinit();
    const labels = try parsed.value.labels();
    try std.testing.expectEqualStrings("L", labels[0]);
    try std.testing.expectEqualStrings("Ls", labels[2]);
    try std.testing.expectEqualStrings("C", labels[4]);
    const sources = try parsed.value.sources();
    try std.testing.expectEqual(@as(u32, 4), sources[2]);
}

test "flow widths follow the ceil(channels/8) split for every layout" {
    // The split rule is layout-agnostic: any channel count N produces
    // ceil(N/8) flows of at most 8 channels; 16ch is just one instance.
    var value = file();
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 8 },
    };
    const twelve = try cfg.prepare(value, 12);
    try std.testing.expectEqual(@as(u8, 2), twelve.flow_count);
    try std.testing.expectEqual(@as(u16, 4), twelve.flows[1].channels);
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 6, .channelOffset = 8 },
    };
    const fourteen = try cfg.prepare(value, 14);
    try std.testing.expectEqual(@as(u8, 2), fourteen.flow_count);
    try std.testing.expectEqual(@as(u16, 6), fourteen.flows[1].channels);
    value.flows = &.{.{ .destIp = "192.0.2.20", .channels = 8 }};
    const eight = try cfg.prepare(value, 8);
    try std.testing.expectEqual(@as(u8, 1), eight.flow_count);
    // Offsets must stay contiguous and sum to the layout channels.
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 4 },
    };
    try std.testing.expectError(error.InvalidAes67FlowOffset, cfg.prepare(value, 12));
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 6, .channelOffset = 8 },
    };
    try std.testing.expectError(error.InvalidAes67FlowChannels, cfg.prepare(value, 12));
}

test "multi-flow split rejects duplicate destinations, groups and SSRCs" {
    var value = file();
    // Identical (destination, port) pair.
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5010, .channels = 4, .channelOffset = 8 },
    };
    try std.testing.expectError(error.Aes67DuplicateFlowDestination, cfg.prepare(value, 12));
    // Two hand-filled flows sharing one SSRC would mix on the wire
    // (same SSRC on two streams = one mashed stream for the receiver).
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .port = 5010, .channels = 8, .channelOffset = 0, .ssrc = 0x11223344 },
        .{ .destIp = "192.0.2.20", .port = 5012, .channels = 4, .channelOffset = 8, .ssrc = 0x11223344 },
    };
    try std.testing.expectError(error.Aes67DuplicateFlowSsrc, cfg.prepare(value, 12));
    // The auto default (base + flow index) never collides.
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5012, .channels = 4, .channelOffset = 8 },
    };
    const prepared = try cfg.prepare(value, 12);
    try std.testing.expect(prepared.flows[0].ssrc != prepared.flows[1].ssrc);
}

test "Plan A multi-flow: distinct multicast groups share the default 5004 port" {
    var value = file();
    value.srcInterfaceIp = "192.0.2.63";
    value.ttl = 32;
    // No port entries at all: every flow resolves to the AES67 default 5004,
    // so the spec-preferred Plan A shape needs only one group per flow.
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.2", .channels = 8, .channelOffset = 8 },
    };
    const prepared = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[1].port);
    try std.testing.expect(prepared.flows[0].multicast and prepared.flows[1].multicast);
    try std.testing.expectEqual(@as(u8, 32), prepared.flows[0].ttl);
    try std.testing.expectEqual(@as(u8, 32), prepared.flows[1].ttl);
    // One group per flow: the (group, port) wire tuple stays unique even when
    // the port is shared (AES67 6.1.3 + 6.3 Plan A shape).
    try std.testing.expect(!std.mem.eql(u8, &prepared.flows[0].dest_ip, &prepared.flows[1].dest_ip));
    // Explicit 5004 on both flows is the exact same plan.
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5004, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.2", .port = 5004, .channels = 8, .channelOffset = 8 },
    };
    const again = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(u16, 5004), again.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5004), again.flows[1].port);
}

test "per-flow srcInterfaceIp overrides the session egress and fails closed" {
    var value = file();
    value.srcInterfaceIp = "192.0.2.63";
    value.ttl = 32;
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5004, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.2", .port = 5004, .channels = 8, .channelOffset = 8, .srcInterfaceIp = "10.0.0.9" },
    };
    const prepared = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(?[4]u8, .{ 192, 0, 2, 63 }), prepared.flows[0].source_ip);
    try std.testing.expectEqual(@as(?[4]u8, .{ 10, 0, 0, 9 }), prepared.flows[1].source_ip);
    // A per-flow source cannot rescue a sibling flow: the requirement is per
    // flow, so a multicast flow without any resolved egress still fails.
    value.srcInterfaceIp = "";
    try std.testing.expectError(error.Aes67MulticastRequiresSourceInterface, cfg.prepare(value, 16));
    // Per-flow sources without any session-level value are a complete plan.
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5004, .channels = 8, .channelOffset = 0, .srcInterfaceIp = "10.0.0.8" },
        .{ .destIp = "239.1.0.2", .port = 5004, .channels = 8, .channelOffset = 8, .srcInterfaceIp = "10.0.0.9" },
    };
    const no_session = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(?[4]u8, .{ 10, 0, 0, 8 }), no_session.flows[0].source_ip);
    try std.testing.expectEqual(@as(?[4]u8, .{ 10, 0, 0, 9 }), no_session.flows[1].source_ip);
    // Invalid per-flow sources are rejected like invalid session ones.
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5004, .channels = 8, .channelOffset = 0, .srcInterfaceIp = "10.0.0.8" },
        .{ .destIp = "239.1.0.2", .port = 5004, .channels = 8, .channelOffset = 8, .srcInterfaceIp = "239.1.1.1" },
    };
    try std.testing.expectError(error.InvalidAes67SourceInterface, cfg.prepare(value, 16));
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5004, .channels = 8, .channelOffset = 0, .srcInterfaceIp = "10.0.0" },
        .{ .destIp = "239.1.0.2", .port = 5004, .channels = 8, .channelOffset = 8, .srcInterfaceIp = "10.0.0.9" },
    };
    try std.testing.expectError(error.InvalidAes67SourceInterface, cfg.prepare(value, 16));
}

test "multicast flows may never share one group (AES67 6.1.3 unique destination)" {
    var value = file();
    value.srcInterfaceIp = "192.0.2.63";
    value.ttl = 32;
    // Same group with a different port is still one collision domain for a
    // receiver that demuxes by group: rejected regardless of port.
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.1", .port = 5012, .channels = 8, .channelOffset = 8 },
    };
    try std.testing.expectError(error.Aes67DuplicateFlowGroup, cfg.prepare(value, 16));
    // Identical (group, port) keeps the exact-duplicate error.
    value.flows = &.{
        .{ .destIp = "239.1.0.1", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.1", .port = 5010, .channels = 8, .channelOffset = 8 },
    };
    try std.testing.expectError(error.Aes67DuplicateFlowDestination, cfg.prepare(value, 16));
    // The unicast lab shape (same host, distinct ports) is still valid.
    value.flows = &.{
        .{ .destIp = "192.0.2.222", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.222", .port = 5012, .channels = 8, .channelOffset = 8 },
    };
    const unicast = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(u8, 2), unicast.flow_count);
}

test "flow payloadType inherits the session default and fails closed out of range" {
    var value = file();
    value.payloadType = 98;
    const prepared = try cfg.prepare(value, 12);
    try std.testing.expectEqual(@as(u8, 98), prepared.flows[0].payload_type);
    try std.testing.expectEqual(@as(u8, 98), prepared.flows[1].payload_type);
    // An explicit flow value wins over the session default.
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0, .payloadType = 97 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 8 },
    };
    const mixed = try cfg.prepare(value, 12);
    try std.testing.expectEqual(@as(u8, 97), mixed.flows[0].payload_type);
    try std.testing.expectEqual(@as(u8, 98), mixed.flows[1].payload_type);
    // Out-of-range values fail on either level.
    value.payloadType = 95;
    try std.testing.expectError(error.InvalidAes67PayloadType, cfg.prepare(value, 12));
    value.payloadType = 96;
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .channels = 8, .channelOffset = 0, .payloadType = 128 },
        .{ .destIp = "192.0.2.20", .port = 5006, .channels = 4, .channelOffset = 8 },
    };
    try std.testing.expectError(error.InvalidAes67PayloadType, cfg.prepare(value, 12));
}

test "flow destination allocation is deterministic and bounded by class D" {
    const base = [4]u8{ 239, 1, 0, 1 };
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 1 }, cfg.allocateFlowGroup(base, 0).?);
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 2 }, cfg.allocateFlowGroup(base, 1).?);
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 3 }, cfg.allocateFlowGroup(base, 2).?);
    // Octet carry stays inside the multicast range.
    const edge = [4]u8{ 239, 1, 0, 255 };
    try std.testing.expectEqual([4]u8{ 239, 1, 1, 0 }, cfg.allocateFlowGroup(edge, 1).?);
    // Leaving class D fails closed (240.0.0.1 is reserved, not multicast).
    const last = [4]u8{ 239, 255, 255, 255 };
    try std.testing.expect(cfg.allocateFlowGroup(last, 1) == null);
    // A non-multicast base never allocates (unicast reuses the host instead).
    try std.testing.expect(cfg.allocateFlowGroup([4]u8{ 192, 0, 2, 222 }, 1) == null);
    // The public parse helper mirrors the sender's address syntax.
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 1 }, cfg.parseIpv4Address("239.1.0.1").?);
    try std.testing.expect(cfg.parseIpv4Address("239.1.0") == null);
    try std.testing.expect(cfg.parseIpv4Address("") == null);
}

test "a pre-removal mirror-format config is rejected but its flow rows survive" {
    // The transport shape a pre-removal deployment saved: top-level
    // destIp/port plus explicit flows. The top-level pair was never merged
    // upstream and is rejected loudly now - the flow rows themselves are the
    // surviving, valid part of that config.
    var value = cfg.File{
        .destIp = "239.1.0.1",
        .port = 5010,
        .srcInterfaceIp = "192.0.2.63",
        .ssrc = 1314082870,
        .ttl = 32,
        .ringPackets = 512,
        .flows = &.{
            .{ .destIp = "239.1.0.1", .port = 5010, .channels = 8, .channelOffset = 0, .payloadType = 96, .ssrc = 1314082870, .ttl = 32 },
            .{ .destIp = "239.1.0.2", .port = 5012, .channels = 8, .channelOffset = 8, .payloadType = 96, .ssrc = 1314082871, .ttl = 32 },
        },
    };
    const channel_map = [_]u16{ 6, 7, 2, 3, 8, 9, 4, 5, 0, 1, 10, 11, 14, 15, 12, 13 };
    value.channelMap = &channel_map;
    try std.testing.expectError(error.Aes67LegacyMirrorFormat, cfg.prepare(value, 16));
    // Strip only the legacy pair: the same flow rows then prepare unchanged.
    value.destIp = null;
    value.port = null;
    const prepared = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqualStrings("239.1.0.1", std.mem.sliceTo(&prepared.flows[0].dest_text, 0));
    try std.testing.expectEqualStrings("239.1.0.2", std.mem.sliceTo(&prepared.flows[1].dest_text, 0));
    try std.testing.expectEqual(@as(u16, 5010), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5012), prepared.flows[1].port);
    try std.testing.expectEqual(@as(u32, 1314082870), prepared.flows[0].ssrc);
    try std.testing.expectEqual(@as(u32, 1314082871), prepared.flows[1].ssrc);
    try std.testing.expectEqual(@as(?[4]u8, .{ 192, 0, 2, 63 }), prepared.flows[1].source_ip);
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 1 }, prepared.dest_ip);
    try std.testing.expectEqual(@as(u16, 5010), prepared.port);
}

test "explicit channelMap permutes wire order and must be a permutation" {
    var value = file();
    const map = [_]u16{ 6, 7, 2, 3, 8, 9, 4, 5, 0, 1, 10, 11, 14, 15, 12, 13 }; // 9.1.6 -> 16ch container order
    value.channelMap = &map;
    value.flows = &.{
        .{ .destIp = "192.0.2.20", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "192.0.2.20", .port = 5012, .channels = 8, .channelOffset = 8 },
    };
    const prepared = try cfg.prepare(value, 16);
    try std.testing.expectEqual(@as(u16, 6), prepared.channel_map[0]); // wire L <- 9.1.6 L
    try std.testing.expectEqual(@as(u16, 0), prepared.channel_map[8]); // wire Lw <- 9.1.6 Lw
    var bad = map;
    bad[1] = 6;
    value.channelMap = &bad;
    try std.testing.expectError(error.InvalidAes67ChannelMap, cfg.prepare(value, 16));
    const short = [_]u16{ 0, 1 };
    value.channelMap = &short;
    try std.testing.expectError(error.InvalidAes67ChannelMap, cfg.prepare(value, 16));
    const out_of_range = [_]u16{ 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16 };
    value.channelMap = &out_of_range;
    try std.testing.expectError(error.InvalidAes67ChannelMap, cfg.prepare(value, 16));
}
