const std = @import("std");
const playback = @import("desktop_playback.zig");
const cfg = @import("aes67_config.zig");

fn read(allocator: std.mem.Allocator, name: []const u8) ![]u8 {
    const path = std.fmt.allocPrint(allocator, "modules/zig-scheduler/{s}", .{name}) catch return error.OutOfMemory;
    defer allocator.free(path);
    return std.fs.cwd().readFileAlloc(allocator, path, 1024 * 1024);
}
fn field(value: std.json.Value, key: []const u8) std.json.Value {
    return if (value == .object) value.object.get(key) orelse .null else .null;
}
fn items(value: std.json.Value) []std.json.Value {
    return if (value == .array) value.array.items else &.{};
}
fn backendConst(ifValue: std.json.Value) ?[]const u8 {
    const backend = field(field(field(ifValue, "properties"), "outputBackend"), "const");
    return if (backend == .string) backend.string else null;
}
fn findBranch(value: std.json.Value, backend: []const u8) ?std.json.Value {
    switch (value) {
        .object => |object| {
            if (backendConst(field(value, "if"))) |name| if (std.mem.eql(u8, name, backend)) return value;
            var iterator = object.iterator();
            while (iterator.next()) |entry| if (findBranch(entry.value_ptr.*, backend)) |found| return found;
            return null;
        },
        .array => |array| {
            for (array.items) |item| if (findBranch(item, backend)) |found| return found;
            return null;
        },
        else => return null,
    }
}
fn containsString(value: std.json.Value, needle: []const u8) bool {
    for (items(value)) |item| if (item == .string and std.mem.eql(u8, item.string, needle)) return true;
    return false;
}

test "playback schema exposes four output backends and keeps ASIO as the default" {
    const allocator = std.testing.allocator;
    const bytes = try read(allocator, "playback.schema.json");
    defer allocator.free(bytes);
    var parsed = try std.json.parseFromSlice(std.json.Value, allocator, bytes, .{});
    defer parsed.deinit();
    const properties = field(parsed.value, "properties");
    const backend = field(properties, "outputBackend");
    const values = items(field(backend, "enum"));
    try std.testing.expectEqual(@as(usize, 4), values.len);
    try std.testing.expectEqualStrings("asio", values[0].string);
    try std.testing.expectEqualStrings("aoip", values[1].string);
    try std.testing.expectEqualStrings("aes67", values[2].string);
    try std.testing.expectEqualStrings("wasapi", values[3].string);
    const default = field(backend, "default");
    try std.testing.expectEqualStrings("asio", default.string);
    // Only bridgeDll remains mandatory at the document root.
    try std.testing.expectEqual(@as(usize, 1), items(field(parsed.value, "required")).len);
    try std.testing.expect(containsString(field(parsed.value, "required"), "bridgeDll"));
}

test "AES67 schema block matches the disabled-by-default transport contract" {
    const allocator = std.testing.allocator;
    const bytes = try read(allocator, "playback.schema.json");
    defer allocator.free(bytes);
    var parsed = try std.json.parseFromSlice(std.json.Value, allocator, bytes, .{});
    defer parsed.deinit();
    const aes67 = field(field(parsed.value, "properties"), "aes67");
    try std.testing.expect(aes67 == .object);
    try std.testing.expectEqual(@as(usize, 2), items(field(aes67, "type")).len);
    const additional = field(aes67, "additionalProperties");
    try std.testing.expect(additional == .bool and !additional.bool);
    // flows are optional now: auto/single derive them from groupBase.
    try std.testing.expect(!containsString(field(aes67, "required"), "flows"));
    const properties = field(aes67, "properties");
    try std.testing.expectEqual(@as(i64, 48_000), field(field(properties, "sampleRate"), "default").integer);
    try std.testing.expectEqual(@as(i64, 1_000), field(field(properties, "ptimeUs"), "default").integer);
    try std.testing.expect(field(properties, "destIp") == .null);
    try std.testing.expect(field(properties, "port") == .null);
    const flows = field(properties, "flows");
    try std.testing.expect(flows == .object);
    try std.testing.expectEqualStrings("array", field(flows, "type").string);
    const frames = field(properties, "framesPerPacket");
    try std.testing.expectEqualStrings("integer", field(frames, "type").string);
    try std.testing.expectEqual(@as(i64, 768), field(frames, "maximum").integer);
    try std.testing.expectEqual(@as(i64, cfg.default_payload_type), field(field(properties, "payloadType"), "default").integer);
    try std.testing.expectEqual(@as(i64, cfg.default_ring_packets), field(field(properties, "ringPackets"), "default").integer);
    try std.testing.expectEqual(@as(i64, cfg.default_prebuffer_frames), field(field(properties, "prebufferFrames"), "default").integer);
    try std.testing.expectEqual(@as(i64, 96), field(field(properties, "payloadType"), "minimum").integer);
    try std.testing.expectEqual(@as(i64, 127), field(field(properties, "payloadType"), "maximum").integer);
}

