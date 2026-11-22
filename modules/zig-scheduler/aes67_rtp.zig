//! AES67 RTP/L24 packet and SDP construction.
//! Pure serialization only: no sockets, threads, layout or DSP policy. The
//! channel count, order and labels are supplied by the caller from the existing
//! output layout and routing configuration, so this module never downmixes or
//! rewires channels.
const std = @import("std");

pub const sample_rate: u32 = 48000;
pub const frames_per_packet: u16 = 48;
pub const header_bytes: usize = 12;
pub const max_channels: u16 = 16;
pub const default_payload_type: u8 = 96;
pub const sdp_max_bytes: usize = 8192;

pub fn packetBytes(frames: u16, channels: u16) usize {
    return header_bytes + @as(usize, frames) * @as(usize, channels) * 3;
}

/// 48 frames at 48 kHz is 1 ms per packet.
pub fn ptimeMs(frames: u16) u16 {
    return frames / frames_per_packet;
}

/// 12-byte RTP/AVP header: V=2, P=0, X=0, CC=0, M=0, dynamic payload type.
pub fn writeHeader(dst: *[header_bytes]u8, seq: u16, timestamp: u32, ssrc: u32, payload_type: u8) void {
    dst[0] = 0x80;
    dst[1] = payload_type & 0x7f;
    dst[2] = @intCast(seq >> 8);
    dst[3] = @intCast(seq & 0xff);
    dst[4] = @intCast(timestamp >> 24);
    dst[5] = @intCast((timestamp >> 16) & 0xff);
    dst[6] = @intCast((timestamp >> 8) & 0xff);
    dst[7] = @intCast(timestamp & 0xff);
    dst[8] = @intCast(ssrc >> 24);
    dst[9] = @intCast((ssrc >> 16) & 0xff);
    dst[10] = @intCast((ssrc >> 8) & 0xff);
    dst[11] = @intCast(ssrc & 0xff);
}

/// Interleaved float32 in [-1, 1] to big-endian signed 24-bit PCM. NaN becomes
/// digital silence; out-of-range samples are clamped. +1.0 maps to 0x7FFFFF and
/// -1.0 maps to 0x800000 (the asymmetric L24 rails).
pub fn encodeL24(sample: f32, dst: *[3]u8) void {
    if (!(sample == sample)) {
        dst.* = .{ 0, 0, 0 };
        return;
    }
    var value = sample;
    if (value > 1.0) value = 1.0;
    if (value < -1.0) value = -1.0;
    var integer: i32 = @intFromFloat(@round(@as(f64, value) * 8388608.0));
    if (integer > 8388607) integer = 8388607;
    if (integer < -8388608) integer = -8388608;
    const bits: u32 = @bitCast(integer);
    dst.* = .{
        @intCast((bits >> 16) & 0xff),
        @intCast((bits >> 8) & 0xff),
        @intCast(bits & 0xff),
    };
}

/// Build one RTP packet from already channel-mapped interleaved PCM.
pub fn buildPacket(dst: []u8, pcm: []const f32, frames: u16, channels: u16, seq: u16, timestamp: u32, ssrc: u32, payload_type: u8) !usize {
    if (frames == 0 or channels == 0 or channels > max_channels) return error.InvalidAes67Packet;
    const samples = @as(usize, frames) * @as(usize, channels);
    if (pcm.len < samples) return error.InvalidAes67Packet;
    const size = packetBytes(frames, channels);
    if (dst.len < size) return error.PacketBufferTooSmall;
    writeHeader(dst[0..header_bytes], seq, timestamp, ssrc, payload_type);
    const payload = dst[header_bytes..size];
    for (pcm[0..samples], 0..) |sample, index| encodeL24(sample, payload[index * 3 ..][0..3]);
    return size;
}

/// Build one RTP packet of digital silence (underrun / prebuffer behaviour).
pub fn buildSilentPacket(dst: []u8, frames: u16, channels: u16, seq: u16, timestamp: u32, ssrc: u32, payload_type: u8) !usize {
    if (frames == 0 or channels == 0 or channels > max_channels) return error.InvalidAes67Packet;
    const size = packetBytes(frames, channels);
    if (dst.len < size) return error.PacketBufferTooSmall;
    writeHeader(dst[0..header_bytes], seq, timestamp, ssrc, payload_type);
    @memset(dst[header_bytes..size], 0);
    return size;
}

