//! AES67 transport configuration. This module owns network/transport
//! validation only; channel count and channel order are derived by the caller
//! from outputLayout/routing, never from a fixed layout here.
const std = @import("std");

pub const max_channels: u16 = 16;
pub const frames_per_packet: u16 = 48;
pub const sample_rate: u32 = 48000;
pub const default_port: u16 = 5004;
pub const default_payload_type: u8 = 96;
/// Fixed default SSRC "NSP6". A configured nonzero value overrides it.
pub const default_ssrc: u32 = 0x4E535036;
pub const default_ring_packets: u16 = 64;
pub const default_prebuffer_frames: u32 = 480;
pub const default_ptp_domain: u16 = 0;

/// Parsed playback.json `aes67` object. Slices borrow the parsed document.
pub const File = struct {
    destIp: []const u8,
    port: u16 = default_port,
    framesPerPacket: u16 = frames_per_packet,
    payloadType: u8 = default_payload_type,
    ssrc: u32 = 0,
    srcInterfaceIp: []const u8 = "",
    ptpGmid: []const u8 = "",
    ptpDomain: u16 = default_ptp_domain,
    ringPackets: u16 = default_ring_packets,
    prebufferFrames: u32 = default_prebuffer_frames,
};

/// Validated, fixed-size transport settings consumed by the sender.
pub const Prepared = struct {
    dest_ip: [4]u8,
    port: u16,
    frames_per_packet: u16,
    payload_type: u8,
    ssrc: u32,
    src_interface_ip: ?[4]u8,
    ptp_gmid: ?[23]u8,
    ptp_domain: u16,
    ring_packets: u16,
    prebuffer_frames: u32,
    channels: u16,
};

fn parseIpv4(text: []const u8, out: *[4]u8) bool {
    var parts = std.mem.splitScalar(u8, text, '.');
    var count: usize = 0;
    while (parts.next()) |part| {
        if (count == 4 or part.len == 0 or part.len > 3) return false;
        var value: u32 = 0;
        for (part) |ch| {
            if (ch < '0' or ch > '9') return false;
            value = value * 10 + (ch - '0');
            if (value > 255) return false;
        }
        out[count] = @intCast(value);
        count += 1;
    }
    return count == 4;
}

fn parseGmid(text: []const u8) !?[23]u8 {
    if (text.len == 0) return null;
    if (text.len != 23) return error.InvalidAes67PtpGmid;
    var out: [23]u8 = undefined;
    for (text, 0..) |ch, index| {
        if (index % 3 == 2) {
            if (ch != '-') return error.InvalidAes67PtpGmid;
            out[index] = ch;
        } else {
            if (!std.ascii.isHex(ch)) return error.InvalidAes67PtpGmid;
            out[index] = std.ascii.toUpper(ch);
        }
    }
    return out;
}

fn isUnicastIpv4(address: [4]u8) bool {
    // Unicast only: no unspecified, multicast or limited broadcast destination.
    if (address[0] == 0 or address[0] == 255) return false;
    if (address[0] >= 224) return false;
    return true;
}

/// Validates the JSON object and resolves defaults. Fail-closed: every value
/// used by the transport is checked here before a socket or session exists.
pub fn prepare(file: File, channels: usize) !Prepared {
    if (channels == 0 or channels > max_channels) return error.Aes67ChannelCount;
    if (file.port == 0) return error.InvalidAes67Port;
    if (file.framesPerPacket != frames_per_packet) return error.UnsupportedAes67PacketFrames;
    if (file.payloadType < 96 or file.payloadType > 127) return error.InvalidAes67PayloadType;
    if (file.ptpDomain > 127) return error.InvalidAes67PtpDomain;
    if (file.ringPackets < 8 or file.ringPackets > 1024) return error.InvalidAes67RingPackets;
    if (file.prebufferFrames < frames_per_packet or file.prebufferFrames > 48000 or
        file.prebufferFrames > @as(u32, file.ringPackets) * frames_per_packet) return error.InvalidAes67Prebuffer;
    var dest: [4]u8 = undefined;
    if (!parseIpv4(file.destIp, &dest)) return error.InvalidAes67Destination;
    if (!isUnicastIpv4(dest)) return error.Aes67DestinationMustBeUnicast;
    var source: ?[4]u8 = null;
    if (file.srcInterfaceIp.len != 0) {
        var value: [4]u8 = undefined;
        if (!parseIpv4(file.srcInterfaceIp, &value)) return error.InvalidAes67SourceInterface;
        if (!isUnicastIpv4(value)) return error.InvalidAes67SourceInterface;
        source = value;
    }
    return .{
        .dest_ip = dest,
        .port = file.port,
        .frames_per_packet = file.framesPerPacket,
        .payload_type = file.payloadType,
        .ssrc = if (file.ssrc == 0) default_ssrc else file.ssrc,
        .src_interface_ip = source,
        .ptp_gmid = try parseGmid(file.ptpGmid),
        .ptp_domain = file.ptpDomain,
        .ring_packets = file.ringPackets,
        .prebuffer_frames = file.prebufferFrames,
        .channels = @intCast(channels),
    };
}

/// Renders the dotted IPv4 form used in reports and SDP.
pub fn formatIpv4(address: [4]u8) [15]u8 {
    var out: [15]u8 = [_]u8{0} ** 15;
    const text = std.fmt.bufPrint(&out, "{d}.{d}.{d}.{d}", .{ address[0], address[1], address[2], address[3] }) catch return out;
    _ = text;
    return out;
}
