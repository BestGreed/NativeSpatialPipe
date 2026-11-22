//! Offline-gated 16ch AES67 2 x 8ch RTP/L24 network sender for acceptance
//! evidence (P0 packet-level + 16ch per-channel signal tests).
//!
//! This is a test harness around the production sender modules
//! (aes67_config / aes67_rtp / aes67_tx): it builds the same Prepared transport
//! plan and one Tx (single sender thread, shared timestamp timeline), then
//! streams 16 per-channel sine tones (channel k = base + k*step Hz, distinct
//! amplitude) into the bounded ring for a fixed duration.
//!
//! Usage (run on the NSP machine; no playback.json / FSA session required):
//!   zig build-exe modules/zig-scheduler/aes67_net_tool.zig -target x86_64-windows-gnu
//!     -O ReleaseSafe -lc -lws2_32 -ladvapi32 -luser32 -femit-bin=build/nsp16-send.exe
//!   nsp16-send.exe  (env NSP_AES67_NET_TEST=1 ... see readEnv below)
//!
//! Env:
//!   NSP_AES67_NET_TEST=1           required gate (never sends otherwise)
//!   NSP_AES67_NET_DEST0/DEST1      flow0/flow1 unicast destination (default 127.0.0.1)
//!   NSP_AES67_NET_PORT0/PORT1      flow0/flow1 UDP port (default 5010/5012)
//!   NSP_AES67_NET_SRC              optional source interface IPv4
//!   NSP_AES67_NET_SECONDS          duration seconds (default 10)
//!   NSP_AES67_NET_SDP              optional path to write the 2-session SDP
//!   NSP_AES67_NET_JSON             optional path to write the final stats JSON
//!   NSP_AES67_NET_FREQ0/STEP       per-channel tone frequencies (default 1000/100 Hz)
const std = @import("std");
const cfg = @import("aes67_config.zig");
const rtp = @import("aes67_rtp.zig");
const tx = @import("aes67_tx.zig");

const variants: [16][]const u8 = .{
    "L", "R", "C", "LFE", "Ls", "Rs", "Lrs", "Rrs",
    "Lw", "Rw", "Ltf", "Rtf", "Aux12", "Aux13", "Ltr", "Rtr",
};

fn envText(allocator: std.mem.Allocator, name: []const u8, fallback: []const u8) ![]u8 {
    if (std.process.getEnvVarOwned(allocator, name)) |value| return value else |_| {}
    return allocator.dupe(u8, fallback);
}

fn envU32(allocator: std.mem.Allocator, name: []const u8, fallback: u32) !u32 {
    const text = std.process.getEnvVarOwned(allocator, name) catch return fallback;
    defer allocator.free(text);
    return std.fmt.parseInt(u32, text, 10);
}