/// RTP sequence and timestamp state. One packet always advances the sequence by
/// one and the timestamp by exactly frames_per_packet samples.
pub const Timeline = struct {
    seq: u16 = 0,
    timestamp: u32 = 0,
    pub fn advance(self: *Timeline, frames: u16) void {
        self.seq +%= 1;
        self.timestamp +%= frames;
    }
};

/// Informative ST 2110-30 channel-order spelling for the layouts carried by the
/// public tree. AES67 itself has no normative channel-order token; the
/// authoritative order is the WAV/rtpmap order plus the channel-label comment.
pub fn smpteChannelOrder(layout: []const u8) ?[]const u8 {
    const table = [_]struct { id: []const u8, token: []const u8 }{
        .{ .id = "5.1.2", .token = "SMPTE2110.(51,2)" },
        .{ .id = "5.1.4", .token = "SMPTE2110.(51,4)" },
        .{ .id = "7.1.2", .token = "SMPTE2110.(71,2)" },
        .{ .id = "7.1.4", .token = "SMPTE2110.(71,4)" },
        .{ .id = "7.1.6", .token = "SMPTE2110.(71,6)" },
        .{ .id = "9.1.4", .token = "SMPTE2110.(91,4)" },
        .{ .id = "9.1.6", .token = "SMPTE2110.(91,6)" },
    };
    for (table) |row| if (std.mem.eql(u8, row.id, layout)) return row.token;
    return null;
}

pub const SdpParams = struct {
    session_name: []const u8 = "NativeSpatialPipe AES67 output",
    origin_addr: []const u8 = "0.0.0.0",
    dest_ip: []const u8,
    port: u16 = 5004,
    channels: u16,
    frames_per_packet: u16 = 48,
    payload_type: u8 = default_payload_type,
    ssrc: u32,
    ptp_gmid: ?[]const u8 = null,
    ptp_domain: u16 = 0,
    layout_id: []const u8 = "",
    channel_labels: []const []const u8 = &.{},
    /// Multi-flow session decoration: flow index and session-wide channel offset
    /// (0-based). Only emitted by writeSdpMulti; single-flow SDP stays clean.
    flow_index: u8 = 0,
    channel_offset: u16 = 0,
    multi_flow: bool = false,
    /// Multicast TTL: when set, c= is written as "c=IN IP4 <group>/<ttl>"
    /// (RFC 4566 requires the /ttl suffix for multicast connections). Null keeps
    /// the plain unicast form "c=IN IP4 <addr>".
    ttl: ?u8 = null,
    /// Optional RFC 4570 single-source filter (multicast only). Requires
    /// source_addr; receivers that ignore it are unaffected.
    source_filter: bool = false,
    source_addr: ?[]const u8 = null,
};

/// AES67 payload budget check for one flow (RTP header excluded).
pub fn payloadFits(frames: u16, channels: u16) bool {
    return channels != 0 and channels <= max_channels and
        @as(usize, frames) * channels * 3 <= 1440;
}

