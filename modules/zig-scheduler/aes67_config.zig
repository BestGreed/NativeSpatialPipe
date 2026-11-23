//! AES67 transport configuration. This module owns network/transport
//! validation only; channel count and channel order are derived by the caller
//! from outputLayout/routing, never from a fixed layout here.
//!
//! Multi-flow (NSP 16ch = 2 x 8ch AES67): a 'flows' array in the playback
//! 'aes67' block splits the interleaved renderer output into N independent RTP
//! flows that share one sender thread, one timestamp timeline and one SDP file
//! (one v=0 session per flow). Every flow is bounded by the RFC 3550 AES67
//! payload budget (12 + frames*channels*3 <= 12 + 1440), so a 16ch/1ms single
//! stream (2304 B payload) is structurally impossible.
//!
//! Per-flow destinations are EXPLICIT and fail closed: flows[] is mandatory
//! for every layout (flow count = ceil(channels / 8), each flow <= 8 channels)
//! and every flow entry carries its own destIp. The sender never derives a
//! destination at runtime. The legacy single-flow mirror format (top-level
//! destIp/port with flows omitted) is rejected with Aes67LegacyMirrorFormat:
//! regenerate such configs through the UI, they are not migrated silently.
//! The one sanctioned auto-allocation (web save path) is deterministic
//! (allocateFlowGroup: flow-0 base + flow index, staying inside class D),
//! runs only at configuration-authoring time and is materialized into the
//! saved file so every per-flow address is visible in the config itself.
const std = @import("std");

pub const max_channels: u16 = 16;
/// Maximum number of flows in one session (16ch uses 2; 64ch would use 8).
pub const max_flows: u8 = 8;
/// AES67 media-profile per-flow channel limit (one RTP payload).
pub const max_flow_channels: u16 = 8;
/// AES67 RTP payload budget (RTP fixed header excluded).
pub const max_payload_bytes: usize = 1440;
pub const frames_per_packet: u16 = 48;
pub const sample_rate: u32 = 48000;
pub const default_port: u16 = 5004;
pub const default_payload_type: u8 = 96;
/// Fixed default SSRC "NSP6". A configured nonzero value overrides it.
pub const default_ssrc: u32 = 0x4E535036;
pub const default_ring_packets: u16 = 64;
pub const default_prebuffer_frames: u32 = 480;
pub const default_ptp_domain: u16 = 0;
/// RFC 4566 multicast TTL default used when a destination is a multicast group
/// and no explicit ttl is configured (AES67 SDP conventionally uses /32).
pub const default_ttl: u8 = 32;

/// One RTP flow in a multi-flow AES67 session.
///
/// channelOffset is the first wire channel of this flow in the session-wide
/// interleaved input (flow0 = 0, flow1 = 8 for 16ch). Offsets must be contiguous
/// from 0 so the union of flows is exactly the total channel set.
pub const Flow = struct {
    /// Explicit destination of this flow (required, never derived at runtime).
    destIp: []const u8,
    /// UDP port of this flow. Defaults to the AES67 default 5004, so Plan A
    /// (every flow on 5004, one group per flow) needs no port entries at all.
    port: u16 = default_port,
    channels: u16 = 8,
    channelOffset: u16 = 0,
    /// null = inherit the session-level payloadType (validated 96-127).
    payloadType: ?u8 = null,
    ssrc: u32 = 0,
    /// Multicast TTL for this flow (0 = inherit File.ttl). Ignored for unicast.
    ttl: u8 = 0,
    /// Per-flow egress interface override. Empty inherits File.srcInterfaceIp.
    /// Persisted per flow so each flow's full destination tuple
    /// (group + port + TTL + source interface) is visible in the config.
    srcInterfaceIp: []const u8 = "",
};

pub const Timestamping = enum(u32) { auto, software, hardware };
pub const Ptp = struct {
    domain: u16 = default_ptp_domain,
    lockTimeoutMs: u32 = 5000,
    timestamping: Timestamping = .auto,

    pub fn validate(self: Ptp) !void {
        if (self.domain > 127) return error.InvalidAes67PtpDomain;
        if (self.lockTimeoutMs < 100 or self.lockTimeoutMs > 120000) return error.InvalidAes67PtpLockTimeout;
    }
};