fn writeSdp(path: []const u8, prepared: cfg.Prepared) !void {
    var labels: [16][]const u8 = undefined;
    for (variants, 0..) |label, index| labels[index] = label;
    var params: [cfg.max_flows]rtp.SdpParams = undefined;
    // Iterate by pointer: &flow.dest_text must point into the stable prepared
    // array, not a per-iteration copy (dangling capture wrote the last flow's
    // group into every session's c= line).
    for (prepared.flows[0..prepared.flow_count], 0..) |*flow, index| {
        const offset: usize = flow.channel_offset;
        params[index] = .{
            .session_name = "NativeSpatialPipe AES67 output",
            .dest_ip = std.mem.sliceTo(&flow.dest_text, 0),
            .port = flow.port,
            .channels = flow.channels,
            .frames_per_packet = prepared.frames_per_packet,
            .payload_type = flow.payload_type,
            .ssrc = flow.ssrc,
            .ptp_gmid = null,
            .ptp_domain = 0,
            // Informational layout token from the channel count (no device names).
            .layout_id = "16ch",
            .channel_labels = labels[offset .. offset + flow.channels],
            .flow_index = @intCast(index),
            .channel_offset = flow.channel_offset,
        };
    }
    var buffer: [rtp.sdp_max_bytes]u8 = undefined;
    const size = try rtp.writeSdpMulti(&buffer, params[0..prepared.flow_count]);
    var file = try std.fs.cwd().createFile(path, .{});
    defer file.close();
    try file.writeAll(buffer[0..size]);
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    const gate = std.process.getEnvVarOwned(allocator, "NSP_AES67_NET_TEST") catch {
        std.debug.print("NSP_AES67_NET_TEST is not set; refusing to send\n", .{});
        return error.NetTestNotEnabled;
    };
    allocator.free(gate);

    const dest0 = try envText(allocator, "NSP_AES67_NET_DEST0", "127.0.0.1");
    defer allocator.free(dest0);
    const dest1 = try envText(allocator, "NSP_AES67_NET_DEST1", "127.0.0.1");
    defer allocator.free(dest1);
    const port0 = try envU32(allocator, "NSP_AES67_NET_PORT0", 5010);
    const port1 = try envU32(allocator, "NSP_AES67_NET_PORT1", 5012);
    const seconds = try envU32(allocator, "NSP_AES67_NET_SECONDS", 10);
    const freq0 = try envU32(allocator, "NSP_AES67_NET_FREQ0", 1000);
    const step = try envU32(allocator, "NSP_AES67_NET_STEP", 100);
    const src = try envText(allocator, "NSP_AES67_NET_SRC", "");
    defer allocator.free(src);
    // This acceptance harness always streams 16ch as 2 x 8ch flows.
    const channels: u32 = 16;

    const total: u16 = @intCast(channels);
    const flows16 = [_]cfg.Flow{
        .{ .destIp = dest0, .port = @intCast(port0), .channels = 8, .channelOffset = 0 },
        .{ .destIp = dest1, .port = @intCast(port1), .channels = 8, .channelOffset = 8 },
    };
    // 9.1.6 renderer order -> 16ch container order (explicit mapping, never implicit):
    // wire [L,R,C,LFE,Ls,Rs,Lrs,Rrs,Lw,Rw,Ltf,Rtf,Aux12,Aux13,Ltr,Rtr]
    // 9.1.6 idx [6,7,2,3,8,9,4,5,0,1,10,11,14,15,12,13]
    const channel_map = [_]u16{ 6, 7, 2, 3, 8, 9, 4, 5, 0, 1, 10, 11, 14, 15, 12, 13 };
    const prepared = try cfg.prepare(.{
        .framesPerPacket = 48,
        .payloadType = 96,
        .srcInterfaceIp = src,
        .ringPackets = 1024,
        .prebufferFrames = 960,
        .flows = &flows16,
        .channelMap = &channel_map,
    }, channels);

    const sdp_path = try envText(allocator, "NSP_AES67_NET_SDP", "");
    defer allocator.free(sdp_path);
    if (sdp_path.len != 0) try writeSdp(sdp_path, prepared);

    const sender = try tx.Tx.create(allocator, prepared);
    defer sender.destroy();

    // Per-channel sine table: 1 second of samples per channel at 48 kHz.
    // Channel k amplitude = 0.05 + 0.02*k (distinct RMS), frequency = f0 + k*step.
    const input = try allocator.alloc(f32, 48000 * channels);
    defer allocator.free(input);
    for (0..48000) |frame| {
        const t = @as(f64, @floatFromInt(frame)) / 48000.0;
        for (0..channels) |channel| {
            const hz = @as(f64, @floatFromInt(freq0 + @as(u32, @intCast(channel)) * step));
            const amp = 0.05 + 0.02 * @as(f64, @floatFromInt(channel));
            input[frame * channels + channel] = @floatCast(std.math.sin(2.0 * std.math.pi * hz * t) * amp);
        }
    }
    var identity: [16]u16 = undefined;
    for (0..channels) |index| identity[index] = @intCast(index);

    try sender.start();
    var submitted: u64 = 0;
    var frame: usize = 0;
    const total_frames: u64 = @as(u64, seconds) * 48000;
    while (submitted < total_frames) {
        // NOTE: Zig 0.13 @min infers the smallest integer type (u13 here); annotate
        // the destination as usize before multiplying, otherwise block * 16 overflows.
        const block: usize = @min(@as(usize, 480), @as(usize, @intCast(total_frames - submitted)));
        if (frame + block > 48000) frame = 0;
        const start_index: usize = frame * channels;
        const take: usize = block * channels;
        try sender.submit(input[start_index..][0..take], @intCast(block), total, identity[0..channels]);
        frame += block;
        submitted += block;
        // Pace submits in real time: 48 frames = 1 ms, so block frames = block/48 ms.
        std.time.sleep(@as(u64, @intCast(block)) * std.time.ns_per_ms / 48);
    }
    // Keep the sender pacing until the ring drains (bounded wait).
    const drain_start = std.time.milliTimestamp();
    while (sender.snapshot().buffered_frames > 0) {
        if (std.time.milliTimestamp() - drain_start > 5000) break;
        std.time.sleep(2 * std.time.ns_per_ms);
    }
    sender.stop();
    const stats = sender.snapshot();

    var json = std.io.getStdOut().writer();
    try json.print("{{\"tool\":\"nsp16-send\",\"channels\":{d},\"flow_count\":{d},\"frames_per_packet\":{d},\"seconds\":{d},\"frames_submitted\":{d},\"packets_sent\":{d},\"silent_packets\":{d},\"send_errors\":{d},\"late_ticks\":{d},\"buffered_frames\":{d}", .{
        channels, stats.flow_count, stats.frames_per_packet, seconds, stats.frames_submitted, stats.packets_sent, stats.silent_packets, stats.send_errors, stats.late_ticks, stats.buffered_frames,
    });
    for (stats.flows[0..stats.flow_count], 0..) |flow, index| {
        try json.print(",\"flow{d}\":{{\"ssrc\":{d},\"channels\":{d},\"channel_offset\":{d},\"packets_sent\":{d},\"last_seq\":{d},\"last_timestamp\":{d},\"send_errors\":{d}}}", .{
            index, flow.ssrc, flow.channels, flow.channel_offset, flow.packets_sent, flow.last_seq, flow.last_timestamp, flow.send_errors,
        });
    }
    try json.print("}}\n", .{});
    const json_path = try envText(allocator, "NSP_AES67_NET_JSON", "");
    defer allocator.free(json_path);
    if (json_path.len != 0) {
        var file = try std.fs.cwd().createFile(json_path, .{});
        defer file.close();
        var buffer: [2048]u8 = undefined;
        const text = try std.fmt.bufPrint(&buffer, "{{\"channels\":16,\"flow_count\":{d},\"frames_submitted\":{d},\"packets_sent\":{d},\"silent_packets\":{d},\"send_errors\":{d},\"late_ticks\":{d},\"flow0\":{{\"ssrc\":{d},\"packets_sent\":{d},\"last_seq\":{d},\"last_timestamp\":{d}}},\"flow1\":{{\"ssrc\":{d},\"packets_sent\":{d},\"last_seq\":{d},\"last_timestamp\":{d}}}}}\n", .{
            stats.flow_count, stats.frames_submitted, stats.packets_sent, stats.silent_packets, stats.send_errors, stats.late_ticks,
            stats.flows[0].ssrc, stats.flows[0].packets_sent, stats.flows[0].last_seq, stats.flows[0].last_timestamp,
            stats.flows[1].ssrc, stats.flows[1].packets_sent, stats.flows[1].last_seq, stats.flows[1].last_timestamp,
        });
        try file.writeAll(text);
    }
}