fn writeSession(writer: anytype, params: SdpParams, write_comments: bool) !void {
    writer.print("v=0\r\n", .{}) catch return error.SdpBufferTooSmall;
    writer.print("o=- {d} {d} IN IP4 {s}\r\n", .{ params.ssrc, params.ssrc, params.origin_addr }) catch return error.SdpBufferTooSmall;
    if (write_comments) {
        writer.print("s={s} flow{d}\r\n", .{ params.session_name, params.flow_index }) catch return error.SdpBufferTooSmall;
    } else {
        writer.print("s={s}\r\n", .{params.session_name}) catch return error.SdpBufferTooSmall;
    }
    if (params.ttl) |ttl| {
        writer.print("c=IN IP4 {s}/{d}\r\n", .{ params.dest_ip, ttl }) catch return error.SdpBufferTooSmall;
        if (params.source_filter) {
            if (params.source_addr) |address| {
                writer.print("a=source-filter: incl IN IP4 {s} {s}\r\n", .{ params.dest_ip, address }) catch return error.SdpBufferTooSmall;
            }
        }
    } else {
        writer.print("c=IN IP4 {s}\r\n", .{params.dest_ip}) catch return error.SdpBufferTooSmall;
    }
    writer.print("t=0 0\r\n", .{}) catch return error.SdpBufferTooSmall;
    writer.print("m=audio {d} RTP/AVP {d}\r\n", .{ params.port, params.payload_type }) catch return error.SdpBufferTooSmall;
    writer.print("a=rtpmap:{d} L24/48000/{d}\r\n", .{ params.payload_type, params.channels }) catch return error.SdpBufferTooSmall;
    writer.print("a=ptime:{d}\r\n", .{ptimeMs(params.frames_per_packet)}) catch return error.SdpBufferTooSmall;
    writer.print("a=sendonly\r\n", .{}) catch return error.SdpBufferTooSmall;
    if (params.ptp_gmid) |gmid| {
        writer.print("a=ts-refclk:ptp=IEEE1588-2008:{s}:{d}\r\n", .{ gmid, params.ptp_domain }) catch return error.SdpBufferTooSmall;
        writer.print("a=mediaclk:direct=0\r\n", .{}) catch return error.SdpBufferTooSmall;
    } else {
        // Phase 1 (PTP not wired): RFC 7273 local clock + sender media clock.
        // localmac is not RFC 7273 syntax and is no longer emitted; the receiver
        // keeps it as legacy tolerance only. Reports must state ptp=not-wired.
        writer.print("a=ts-refclk:local\r\n", .{}) catch return error.SdpBufferTooSmall;
        writer.print("a=mediaclk:sender\r\n", .{}) catch return error.SdpBufferTooSmall;
    }
    if (smpteChannelOrder(params.layout_id)) |token| {
        writer.print("a=fmtp:{d} channel-order={s}\r\n", .{ params.payload_type, token }) catch return error.SdpBufferTooSmall;
    }
    if (params.layout_id.len != 0) {
        writer.print("a=comment:layout={s}\r\n", .{params.layout_id}) catch return error.SdpBufferTooSmall;
    }
    if (params.channel_labels.len != 0) {
        writer.print("a=comment:channel-labels=", .{}) catch return error.SdpBufferTooSmall;
        for (params.channel_labels, 0..) |label, index| {
            writer.print("{s}{s}", .{ if (index == 0) "" else ",", label }) catch return error.SdpBufferTooSmall;
        }
        writer.print("\r\n", .{}) catch return error.SdpBufferTooSmall;
    }
    if (write_comments) {
        writer.print("a=comment:flow={d}\r\n", .{params.flow_index}) catch return error.SdpBufferTooSmall;
        writer.print("a=comment:channel-offset={d}\r\n", .{params.channel_offset}) catch return error.SdpBufferTooSmall;
    }
}

fn validateSdpParams(params: SdpParams) !void {
    if (params.channels == 0 or params.channels > max_channels) return error.InvalidAes67Sdp;
    if (params.frames_per_packet == 0 or params.frames_per_packet % frames_per_packet != 0) return error.InvalidAes67Sdp;
    if (params.dest_ip.len == 0 or params.payload_type < 96 or params.payload_type > 127) return error.InvalidAes67Sdp;
    if (params.ttl) |ttl| if (ttl == 0) return error.InvalidAes67Sdp;
    if (params.channel_labels.len != 0 and params.channel_labels.len != params.channels) return error.InvalidAes67Sdp;
}

/// Writes one CRLF SDP session description for a single RTP/L24 flow.
pub fn writeSdp(out: []u8, params: SdpParams) !usize {
    try validateSdpParams(params);
    var stream = std.io.fixedBufferStream(out);
    try writeSession(stream.writer(), params, false);
    return stream.pos;
}

/// Writes one SDP file containing one v=0 session per flow (2 sessions for
/// 16ch = 2 x 8ch). Flow order must match channel_offset order.
pub fn writeSdpMulti(out: []u8, params: []const SdpParams) !usize {
    if (params.len == 0) return error.InvalidAes67Sdp;
    var stream = std.io.fixedBufferStream(out);
    const writer = stream.writer();
    for (params, 0..) |flow, index| {
        try validateSdpParams(flow);
        var decorated = flow;
        decorated.flow_index = @intCast(index);
        decorated.multi_flow = true;
        try writeSession(writer, decorated, true);
    }
    return stream.pos;
}
