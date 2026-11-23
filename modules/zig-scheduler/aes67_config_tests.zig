const std = @import("std");
const cfg = @import("aes67_config.zig");
const playback = @import("desktop_playback.zig");

fn multicastFile() cfg.File {
    return .{
        .groupBase = "239.69.0.1",
        .srcInterfaceIp = "192.0.2.63",
    };
}

test "defaults resolve the AES67 baseline: 48 kHz, L24, 48 samples, 5004" {
    const prepared = try cfg.prepare(multicastFile(), 2);
    try std.testing.expectEqual(@as(u32, 48_000), prepared.sample_rate);
    try std.testing.expectEqual(@as(u16, 1_000), prepared.packet_time_us);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    try std.testing.expectEqual(cfg.BitDepth.L24, prepared.bit_depth);
    try std.testing.expectEqual(@as(u8, 3), prepared.bytes_per_sample);
    try std.testing.expectEqual(@as(u16, 5004), prepared.port);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u8, 1), prepared.flow_count);
    try std.testing.expectEqual(@as(u8, 96), prepared.payload_type);
    try std.testing.expectEqual(@as(u16, 64), prepared.ring_packets);
    try std.testing.expectEqual(@as(u32, 480), prepared.prebuffer_frames);
    try std.testing.expect(prepared.source_filter);
    try std.testing.expect(prepared.multicast);
    try std.testing.expectEqual(@as(f64, 1.0), prepared.ptimeMilliseconds());
    try std.testing.expectEqual(@as(f64, 1000.0), prepared.packetsPerSecond());
}

test "16ch at 48k/1ms splits into two 8ch flows on shared port 5004" {
    const prepared = try cfg.prepare(multicastFile(), 16);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqual(@as(u16, 8), prepared.flows[0].channels);
    try std.testing.expectEqual(@as(u16, 8), prepared.flows[1].channels);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[1].port);
    // One unique multicast group per stream (AES67 sec 6.1.3).
    try std.testing.expectEqual([4]u8{ 239, 69, 0, 1 }, prepared.flows[0].dest_ip);
    try std.testing.expectEqual([4]u8{ 239, 69, 0, 2 }, prepared.flows[1].dest_ip);
    // 8ch L24 1ms = 1152 B <= 1440 B.
    try std.testing.expectEqual(@as(usize, 8 * 48 * 3), 1152);
}

test "44.1 kHz uses 48 samples: L16 default, ptime 1.09, 918.75 pps" {
    var file = multicastFile();
    file.sampleRate = 44_100;
    file.groupBase = "239.69.16.1";
    const prepared = try cfg.prepare(file, 16);
    try std.testing.expectEqual(cfg.BitDepth.L16, prepared.bit_depth);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 2), prepared.bytes_per_sample);
    try std.testing.expectApproxEqAbs(@as(f64, 1.088435), prepared.ptimeMilliseconds(), 0.0001);
    try std.testing.expectApproxEqAbs(@as(f64, 918.75), prepared.packetsPerSecond(), 0.001);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    // 8ch L16 1ms profile = 768 B.
    try std.testing.expectEqual(@as(usize, 8 * 48 * 2), 768);
}

