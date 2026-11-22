//! Offline-gated AES67 multicast sender harness for compatibility evidence.
//!
//! This is a test harness around the production sender modules (aes67_config /
//! aes67_rtp / aes67_tx): it builds the same Prepared transport plan and one Tx,
//! then streams per-channel sine tones into the bounded ring. Unlike the
//! production playback path it needs no playback.json / FSA session / audio
//! device, so multicast egress (IP_MULTICAST_IF + IP_MULTICAST_TTL) and the SDP
//! shape can be verified without touching the running NSP session.
//!
//! Usage:
//!   zig build-exe modules/zig-scheduler/aes67_mcast_tool.zig -target x86_64-windows-gnu
//!     -O ReleaseSafe -lc -lws2_32 -ladvapi32 -luser32 -I modules/native-capture
//!     -I modules/audio-backends/src -femit-bin=build/mcast-send.exe
//!   NSP_AES67_MCAST_TEST=1 NSP_AES67_MCAST_* env (see below) build/mcast-send.exe
//!
//! Env:
//!   NSP_AES67_MCAST_TEST=1        required gate (never sends otherwise)
//!   NSP_AES67_MCAST_CHANNELS      2 | 8 | 16 (default 8)
//!   NSP_AES67_MCAST_DEST          flow0 destination: unicast IPv4 or 239.x group
//!   NSP_AES67_MCAST_PORT          flow0 UDP port (default 5008)
//!   NSP_AES67_MCAST_DEST1         flow1 destination (16ch only; unset derives
//!                                 group+1 from DEST when DEST is multicast -
//!                                 one group per stream per AES67 6.1.3 - and
//!                                 reuses DEST for unicast hosts)
//!   NSP_AES67_MCAST_PORT1         flow1 UDP port (16ch only; default 5012)
//!   NSP_AES67_MCAST_TTL           multicast TTL 1..255 (default 32)
//!   NSP_AES67_MCAST_SRC           source interface IPv4 (required for multicast)
//!   NSP_AES67_MCAST_SECONDS       duration seconds (default 10)
//!   NSP_AES67_MCAST_SDP           optional path to write the generated SDP
//!   NSP_AES67_MCAST_JSON          optional path to write the final stats JSON
//!   NSP_AES67_MCAST_FREQ0/STEP    per-channel tone frequencies (default 1000/100)
const std = @import("std");
const cfg = @import("aes67_config.zig");
const rtp = @import("aes67_rtp.zig");
const tx = @import("aes67_tx.zig");

const Labels16 = [16][]const u8{
    "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs",
    "Lw", "Rw", "Ltf", "Rtf", "Aux12", "Aux13", "Ltr", "Rtr",
};
const Labels8 = [8][]const u8{ "L", "R", "C", "LFE", "Ls", "Rs", "Ltf", "Rtf" };
const Labels2 = [2][]const u8{ "L", "R" };

fn envText(allocator: std.mem.Allocator, name: []const u8, fallback: []const u8) ![]u8 {
    if (std.process.getEnvVarOwned(allocator, name)) |value| return value else |_| {}
    return allocator.dupe(u8, fallback);
}

fn envU32(allocator: std.mem.Allocator, name: []const u8, fallback: u32) !u32 {
    const text = std.process.getEnvVarOwned(allocator, name) catch return fallback;
    defer allocator.free(text);
    return std.fmt.parseInt(u32, text, 10);
}

/// Informational SDP layout token derived from the channel count only (no
/// device or preset names on this public fork).
fn layoutFor(channels: u16) []const u8 {
    return switch (channels) {
        2 => "2.0",
        8 => "5.1.2",
        16 => "16ch",
        else => "",
    };
}