/// Parsed playback.json aes67 object. Slices borrow the parsed document.
pub const File = struct {
    /// LEGACY single-flow destination of the removed mirror format. Any value
    /// (with or without flows) fails prepare() with Aes67LegacyMirrorFormat.
    /// Kept optional so old documents parse and fail with the clear error
    /// instead of a generic unknown-field JSON error.
    destIp: ?[]const u8 = null,
    /// LEGACY single-flow port, same treatment as destIp.
    port: ?u16 = null,
    framesPerPacket: u16 = frames_per_packet,
    /// Session-level payload type (96-127); a flow row inherits it when its own
    /// payloadType is omitted.
    payloadType: u8 = default_payload_type,
    /// Session SSRC base; flow k derives base + k unless the row sets its own.
    ssrc: u32 = 0,
    srcInterfaceIp: []const u8 = "",
    ptpGmid: []const u8 = "",
    ptpDomain: u16 = default_ptp_domain,
    ptp: ?Ptp = null,
    ringPackets: u16 = default_ring_packets,
    prebufferFrames: u32 = default_prebuffer_frames,
    /// Multicast TTL (1..255) used for every multicast flow unless a flow
    /// overrides it. Emitted in SDP as c=IN IP4 <group>/<ttl> and applied with
    /// IP_MULTICAST_TTL. Ignored for unicast destinations.
    ttl: u8 = default_ttl,
    /// Optional RFC 4570 single-source filter. Emitted only for multicast flows
    /// when true; it is informational for AES67 receivers that ignore it.
    sourceFilter: bool = false,
    /// Mandatory multi-flow split: one explicit destination per flow, flow
    /// count = ceil(channels / 8), each flow <= 8 channels. Null fails with
    /// Aes67RequiresFlows - the legacy single-flow shape no longer exists.
    flows: ?[]const Flow = null,
    /// Optional wire-order channel permutation: transmitted channel k is source
    /// channel channelMap[k]. Used to emit the 16ch container order
    /// (L,R,C,LFE,Ls,Rs,Lrs,Rrs,Lw,Rw,Ltf,Rtf,Aux12,Aux13,Ltr,Rtr) from a
    /// 9.1.6 renderer layout (explicit mapping, never implicit).
    channelMap: ?[]const u16 = null,
};

/// Validated single-flow settings (fixed-size, no slices after prepare).
pub const PreparedFlow = struct {
    dest_ip: [4]u8,
    dest_text: [15]u8,
    port: u16,
    payload_type: u8,
    ssrc: u32,
    channels: u16,
    channel_offset: u16,
    /// Destination is an IPv4 multicast group (224/4).
    multicast: bool = false,
    /// Multicast TTL (0 for unicast flows).
    ttl: u8 = 0,
    /// Effective egress interface: the flow's own srcInterfaceIp when set,
    /// otherwise the session-level one. Null only for unicast flows without any
    /// configured source (multicast flows fail closed instead).
    source_ip: ?[4]u8 = null,
};