test "AES67 conditional branch requires config and 48 kHz while ASIO/AoIP/WASAPI branches survive" {
    const allocator = std.testing.allocator;
    const bytes = try read(allocator, "playback.schema.json");
    defer allocator.free(bytes);
    var parsed = try std.json.parseFromSlice(std.json.Value, allocator, bytes, .{});
    defer parsed.deinit();
    const all = field(parsed.value, "allOf");
    const aes67 = findBranch(all, "aes67").?;
    const then = field(aes67, "then");
    try std.testing.expect(containsString(field(then, "required"), "aes67"));
    try std.testing.expect(containsString(field(then, "required"), "outputLayout"));
    const thenProperties = field(then, "properties");
    const source_policy = field(field(thenProperties, "sourcePolicy"), "enum");
    try std.testing.expect(containsString(source_policy, "fixed"));
    try std.testing.expect(containsString(source_policy, "unified"));
    try std.testing.expect(!containsString(source_policy, "active"));
    try std.testing.expectEqualStrings("object", field(field(thenProperties, "aes67"), "type").string);
    try std.testing.expect(field(field(thenProperties, "sampleRate"), "enum") == .array);
    const asio = findBranch(all, "asio");
    const aoip = findBranch(all, "aoip").?;
    const wasapi = findBranch(all, "wasapi").?;
    try std.testing.expect(asio == null); // ASIO is the final else branch, not a named one.
    try std.testing.expect(containsString(field(field(aoip, "then"), "required"), "aoip"));
    try std.testing.expect(containsString(field(field(wasapi, "then"), "required"), "wasapi"));
    try std.testing.expect(containsString(field(field(wasapi, "then"), "required"), "outputLayout"));
}

test "the AES67 example parses and validates through the runtime configuration" {
    const allocator = std.testing.allocator;
    const bytes = try read(allocator, "playback.aes67.example.json");
    defer allocator.free(bytes);
    var parsed = try playback.parse(allocator, bytes);
    defer parsed.deinit();
    const value = parsed.value;
    try std.testing.expectEqualStrings("aes67", value.outputBackend);
    try std.testing.expectEqualStrings("7.1.4", value.outputLayout);
    try std.testing.expectEqual(@as(usize, 12), value.channelCount());
    try std.testing.expect(value.aes67 != null);
    const prepared = try cfg.prepare(value.aes67.?, value.channelCount());
    try std.testing.expectEqual(@as(u16, 5004), prepared.port);
    try std.testing.expectEqual(@as(u16, 48), prepared.frames_per_packet);
    try std.testing.expectEqual(@as(usize, 12), (try value.sources()).len);
}

test "existing ASIO example still validates after the schema/backends change" {
    const allocator = std.testing.allocator;
    const bytes = try read(allocator, "playback.example.json");
    defer allocator.free(bytes);
    var parsed = try playback.parse(allocator, bytes);
    defer parsed.deinit();
    try std.testing.expectEqualStrings("asio", parsed.value.outputBackend);
}

test "every schema backend enum value is accepted by runtime validation" {
    var asio = playback.Config{ .bridgeDll = "C:\\fixture\\bridge.dll", .asioDll = "C:\\fixture\\asio.dll", .driver = "asio:test" };
    try asio.validate();
    var aoip = playback.Config{
        .bridgeDll = "C:\\fixture\\bridge.dll",
        .outputBackend = "aoip",
        .aoip = .{ .interfaceIp = "192.168.50.1", .mac = "f0d7afd04b1d" },
    };
    try aoip.validate();
    var aes67 = playback.Config{
        .bridgeDll = "C:\\fixture\\bridge.dll",
        .outputBackend = "aes67",
        .outputLayout = "5.1.2",
        .aes67 = .{ .flows = &.{.{ .destIp = "192.168.50.20", .channels = 8 }} },
    };
    try aes67.validate();
    var wasapi = playback.Config{
        .bridgeDll = "C:/fixture/bridge.dll",
        .outputBackend = "wasapi",
        .outputLayout = "7.1.4",
        .lsac = .{ .enabled = true, .filterFile = "C:/fixture/filters.nbf" },
        .wasapi = .{ .dllPath = "C:/fixture/wasapi.dll", .endpointId = "{0.0.0.00000000}.{77b2c5f3-cf42-4d93-8770-8528e102135e}" },
    };
    try wasapi.validate();
    var unknown = asio;
    unknown.outputBackend = "rtp";
    try std.testing.expectError(error.InvalidOutputBackend, unknown.validate());
}

test "flows sub-schema persists a full per-flow destination tuple with Plan A defaults" {
    const allocator = std.testing.allocator;
    const bytes = try read(allocator, "playback.schema.json");
    defer allocator.free(bytes);
    var parsed = try std.json.parseFromSlice(std.json.Value, allocator, bytes, .{});
    defer parsed.deinit();
    const aes67 = field(field(parsed.value, "properties"), "aes67");
    const flows = field(field(aes67, "properties"), "flows");
    const flow_items = field(flows, "items");
    // Every flow row must declare its own destination; channels stay required.
    try std.testing.expect(containsString(field(flow_items, "required"), "destIp"));
    try std.testing.expect(containsString(field(flow_items, "required"), "channels"));
    const properties = field(flow_items, "properties");
    // Plan A default: a flow row without a port entry resolves to the AES67
    // default 5004 (multiple flows may share it with distinct groups).
    try std.testing.expectEqual(@as(i64, 5004), field(field(properties, "port"), "default").integer);
    try std.testing.expectEqual(@as(i64, cfg.default_port), field(field(properties, "port"), "default").integer);
    // Per-flow egress override exists and defaults to inheriting the session value.
    const source = field(properties, "srcInterfaceIp");
    try std.testing.expectEqualStrings("string", field(source, "type").string);
    try std.testing.expectEqualStrings("", field(source, "default").string);
    // TTL stays an inherit-by-default per-flow override.
    try std.testing.expectEqual(@as(i64, 0), field(field(properties, "ttl"), "default").integer);
    // The flows contract documents the auto-derived plan, the 8-channel
    // media-profile cap and the standard shared 5004 port.
    const description = field(flows, "description");
    try std.testing.expect(description == .string);
    try std.testing.expect(std.mem.indexOf(u8, description.string, "Optional explicit flows") != null);
    try std.testing.expect(std.mem.indexOf(u8, description.string, "16ch = 2 x 8ch") != null);
    try std.testing.expect(std.mem.indexOf(u8, description.string, "5004") != null);
}