fn writeSdp(path: []const u8, prepared: cfg.Prepared, channels: u16) !void {
    var params: [cfg.max_flows]rtp.SdpParams = undefined;
    // Per-flow scratch must outlive the loop: iterate by pointer so
    // &flow.dest_text points into the stable prepared array (a by-value
    // capture made every session's c= read the LAST flow's group - the
    // flow0/flow1 SDP dump bug found during real-network verification).
    var source_buffers: [cfg.max_flows][15]u8 = undefined;
    for (prepared.flows[0..prepared.flow_count], 0..) |*flow, index| {
        const offset: usize = flow.channel_offset;
        const labels: []const []const u8 = switch (channels) {
            2 => &Labels2,
            8 => &Labels8,
            else => &Labels16,
        };
        const source_text: ?[]const u8 = if (flow.source_ip) |source| blk: {
            source_buffers[index] = cfg.formatIpv4(source);
            break :blk std.mem.sliceTo(&source_buffers[index], 0);
        } else null;
        params[index] = .{
            .session_name = "NativeSpatialPipe AES67 multicast test",
            .dest_ip = std.mem.sliceTo(&flow.dest_text, 0),
            .port = flow.port,
            .channels = flow.channels,
            .frames_per_packet = prepared.frames_per_packet,
            .payload_type = flow.payload_type,
            .ssrc = flow.ssrc,
            .ptp_gmid = null,
            .ptp_domain = 0,
            .layout_id = layoutFor(channels),
            .channel_labels = labels[offset .. offset + flow.channels],
            .flow_index = @intCast(index),
            .channel_offset = flow.channel_offset,
            .ttl = if (flow.multicast) flow.ttl else null,
            .source_filter = prepared.source_filter,
            .source_addr = source_text,
        };
    }
    var buffer: [rtp.sdp_max_bytes]u8 = undefined;
    const size = if (prepared.flow_count == 1)
        try rtp.writeSdp(&buffer, params[0])
    else
        try rtp.writeSdpMulti(&buffer, params[0..prepared.flow_count]);
    var file = try std.fs.cwd().createFile(path, .{});
    defer file.close();
    try file.writeAll(buffer[0..size]);
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    const gate = std.process.getEnvVarOwned(allocator, "NSP_AES67_MCAST_TEST") catch {
        std.debug.print("NSP_AES67_MCAST_TEST is not set; refusing to send\n", .{});
        return error.NetTestNotEnabled;
    };
    allocator.free(gate);

    const channels_u32 = try envU32(allocator, "NSP_AES67_MCAST_CHANNELS", 8);
    if (channels_u32 != 2 and channels_u32 != 8 and channels_u32 != 16) return error.BadChannels;
    const channels: u16 = @intCast(channels_u32);
    const dest = try envText(allocator, "NSP_AES67_MCAST_DEST", "127.0.0.1");
    defer allocator.free(dest);
    const port = try envU32(allocator, "NSP_AES67_MCAST_PORT", 5008);
    // flow1 destination: explicit env wins; unset derives group+1 for a
    // multicast DEST (AES67 6.1.3: one group per stream) and reuses the host
    // for unicast. The derived value is printed so the operator sees it.
    var dest1: []u8 = undefined;
    if (std.process.getEnvVarOwned(allocator, "NSP_AES67_MCAST_DEST1")) |value| {
        dest1 = value;
    } else |_| {
        var derived: ?[]u8 = null;
        if (channels_u32 == 16) {
            if (cfg.parseIpv4Address(dest)) |base| {
                if (cfg.isMulticastIpv4(base)) {
                    const next = cfg.allocateFlowGroup(base, 1) orelse return error.BadAes67AutoDestination;
                    const text = cfg.formatIpv4(next);
                    derived = try allocator.dupe(u8, std.mem.sliceTo(&text, 0));
                    std.debug.print("DEST1 unset: derived flow1 group {s} (base+1)\n", .{derived.?});
                }
            }
        }
        dest1 = derived orelse try allocator.dupe(u8, dest);
    }
    defer allocator.free(dest1);
    const port1 = try envU32(allocator, "NSP_AES67_MCAST_PORT1", 5012);
    const ttl = try envU32(allocator, "NSP_AES67_MCAST_TTL", cfg.default_ttl);
    const seconds = try envU32(allocator, "NSP_AES67_MCAST_SECONDS", 10);
    const freq0 = try envU32(allocator, "NSP_AES67_MCAST_FREQ0", 1000);
    const step = try envU32(allocator, "NSP_AES67_MCAST_STEP", 100);
    const src = try envText(allocator, "NSP_AES67_MCAST_SRC", "");
    defer allocator.free(src);

    var flows_storage: [2]cfg.Flow = undefined;
    var flow_count: usize = 1;
    if (channels == 16) {
        flows_storage[0] = .{ .destIp = dest, .port = @intCast(port), .channels = 8, .channelOffset = 0, .ttl = @intCast(ttl) };
        flows_storage[1] = .{ .destIp = dest1, .port = @intCast(port1), .channels = 8, .channelOffset = 8, .ttl = @intCast(ttl) };
        flow_count = 2;
    } else {
        flows_storage[0] = .{ .destIp = dest, .port = @intCast(port), .channels = channels, .channelOffset = 0, .ttl = @intCast(ttl) };
    }
    const prepared = try cfg.prepare(.{
        .framesPerPacket = 48,
        .payloadType = 96,
        .srcInterfaceIp = src,
        .ringPackets = 256,
        .prebufferFrames = 480,
        .ttl = @intCast(ttl),
        .flows = flows_storage[0..flow_count],
    }, channels);

    const sdp_path = try envText(allocator, "NSP_AES67_MCAST_SDP", "");
    defer allocator.free(sdp_path);
    if (sdp_path.len != 0) try writeSdp(sdp_path, prepared, channels);

    const sender = try tx.Tx.create(allocator, prepared);
    defer sender.destroy();

    const channel_count: usize = channels;
    const input = try allocator.alloc(f32, @as(usize, 48000) * channel_count);
    defer allocator.free(input);
    for (0..48000) |frame| {
        const seconds_f = @as(f64, @floatFromInt(frame)) / 48000.0;
        for (0..channel_count) |channel| {
            const hz = @as(f64, @floatFromInt(freq0 + @as(u32, @intCast(channel)) * step));
            const amp = 0.05 + 0.02 * @as(f64, @floatFromInt(channel % 8));
            input[frame * channel_count + channel] = @floatCast(std.math.sin(2.0 * std.math.pi * hz * seconds_f) * amp);
        }
    }
    const identity = [_]u16{ 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 };

    std.debug.print("checkpoint: tx created channels={d} flows={d}\n", .{ channels, prepared.flow_count });
    try sender.start();
    std.debug.print("checkpoint: tx started\n", .{});
    var submitted: u64 = 0;
    var frame: usize = 0;
    const total_frames: u64 = @as(u64, seconds) * 48000;
    while (submitted < total_frames) {
        const block = @min(@as(usize, 4800), @as(usize, @intCast(total_frames - submitted)));
        if (frame + block > 48000) frame = 0;
        try sender.submit(input[frame * channel_count ..][0 .. block * channel_count], @intCast(block), channels, identity[0..channels]);
        frame += block;
        submitted += block;
        // 48 kHz media pacing: block frames -> ns (not ms; the 16ch tool's
        // generic form sleeps ~20x too fast for a 4800-frame block).
        std.time.sleep(@as(u64, @intCast(block)) * std.time.ns_per_s / 48000);
    }
    const drain_start = std.time.milliTimestamp();
    while (sender.snapshot().buffered_frames > 0) {
        if (std.time.milliTimestamp() - drain_start > 5000) break;
        std.time.sleep(2 * std.time.ns_per_ms);
    }
    sender.stop();
    const stats = sender.snapshot();

    var json = std.io.getStdOut().writer();
    try json.print("{{\"tool\":\"nsp-mcast-send\",\"channels\":{d},\"flow_count\":{d},\"ttl\":{d},\"dest\":\"{s}\",\"port\":{d},\"src\":\"{s}\",\"seconds\":{d},\"frames_submitted\":{d},\"packets_sent\":{d},\"silent_packets\":{d},\"send_errors\":{d},\"late_ticks\":{d}", .{
        channels, stats.flow_count, ttl, dest, port, src, seconds, stats.frames_submitted, stats.packets_sent, stats.silent_packets, stats.send_errors, stats.late_ticks,
    });
    for (stats.flows[0..stats.flow_count], 0..) |flow, index| {
        try json.print(",\"flow{d}\":{{\"ssrc\":{d},\"channels\":{d},\"channel_offset\":{d},\"multicast\":{s},\"ttl\":{d},\"packets_sent\":{d},\"last_seq\":{d},\"last_timestamp\":{d},\"send_errors\":{d}}}", .{
            index, flow.ssrc, flow.channels, flow.channel_offset, if (flow.multicast) "true" else "false", flow.ttl, flow.packets_sent, flow.last_seq, flow.last_timestamp, flow.send_errors,
        });
    }
    try json.print("}}\n", .{});
    const json_path = try envText(allocator, "NSP_AES67_MCAST_JSON", "");
    defer allocator.free(json_path);
    if (json_path.len != 0) {
        var file = try std.fs.cwd().createFile(json_path, .{});
        defer file.close();
        var buffer: [2048]u8 = undefined;
        const text = try std.fmt.bufPrint(&buffer, "{{\"channels\":{d},\"flow_count\":{d},\"packets_sent\":{d},\"silent_packets\":{d},\"send_errors\":{d},\"late_ticks\":{d}}}\n", .{
            channels, stats.flow_count, stats.packets_sent, stats.silent_packets, stats.send_errors, stats.late_ticks,
        });
        try file.writeAll(text);
    }
}