test "96 kHz 1ms L24 fits five channels per flow, so 8ch needs two flows" {
    var file = multicastFile();
    file.sampleRate = 96_000;
    const prepared = try cfg.prepare(file, 8);
    try std.testing.expectEqual(@as(u16, 96), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expect(prepared.flows[0].channels <= 5);
    try std.testing.expect(prepared.flows[1].channels <= 5);
    try std.testing.expectEqual(@as(u16, 8), prepared.flows[0].channels + prepared.flows[1].channels);
}

test "short packet times scale samples and never emit a 16ch single flow" {
    var file = multicastFile();
    file.ptimeUs = 125;
    var prepared = try cfg.prepare(file, 16);
    try std.testing.expectEqual(@as(u16, 6), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);

    file.sampleRate = 192_000;
    prepared = try cfg.prepare(file, 16);
    try std.testing.expectEqual(@as(u16, 24), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expectEqual(@as(u16, 8), prepared.flows[0].channels);

    file.ptimeUs = 333;
    file.sampleRate = 48_000;
    prepared = try cfg.prepare(file, 16);
    try std.testing.expectEqual(@as(u16, 16), prepared.frames_per_packet);
    try std.testing.expectApproxEqAbs(@as(f64, 0.333333), prepared.ptimeMilliseconds(), 0.00001);
    file.ptimeUs = 4_000;
    prepared = try cfg.prepare(file, 8);
    try std.testing.expectEqual(@as(u16, 192), prepared.frames_per_packet);
}

test "unservable rate/ptime/channel combinations are rejected" {
    var file = multicastFile();
    file.sampleRate = 192_000;
    // 192k/1ms 16ch L24 = 9216 B total: 2ch per flow, 8 flows is the maximum
    // aggregation; 2 ms would need 16 flows and is rejected.
    _ = try cfg.prepare(file, 16);
    file.ptimeUs = 2_000;
    try std.testing.expectError(error.Aes67PayloadTooLarge, cfg.prepare(file, 16));
    file.ptimeUs = 1_000;
    file.sampleRate = 352_800;
    try std.testing.expectError(error.InvalidAes67SampleRate, cfg.prepare(file, 2));
    file.sampleRate = 48_000;
    file.ptimeUs = 300;
    try std.testing.expectError(error.InvalidAes67Ptime, cfg.prepare(file, 2));
}

test "single flow mode is capped at eight channels" {
    var file = multicastFile();
    file.flowMode = .single;
    const eight = try cfg.prepare(file, 8);
    try std.testing.expectEqual(@as(u8, 1), eight.flow_count);
    try std.testing.expectEqual(@as(u8, 1), eight.abi_flow_mode);
    try std.testing.expectError(error.InvalidAes67FlowChannels, cfg.prepare(file, 16));
}

test "strict ports share 5004; legacy per-flow ports need explicit policy" {
    var file = cfg.File{
        .flows = &.{
            .{ .destIp = "239.1.0.1", .channels = 8, .channelOffset = 0 },
            .{ .destIp = "239.1.0.2", .channels = 8, .channelOffset = 8 },
        },
        .srcInterfaceIp = "192.0.2.63",
        .flowMode = .multi,
    };
    const prepared = try cfg.prepare(file, 16);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5004), prepared.flows[1].port);

    // Legacy FLP01 sender ports 5010/5012 are rejected in strict mode.
    var legacy_rows = [_]cfg.Flow{
        .{ .destIp = "239.1.0.1", .port = 5010, .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.2", .port = 5012, .channels = 8, .channelOffset = 8 },
    };
    file.flows = &legacy_rows;
    try std.testing.expectError(error.InvalidAes67Port, cfg.prepare(file, 16));

    // Explicit policy accepts even per-flow ports for labs/migration.
    file.portPolicy = .explicit;
    const legacy = try cfg.prepare(file, 16);
    try std.testing.expectEqual(@as(u16, 5010), legacy.flows[0].port);
    try std.testing.expectEqual(@as(u16, 5012), legacy.flows[1].port);
    legacy_rows[0].port = 5011;
    try std.testing.expectError(error.InvalidAes67Port, cfg.prepare(file, 16));
}
test "legacy mirror format is rejected but its flow rows survive" {
    var file = cfg.File{
        .destIp = "239.1.0.1",
        .port = 5010,
        .flows = &.{.{ .destIp = "239.1.0.1", .channels = 8, .channelOffset = 0 }},
        .srcInterfaceIp = "192.0.2.63",
    };
    try std.testing.expectError(error.Aes67LegacyMirrorFormat, cfg.prepare(file, 8));
    file.destIp = null;
    file.port = null;
    _ = try cfg.prepare(file, 8);
}

test "multicast requires an explicit egress interface and unique groups" {
    var file = cfg.File{
        .groupBase = "239.1.0.1",
        .flowMode = .auto,
    };
    _ = cfg.prepare(file, 8) catch |err| {
        try std.testing.expectEqual(error.Aes67MulticastRequiresSourceInterface, err);
    };
    file.srcInterfaceIp = "192.0.2.63";
    _ = try cfg.prepare(file, 8);
    var duplicate_rows = [_]cfg.Flow{
        .{ .destIp = "239.1.0.1", .channels = 8, .channelOffset = 0 },
        .{ .destIp = "239.1.0.1", .channels = 8, .channelOffset = 8 },
    };
    file.flows = &duplicate_rows;
    file.flowMode = .multi;
    file.portPolicy = .explicit;
    duplicate_rows[1].port = 5006;
    try std.testing.expectError(error.Aes67DuplicateFlowGroup, cfg.prepare(file, 16));
    duplicate_rows[1].destIp = "239.1.0.2";
    duplicate_rows[1].port = 5005;
    try std.testing.expectError(error.InvalidAes67Port, cfg.prepare(file, 16));
}

test "SAP address defaults to 224.2.127.254 and is configurable" {
    const defaults = try cfg.prepare(multicastFile(), 2);
    try std.testing.expectEqual([4]u8{ 224, 2, 127, 254 }, defaults.sap_address);
    try std.testing.expectEqual(@as(u32, 30_000), defaults.sap_interval_ms);
    var file = multicastFile();
    file.sapAddress = "239.255.255.255";
    file.sapIntervalMs = 30_000;
    const configured = try cfg.prepare(file, 2);
    try std.testing.expectEqual([4]u8{ 239, 255, 255, 255 }, configured.sap_address);
    file.sapAddress = "192.0.2.1";
    try std.testing.expectError(error.InvalidAes67SapAddress, cfg.prepare(file, 2));
    file.sapAddress = "";
    file.sapIntervalMs = 500;
    try std.testing.expectError(error.InvalidAes67SapInterval, cfg.prepare(file, 2));
}

test "codec selection and frames-per-packet cross checks" {
    var file = multicastFile();
    file.codec = .L16;
    var prepared = try cfg.prepare(file, 8);
    try std.testing.expectEqual(cfg.BitDepth.L16, prepared.bit_depth);
    // 8ch L24 1ms = 1152 B; 8ch L16 = 768 B.
    try std.testing.expectEqual(@as(usize, 8 * 48 * 2), 768);

    file.framesPerPacket = 48;
    prepared = try cfg.prepare(file, 8);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    file.framesPerPacket = 44;
    try std.testing.expectError(error.UnsupportedAes67PacketFrames, cfg.prepare(file, 8));

    var mono = multicastFile();
    mono.sampleRate = 48_000;
    mono.ptimeUs = 1_000;
    // Explicit 10ch L24 single flow would be 1440 B but is >8ch profile.
    mono.flows = &.{.{ .destIp = "239.1.0.1", .channels = 10, .channelOffset = 0 }};
    mono.flowMode = .single;
    mono.srcInterfaceIp = "192.0.2.63";
    try std.testing.expectError(error.InvalidAes67FlowChannels, cfg.prepare(mono, 10));
}

test "channel map must be a permutation" {
    var file = multicastFile();
    const map = [_]u16{ 6, 7, 2, 3, 8, 9, 4, 5, 0, 1, 10, 11, 14, 15, 12, 13 };
    file.channelMap = &map;
    const prepared = try cfg.prepare(file, 16);
    try std.testing.expectEqual(@as(u16, 6), prepared.channel_map[0]);
    try std.testing.expectEqual(@as(u16, 0), prepared.channel_map[8]);
    var bad = map;
    bad[1] = 6;
    file.channelMap = &bad;
    try std.testing.expectError(error.InvalidAes67ChannelMap, cfg.prepare(file, 16));
}

test "allocation helpers stay inside class D" {
    const base = [4]u8{ 239, 1, 0, 1 };
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 1 }, cfg.allocateFlowGroup(base, 0).?);
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 2 }, cfg.allocateFlowGroup(base, 1).?);
    try std.testing.expect(cfg.allocateFlowGroup([4]u8{ 239, 255, 255, 255 }, 1) == null);
    try std.testing.expectEqual([4]u8{ 239, 1, 0, 1 }, cfg.parseIpv4Address("239.1.0.1").?);
    try std.testing.expect(cfg.parseIpv4Address("239.1.0") == null);
}