/// Validated, fixed-size transport settings consumed by the sender.
pub const Prepared = struct {
    /// Legacy single-flow mirror (= flow 0). Kept for existing callers/tests and
    /// for single-flow reports; multi-flow callers use flows[0..flow_count].
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
    /// Total channels across all flows (= sum of flow.channels).
    channels: u16,
    /// Number of RTP flows (1 = legacy single flow, 2 = 16ch 2x8ch).
    flow_count: u8,
    flows: [max_flows]PreparedFlow,
    /// Wire channel -> source channel permutation (identity when unspecified).
    channel_map: [max_channels]u16,
    /// Aggregate transport shape of flow 0 (single-flow mirror/report use).
    multicast: bool = false,
    ttl: u8 = 0,
    /// Emit a=source-filter for multicast flows when src_interface_ip is set.
    source_filter: bool = false,
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

/// IPv4 multicast class D range (224.0.0.0/4).
pub fn isMulticastIpv4(address: [4]u8) bool {
    return address[0] >= 224 and address[0] <= 239;
}

/// Parsed destination: explicit IPv4 unicast host or multicast group.
pub const Destination = struct {
    address: [4]u8,
    multicast: bool,
};

fn resolveDestination(text: []const u8) !Destination {
    var dest: [4]u8 = undefined;
    if (!parseIpv4(text, &dest)) return error.InvalidAes67Destination;
    if (isMulticastIpv4(dest)) return .{ .address = dest, .multicast = true };
    if (!isUnicastIpv4(dest)) return error.InvalidAes67Destination;
    return .{ .address = dest, .multicast = false };
}

/// Parses dotted IPv4 text into octets; null on malformed input. Public form
/// for the configuration-authoring paths (web save materialization, offline
/// tools) that must reproduce exactly the sender's address syntax.
pub fn parseIpv4Address(text: []const u8) ?[4]u8 {
    var out: [4]u8 = undefined;
    if (!parseIpv4(text, &out)) return null;
    return out;
}

/// Deterministic auto-allocation for multi-flow destinations (AES67 6.1.3:
/// one group per stream). Flow k gets `base + k` with ordinary octet carry;
/// the result must stay inside the class-D multicast range. Returns null when
/// base is not multicast or base+k would leave the range - callers must then
/// reject the configuration instead of guessing a different group. This runs
/// only at configuration-authoring time; the sender itself never derives a
/// destination, and the result is always materialized into the saved file.
pub fn allocateFlowGroup(base: [4]u8, index: u8) ?[4]u8 {
    if (!isMulticastIpv4(base)) return null;
    const value: u32 = (@as(u32, base[0]) << 24) | (@as(u32, base[1]) << 16) | (@as(u32, base[2]) << 8) | @as(u32, base[3]);
    const sum = @addWithOverflow(value, @as(u32, index));
    if (sum[1] != 0) return null;
    const next = sum[0];
    const out = [4]u8{
        @intCast((next >> 24) & 0xff),
        @intCast((next >> 16) & 0xff),
        @intCast((next >> 8) & 0xff),
        @intCast(next & 0xff),
    };
    if (!isMulticastIpv4(out)) return null;
    return out;
}

/// Validates the JSON object and resolves defaults. Fail-closed: every value
/// used by the transport is checked here before a socket or session exists.
///
/// channels is the renderer output width (outputLayout channel count).
pub fn prepare(file: File, channels: usize) !Prepared {
    if (channels == 0 or channels > max_channels) return error.Aes67ChannelCount;
    if (file.framesPerPacket != frames_per_packet) return error.UnsupportedAes67PacketFrames;
    if (file.ptpDomain > 127) return error.InvalidAes67PtpDomain;
    if (file.ringPackets < 8 or file.ringPackets > 1024) return error.InvalidAes67RingPackets;
    if (file.prebufferFrames < frames_per_packet or file.prebufferFrames > 48000 or
        file.prebufferFrames > @as(u32, file.ringPackets) * frames_per_packet) return error.InvalidAes67Prebuffer;
    if (file.ttl == 0) return error.InvalidAes67Ttl;
    if (file.payloadType < 96 or file.payloadType > 127) return error.InvalidAes67PayloadType;
    // The single-flow mirror format is gone: top-level destIp/port were a
    // local transitional design and were never merged upstream, so there is no
    // compatibility obligation. Fail closed with a dedicated error so callers
    // can tell the user to regenerate the config through the UI; no silent
    // migration happens here.
    if (file.destIp != null or file.port != null) return error.Aes67LegacyMirrorFormat;
    // flows[] is mandatory for every layout and channel count: flow count is
    // ceil(channels / 8) with each flow bounded to 8 channels / 1440 B.
    const flows = file.flows orelse return error.Aes67RequiresFlows;

    var source: ?[4]u8 = null;
    if (file.srcInterfaceIp.len != 0) {
        var value: [4]u8 = undefined;
        if (!parseIpv4(file.srcInterfaceIp, &value)) return error.InvalidAes67SourceInterface;
        if (!isUnicastIpv4(value)) return error.InvalidAes67SourceInterface;
        source = value;
    }
    const gmid = try parseGmid(file.ptpGmid);
    // A legacy configured GMID cannot stand in for the module's lock result.
    // The module derives its SDP clock identity from the locked PTP source.
    if (gmid != null) return error.Aes67PtpGmidRequiresLock;

    const total: u16 = @intCast(channels);
    var prepared = Prepared{
        .dest_ip = .{ 0, 0, 0, 0 },
        .port = 0,
        .frames_per_packet = file.framesPerPacket,
        .payload_type = file.payloadType,
        .ssrc = if (file.ssrc == 0) default_ssrc else file.ssrc,
        .src_interface_ip = source,
        .ptp_gmid = gmid,
        .ptp_domain = file.ptpDomain,
        .ring_packets = file.ringPackets,
        .prebuffer_frames = file.prebufferFrames,
        .channels = total,
        .flow_count = 1,
        .flows = [_]PreparedFlow{std.mem.zeroes(PreparedFlow)} ** max_flows,
        .channel_map = [_]u16{0} ** max_channels,
    };
    for (0..channels) |index| prepared.channel_map[index] = @intCast(index);

    if (flows.len == 0 or flows.len > max_flows) return error.InvalidAes67FlowCount;
    var offset: u16 = 0;
    var count: usize = 0;
    for (flows, 0..) |flow, index| {
        if (flow.channels == 0 or flow.channels > max_flow_channels) return error.InvalidAes67FlowChannels;
        if (flow.channelOffset != offset) return error.InvalidAes67FlowOffset;
        if (flow.port == 0) return error.InvalidAes67Port;
        const flow_payload_type = flow.payloadType orelse file.payloadType;
        if (flow_payload_type < 96 or flow_payload_type > 127) return error.InvalidAes67PayloadType;
        const dest = try resolveDestination(flow.destIp);
        const payload = @as(usize, flow.channels) * frames_per_packet * 3;
        if (payload > max_payload_bytes) return error.Aes67PayloadTooLarge;
        const flow_ttl: u8 = if (flow.ttl == 0) file.ttl else flow.ttl;
        if (dest.multicast and flow_ttl == 0) return error.InvalidAes67Ttl;
        // Per-flow egress override: an explicit flow source wins over the
        // session-level srcInterfaceIp; both must be unicast IPv4.
        var flow_source: ?[4]u8 = source;
        if (flow.srcInterfaceIp.len != 0) {
            var value: [4]u8 = undefined;
            if (!parseIpv4(flow.srcInterfaceIp, &value)) return error.InvalidAes67SourceInterface;
            if (!isUnicastIpv4(value)) return error.InvalidAes67SourceInterface;
            flow_source = value;
        }
        for (prepared.flows[0..index]) |previous| {
            if (previous.port == flow.port and std.mem.eql(u8, &previous.dest_ip, &dest.address))
                return error.Aes67DuplicateFlowDestination;
            // AES67 6.1.3: each stream is assigned a unique destination
            // address and only a single device sends per multicast group.
            // Two flows on one group cannot be demuxed by a compliant
            // receiver, so the whole shape is rejected regardless of port.
            if (dest.multicast and previous.multicast and std.mem.eql(u8, &previous.dest_ip, &dest.address))
                return error.Aes67DuplicateFlowGroup;
        }
        const base = if (file.ssrc == 0) default_ssrc else file.ssrc;
        const ssrc = if (flow.ssrc != 0) flow.ssrc else base +% @as(u32, @intCast(index));
        for (prepared.flows[0..index]) |previous| {
            if (previous.ssrc == ssrc) return error.Aes67DuplicateFlowSsrc;
        }
        prepared.flows[index] = .{
            .dest_ip = dest.address,
            .dest_text = formatIpv4(dest.address),
            .port = flow.port,
            .payload_type = flow_payload_type,
            .ssrc = ssrc,
            .channels = flow.channels,
            .channel_offset = flow.channelOffset,
            .multicast = dest.multicast,
            .ttl = flow_ttl,
            .source_ip = flow_source,
        };
        offset += flow.channels;
        count += 1;
    }
    if (offset != total) return error.InvalidAes67FlowChannels;
    prepared.flow_count = @intCast(count);
    // Legacy aggregate mirror for single-flow readers: always flow 0's values.
    prepared.dest_ip = prepared.flows[0].dest_ip;
    prepared.port = prepared.flows[0].port;
    prepared.payload_type = prepared.flows[0].payload_type;
    prepared.ssrc = prepared.flows[0].ssrc;

    // Multicast egress is explicit per flow: the OS cannot guess between the
    // several NICs a desktop can have, so every multicast flow must resolve an
    // egress interface (its own srcInterfaceIp or the session-level one).
    for (prepared.flows[0..prepared.flow_count]) |flow| {
        if (flow.multicast and flow.source_ip == null) return error.Aes67MulticastRequiresSourceInterface;
    }
    prepared.multicast = prepared.flows[0].multicast;
    prepared.ttl = prepared.flows[0].ttl;
    prepared.source_filter = file.sourceFilter;

    if (file.channelMap) |map| {
        if (map.len != channels) return error.InvalidAes67ChannelMap;
        var seen: u16 = 0;
        for (map) |value| {
            if (value >= total) return error.InvalidAes67ChannelMap;
            const bit = @as(u16, 1) << @intCast(value);
            if (seen & bit != 0) return error.InvalidAes67ChannelMap;
            seen |= bit;
        }
        for (map, 0..) |value, index| prepared.channel_map[index] = value;
    }
    return prepared;
}

/// Renders the dotted IPv4 form used in reports and SDP.
pub fn formatIpv4(address: [4]u8) [15]u8 {
    var out: [15]u8 = [_]u8{0} ** 15;
    const text = std.fmt.bufPrint(&out, "{d}.{d}.{d}.{d}", .{ address[0], address[1], address[2], address[3] }) catch return out;
    _ = text;
    return out;
}