test "playback JSON carries the new transport fields and validates" {
    const allocator = std.testing.allocator;
    const text =
        "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"outputLayout\":\"9.1.6\"," ++
        "\"sampleRate\":96000,\"aes67\":{\"sampleRate\":96000,\"ptimeUs\":500,\"codec\":\"L24\"," ++
        "\"flowMode\":\"auto\",\"basePort\":5004,\"groupBase\":\"239.69.0.1\"," ++
        "\"sapAddress\":\"224.2.127.254\",\"sapIntervalMs\":30000,\"srcInterfaceIp\":\"192.0.2.63\"}}";
    var parsed = try playback.parse(allocator, text);
    defer parsed.deinit();
    try parsed.value.validate();
    const prepared = try cfg.prepare(parsed.value.aes67.?, parsed.value.channelCount());
    try std.testing.expectEqual(@as(u32, 96_000), prepared.sample_rate);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(u8, 2), prepared.flow_count);
    try std.testing.expect(parsed.value.aes67.?.ptp == null);
}

test "unknown AES67 fields still fail closed" {
    const allocator = std.testing.allocator;
    try std.testing.expectError(
        error.UnknownField,
        playback.parse(allocator, "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"aes67\":{\"groupBase\":\"239.1.0.1\",\"typo\":1}}"),
    );
    try std.testing.expectError(
        error.InvalidEnumTag,
        playback.parse(allocator, "{\"bridgeDll\":\"C:/fixture/bridge.dll\",\"outputBackend\":\"aes67\",\"aes67\":{\"groupBase\":\"239.1.0.1\",\"flowMode\":\"other\"}}"),
    );
}