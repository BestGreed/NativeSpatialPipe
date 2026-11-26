//! AES67 transport configuration. This module owns network/transport
//! validation only; channel count and channel order are derived by the caller
//! from outputLayout/routing, never from a fixed layout here.
//!
//! Standard shape (AES67-2023 sec 6.3 line 1332-1333): every stream uses RTP
//! port 5004; flows are distinguished by unique administrative-scope multicast
//! groups (sec 6.1.3 line 1258-1260). ST 2022-7 redundancy uses port 5006
//! (primary + 2). Legacy per-flow sender ports 5010/5012 must be migrated to
//! 5004 + unique groups; strict mode rejects divergent per-flow ports.
const std = @import("std");

pub const max_channels: u16 = 16;
pub const max_flows: u8 = 8;
/// AES67 media profile: senders offer streams of 1-8 channels (sec 7.3 line
/// 1556-1558). Larger flows are an explicit extension used only when the MTU
/// permits (flowMode = extension, an explicit product extension).
pub const standard_flow_channels: u16 = 8;
/// Explicit single-flow extension: one flow may carry up to 16 channels when
/// the 1440-byte AES67 payload budget permits it. Not the AES67 profile.
pub const single_flow_channels: u16 = 16;
pub const max_flow_channels: u16 = single_flow_channels;
/// AES67-2023 sec 6.3 line 1364-1366: maximum RTP payload is 1440 bytes.
pub const max_payload_bytes: usize = 1440;
pub const default_port: u16 = 5004;
pub const secondary_port_offset: u16 = 2;
pub const default_payload_type: u8 = 96;
/// RFC 3550 sec 5.1 line 857-862: SSRC should be random. Zero asks the module
/// to pick a random per-session base and keep flow k = base + k.
pub const default_ssrc: u32 = 0;
pub const default_ring_packets: u16 = 64;
pub const default_prebuffer_frames: u32 = 480;
pub const default_ptp_domain: u16 = 0;
pub const default_ptp_priority: u8 = 128;
/// AES67 Annex A Table A.1 announceReceiptTimeout default 3 x 2 s.
pub const default_ptp_announce_timeout_ms: u32 = 6_000;
pub const default_ttl: u8 = 32;
pub const default_sap_interval_ms: u32 = 30_000;
pub const auto_max_flows: u8 = 4;
/// aes67.name: optional device/session name advertised in SDP and receiver
/// device lists. Trimmed length must fit 63 bytes of printable ASCII.
pub const max_name_len: usize = 63;

pub const supported_sample_rates = [_]u32{ 44_100, 48_000, 88_200, 96_000, 176_400, 192_000 };
pub const supported_packet_times_us = [_]u16{ 125, 250, 333, 500, 1_000, 2_000, 4_000 };

/// Linear PCM encoding (AES67-2023 sec 7.1 line 1411-1414): L16 per RFC 3551
/// 4.5.11 and L24 per RFC 3190 clause 4.
pub const BitDepth = enum(u8) {
    L24 = 0,
    L16 = 1,

    pub fn bytes(self: BitDepth) u8 {
        return switch (self) {
            .L16 => 2,
            .L24 => 3,
        };
    }
    pub fn codec(self: BitDepth) []const u8 {
        return switch (self) {
            .L16 => "L16",
            .L24 => "L24",
        };
    }
};

/// Flow packing mode. standard keeps the AES67 1-8 channel profile; extension
/// packs each flow to the 1440-byte payload limit (a single 16/64-channel flow
/// is possible) and only splits when the payload does not fit. Extension flows
/// wider than 8 channels are a product EXTENSION, not AES67 conformance.
pub const FlowMode = enum(u8) { standard = 0, extension = 1 };
pub const PortPolicy = enum(u8) { strict = 0, explicit = 1 };

fn familyBase(rate: u32) ?u32 {
    if (rate % 44_100 == 0) return 44_100;
    if (rate % 48_000 == 0) return 48_000;
    return null;
}

/// Samples per packet from AES67-2023 Table 2 (lines 1486-1550): 6/12/16/48/
/// 192 samples at the family's 1FS rate; 24 (500 us) and 96 (2 ms) are
/// additional packet times permitted by sec 7.2.2 line 1484. The 1 ms row is
/// 48 samples at BOTH 48 kHz and 44.1 kHz (sec 7.2.1 line 1473-1476), so a
/// 44.1 kHz packet lasts 48/44100 s = 1.088 ms (ptime:1.09, 918.75 pps).
pub fn samplesPerPacket(rate: u32, packet_time_us: u16) ?u16 {
    if (std.mem.indexOfScalar(u32, &supported_sample_rates, rate) == null) return null;
    if (std.mem.indexOfScalar(u16, &supported_packet_times_us, packet_time_us) == null) return null;
    const base = familyBase(rate) orelse return null;
    const base_samples: u16 = switch (packet_time_us) {
        125 => 6,
        250 => 12,
        333 => 16,
        500 => 24,
        1_000 => 48,
        2_000 => 96,
        4_000 => 192,
        else => return null,
    };
    return base_samples * @as(u16, @intCast(rate / base));
}

pub fn maxChannelsPerFlow(samples: u16, bytes_per_sample: u8) u16 {
    return @intCast(max_payload_bytes / (@as(usize, samples) * bytes_per_sample));
}

/// 44.1 kHz family defaults to L16: AES67-2023 sec 7.1 line 1423-1424 requires
/// L16 support at 44.1 kHz, L24 is optional (line 1429). 48 kHz family
/// defaults to L24, the production encoding; L16 is selectable.
pub fn defaultBitDepth(rate: u32) BitDepth {
    return if (rate % 44_100 == 0) .L16 else .L24;
}

/// One RTP flow in a multi-flow AES67 session.
pub const Flow = struct {
    /// Empty is allowed only when the flow comes from auto/single derivation.
    destIp: []const u8 = "",
    /// UDP port. Strict policy defaults to 5004 and rejects any other value.
    port: u16 = default_port,
    channels: u16 = 8,
    channelOffset: u16 = 0,
    payloadType: ?u8 = null,
    ssrc: u32 = 0,
    ttl: u8 = 0,
    srcInterfaceIp: []const u8 = "",
};

pub const Timestamping = enum(u32) { auto, software, hardware };
/// PTP topology mode (DESIGN-TOPO.md section 2 / appended ABI ptp_mode):
/// slave follows an external GM (pre-existing behavior), auto listens then
/// falls back to gm when gmCapable else local, gm announces this sender,
/// local uses a local media clock.
pub const PtpMode = enum(u8) { slave = 0, auto = 1, gm = 2, local = 3 };
pub const Ptp = struct {
    domain: u16 = default_ptp_domain,
    lockTimeoutMs: u32 = 5000,
    timestamping: Timestamping = .auto,
    /// aes67.ptp.mode (frozen key, DESIGN audit D8). Default slave keeps the
    /// pre-existing wait-for-external-GM behavior.
    mode: PtpMode = .slave,
    priority1: u8 = default_ptp_priority,
    priority2: u8 = default_ptp_priority,
    /// aes67.ptp.announceTimeoutMs: AUTO listening window and foreign-master
    /// receipt timeout (1000..60000 ms).
    announceTimeoutMs: u32 = default_ptp_announce_timeout_ms,
    /// aes67.ptp.gmCapable: auto mode may announce a GM after the timeout.
    gmCapable: bool = false,
    /// aes67.ptp.gmUtcOffset: null = not configured (ARB, ptpTimescale=0);
    /// 0..127 = currentUtcOffset seconds (ptpTimescale=1, offsetValid=1).
    gmUtcOffset: ?u8 = null,

    pub fn validate(self: Ptp) !void {
        if (self.domain > 127) return error.InvalidAes67PtpDomain;
        if (self.lockTimeoutMs < 100 or self.lockTimeoutMs > 120000) return error.InvalidAes67PtpLockTimeout;
        if (self.announceTimeoutMs < 1000 or self.announceTimeoutMs > 60000)
            return error.InvalidAes67PtpAnnounceTimeout;
        if (self.gmUtcOffset) |offset| {
            if (offset > 127) return error.InvalidAes67PtpUtcOffset;
        }
    }
    /// ABI ptp_mode value; gm mode implies ptp_gm_capable.
    pub fn abiMode(self: Ptp) u8 {
        return @intFromEnum(self.mode);
    }
    pub fn abiGmCapable(self: Ptp) u8 {
        return @intFromBool(self.mode == .gm or self.gmCapable);
    }
    /// -1 sentinel = "not configured" (ARB) in the appended ABI field.
    pub fn abiUtcOffset(self: Ptp) i16 {
        return if (self.gmUtcOffset) |offset| @intCast(offset) else -1;
    }
};

/// Parsed playback.json aes67 object. Slices borrow the parsed document.
pub const File = struct {
    /// LEGACY single-flow destination of the removed mirror format: rejected.
    destIp: ?[]const u8 = null,
    /// LEGACY single-flow port, same treatment as destIp.
    port: ?u16 = null,
    /// LEGACY fixed frames-per-packet. When present it must agree with the
    /// (sample rate, packet time) sample count, otherwise it is rejected.
    framesPerPacket: ?u16 = null,
    /// Sampling rate; 48 kHz default (AES67 mandatory). 44.1/96 recommended,
    /// 88.2/176.4/192 are RAVENNA-family extensions.
    sampleRate: u32 = 48_000,
    /// Packet time in microseconds: 125/250/333/500/1000/2000/4000.
    ptimeUs: u16 = 1_000,
    /// null selects defaultBitDepth(rate): L16 for 44.1 kHz, L24 for 48 kHz.
    codec: ?BitDepth = null,
    /// standard (default) splits to at most 8 channels per flow (AES67 media
    /// profile); extension packs to the 1440-byte payload limit first.
    flowMode: FlowMode = .standard,
    /// strict enforces the standard shared 5004 port; explicit allows 5004/5006
    /// style per-flow ports for migration and lab use.
    portPolicy: PortPolicy = .strict,
    /// ST 2022-7 duplicate legs on primary port + 2 (5006 when primary 5004).
    redundant: bool = false,
    /// Session base port for derived flows; must be even and in 1024-65534.
    basePort: u16 = default_port,
    /// Base multicast group for auto/single derivation (e.g. 239.69.0.1);
    /// flow k uses base + k per AES67 sec 6.1.3.
    groupBase: []const u8 = "",
    /// SAP announcement group; empty selects RFC 2974 224.2.127.254.
    sapAddress: []const u8 = "",
    /// SAP repeat interval in milliseconds (1000-300000; daemon uses 30000).
    sapIntervalMs: u32 = default_sap_interval_ms,
    payloadType: u8 = default_payload_type,
    ssrc: u32 = default_ssrc,
    srcInterfaceIp: []const u8 = "",
    ptpGmid: []const u8 = "",
    ptpDomain: u16 = default_ptp_domain,
    ptp: ?Ptp = null,
    /// aes67.name: optional device/session name advertised in SDP and in
    /// receiver device lists. Empty = unset (session open derives
    /// NSP-<COMPUTERNAME>); otherwise trimmed length <= 63 printable ASCII.
    name: []const u8 = "",
    ringPackets: u16 = default_ring_packets,
    prebufferFrames: u32 = default_prebuffer_frames,
    ttl: u8 = default_ttl,
    /// Emit RFC 4570 source-filter for multicast flows. Default on: the daemon
    /// always emits it and SSM/IGMPv3 receivers need it (audit 2026-11-23 item 6).
    sourceFilter: bool = true,
    flows: ?[]const Flow = null,
    channelMap: ?[]const u16 = null,
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

pub fn parseIpv4Address(text: []const u8) ?[4]u8 {
    var out: [4]u8 = undefined;
    if (!parseIpv4(text, &out)) return null;
    return out;
}

pub fn formatIpv4(address: [4]u8) [15]u8 {
    var out: [15]u8 = [_]u8{0} ** 15;
    const text = std.fmt.bufPrint(&out, "{d}.{d}.{d}.{d}", .{ address[0], address[1], address[2], address[3] }) catch return out;
    _ = text;
    return out;
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
    if (address[0] == 0 or address[0] == 255) return false;
    if (address[0] >= 224) return false;
    return true;
}

pub fn isMulticastIpv4(address: [4]u8) bool {
    return address[0] >= 224 and address[0] <= 239;
}

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

/// Deterministic one-group-per-flow allocation (AES67 sec 6.1.3 line
/// 1258-1260: one sender per stream, unique destination address).
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

pub const PreparedFlow = struct {
    dest_ip: [4]u8,
    dest_text: [15]u8,
    port: u16,
    payload_type: u8,
    ssrc: u32,
    channels: u16,
    channel_offset: u16,
    multicast: bool = false,
    ttl: u8 = 0,
    source_ip: ?[4]u8 = null,
};

pub const Prepared = struct {
    dest_ip: [4]u8,
    dest_text: [15]u8,
    port: u16,
    frames_per_packet: u16,
    sample_rate: u32,
    packet_time_us: u16,
    bit_depth: BitDepth,
    bytes_per_sample: u8,
    payload_type: u8,
    ssrc: u32,
    src_interface_ip: ?[4]u8,
    ptp_gmid: ?[23]u8,
    ptp_domain: u16,
    ring_packets: u16,
    prebuffer_frames: u32,
    channels: u16,
    flow_count: u8,
    flows: [max_flows]PreparedFlow,
    channel_map: [max_channels]u16,
    multicast: bool = false,
    ttl: u8 = 0,
    source_filter: bool = false,
    redundant: bool = false,
    sap_interval_ms: u32 = default_sap_interval_ms,
    sap_address: [4]u8 = .{ 224, 2, 127, 254 },
    port_policy: PortPolicy = .strict,
    /// Resolved ABI flow mode: 0 = standard, 1 = extension.
    abi_flow_mode: u8 = 2,

    pub fn ptimeMilliseconds(self: Prepared) f64 {
        return @as(f64, @floatFromInt(self.frames_per_packet)) * 1000.0 / @as(f64, @floatFromInt(self.sample_rate));
    }
    pub fn packetsPerSecond(self: Prepared) f64 {
        return @as(f64, @floatFromInt(self.sample_rate)) / @as(f64, @floatFromInt(self.frames_per_packet));
    }
};
fn balancedCounts(channels: u16, flow_count: u8, counts: *[max_flows]u16) void {
    const n: u16 = flow_count;
    const quotient = channels / n;
    const remainder = channels % n;
    for (0..flow_count) |index| {
        counts[index] = quotient + @as(u16, @intFromBool(index < remainder));
    }
}

fn appendFlow(
    prepared: *Prepared,
    index: usize,
    dest: Destination,
    port: u16,
    channels: u16,
    offset: u16,
    payload_type: u8,
    ssrc: u32,
    ttl: u8,
    source: ?[4]u8,
) !void {
    for (prepared.flows[0..index]) |previous| {
        if (previous.port == port and std.mem.eql(u8, &previous.dest_ip, &dest.address))
            return error.Aes67DuplicateFlowDestination;
        if (dest.multicast and previous.multicast and std.mem.eql(u8, &previous.dest_ip, &dest.address))
            return error.Aes67DuplicateFlowGroup;
        // SSRC 0 means "module picks a random per-session base + index"; it is
        // not a real identifier yet, so it cannot conflict.
        if (ssrc != 0 and previous.ssrc == ssrc) return error.Aes67DuplicateFlowSsrc;
    }
    prepared.flows[index] = .{
        .dest_ip = dest.address,
        .dest_text = formatIpv4(dest.address),
        .port = port,
        .payload_type = payload_type,
        .ssrc = ssrc,
        .channels = channels,
        .channel_offset = offset,
        .multicast = dest.multicast,
        .ttl = ttl,
        .source_ip = source,
    };
}

/// aes67.name is optional; an all-space value is accepted and treated as
/// unset by the session opener. Everything else must be printable ASCII and
/// fit 63 bytes after trimming.
fn validateName(name: []const u8) !void {
    if (name.len == 0) return;
    const trimmed = std.mem.trim(u8, name, " \t\r\n");
    if (trimmed.len > max_name_len) return error.InvalidAes67Name;
    for (name) |ch| {
        if (ch < 0x20 or ch > 0x7e) return error.InvalidAes67Name;
    }
}

/// Validates the JSON object and resolves defaults. Fail-closed: every value
/// used by the transport is checked here before a socket or session exists.
///
/// channels is the renderer output width (outputLayout channel count).
pub fn prepare(file: File, channels: usize) !Prepared {
    if (channels == 0 or channels > max_channels) return error.Aes67ChannelCount;
    try validateName(file.name);
    if (std.mem.indexOfScalar(u32, &supported_sample_rates, file.sampleRate) == null)
        return error.InvalidAes67SampleRate;
    if (std.mem.indexOfScalar(u16, &supported_packet_times_us, file.ptimeUs) == null)
        return error.InvalidAes67Ptime;
    const samples = samplesPerPacket(file.sampleRate, file.ptimeUs) orelse return error.InvalidAes67Ptime;
    const bit_depth = file.codec orelse defaultBitDepth(file.sampleRate);
    const bytes_per_sample = bit_depth.bytes();
    if (file.framesPerPacket) |frames_per_packet| {
        if (frames_per_packet != samples) return error.UnsupportedAes67PacketFrames;
    }
    const fit = maxChannelsPerFlow(samples, bytes_per_sample);
    const standard_cap = @min(standard_flow_channels, fit);
    const extension_cap = @min(max_flow_channels, fit);
    // standard keeps the AES67 1-8 channel profile; extension fills each flow
    // to the 1440-byte payload limit and only splits when it does not fit.
    const flow_cap = if (file.flowMode == .extension) extension_cap else standard_cap;
    if (flow_cap == 0) return error.Aes67PayloadTooLarge;

    if (file.basePort == 0 or file.basePort < 1024 or file.basePort % 2 != 0)
        return error.InvalidAes67Port;
    if (file.redundant and (@as(u32, file.basePort) + secondary_port_offset > 65535))
        return error.InvalidAes67Port;
    if (file.ptpDomain > 127) return error.InvalidAes67PtpDomain;
    if (file.ringPackets < 8 or file.ringPackets > 1024) return error.InvalidAes67RingPackets;
    const capacity_frames = @as(u32, file.ringPackets) * file.sampleRate / 1000;
    if (file.prebufferFrames < samples or file.prebufferFrames > 48000 or
        file.prebufferFrames > capacity_frames) return error.InvalidAes67Prebuffer;
    if (file.ttl == 0) return error.InvalidAes67Ttl;
    if (file.payloadType < 96 or file.payloadType > 127) return error.InvalidAes67PayloadType;
    if (file.sapIntervalMs < 1000 or file.sapIntervalMs > 300000) return error.InvalidAes67SapInterval;

    var sap_address: [4]u8 = .{ 224, 2, 127, 254 };
    if (file.sapAddress.len != 0) {
        var value: [4]u8 = undefined;
        if (!parseIpv4(file.sapAddress, &value) or !isMulticastIpv4(value))
            return error.InvalidAes67SapAddress;
        sap_address = value;
    }
    var base_group: ?Destination = null;
    if (file.groupBase.len != 0) base_group = try resolveDestination(file.groupBase);

    if (file.destIp != null or file.port != null) return error.Aes67LegacyMirrorFormat;

    var source: ?[4]u8 = null;
    if (file.srcInterfaceIp.len != 0) {
        var value: [4]u8 = undefined;
        if (!parseIpv4(file.srcInterfaceIp, &value)) return error.InvalidAes67SourceInterface;
        if (!isUnicastIpv4(value)) return error.InvalidAes67SourceInterface;
        source = value;
    }
    const gmid = try parseGmid(file.ptpGmid);
    if (gmid != null) return error.Aes67PtpGmidRequiresLock;

    const total: u16 = @intCast(channels);
    var prepared = Prepared{
        .dest_ip = .{ 0, 0, 0, 0 },
        .dest_text = [_]u8{0} ** 15,
        .port = file.basePort,
        .frames_per_packet = samples,
        .sample_rate = file.sampleRate,
        .packet_time_us = file.ptimeUs,
        .bit_depth = bit_depth,
        .bytes_per_sample = bytes_per_sample,
        .payload_type = file.payloadType,
        .ssrc = file.ssrc,
        .src_interface_ip = source,
        .ptp_gmid = gmid,
        .ptp_domain = file.ptpDomain,
        .ring_packets = file.ringPackets,
        .prebuffer_frames = file.prebufferFrames,
        .channels = total,
        .flow_count = 1,
        .flows = [_]PreparedFlow{std.mem.zeroes(PreparedFlow)} ** max_flows,
        .channel_map = [_]u16{0} ** max_channels,
        .source_filter = file.sourceFilter,
        .redundant = file.redundant,
        .sap_interval_ms = file.sapIntervalMs,
        .sap_address = sap_address,
        .port_policy = file.portPolicy,
    };
    for (0..channels) |index| prepared.channel_map[index] = @intCast(index);

    var offset: u16 = 0;
    var count: usize = 0;
    if (file.flows) |flows| {
        if (flows.len == 0 or flows.len > max_flows) return error.InvalidAes67FlowCount;
        for (flows, 0..) |flow, index| {
            if (flow.channels == 0 or flow.channels > flow_cap) return error.InvalidAes67FlowChannels;
            if (flow.channels > total -| offset) return error.InvalidAes67FlowChannels;
            if (flow.channelOffset != offset) return error.InvalidAes67FlowOffset;
            const dest = try resolveDestination(flow.destIp);
            const port = if (flow.port == 0) file.basePort else flow.port;
            if (port < 1024 or port % 2 != 0) return error.InvalidAes67Port;
            if (file.portPolicy == .strict and port != file.basePort) return error.InvalidAes67Port;
            if (@as(usize, flow.channels) * samples * bytes_per_sample > max_payload_bytes)
                return error.Aes67PayloadTooLarge;
            const flow_payload_type = flow.payloadType orelse file.payloadType;
            if (flow_payload_type < 96 or flow_payload_type > 127) return error.InvalidAes67PayloadType;
            const flow_ttl: u8 = if (flow.ttl == 0) file.ttl else flow.ttl;
            if (dest.multicast and flow_ttl == 0) return error.InvalidAes67Ttl;
            var flow_source: ?[4]u8 = source;
            if (flow.srcInterfaceIp.len != 0) {
                var value: [4]u8 = undefined;
                if (!parseIpv4(flow.srcInterfaceIp, &value)) return error.InvalidAes67SourceInterface;
                if (!isUnicastIpv4(value)) return error.InvalidAes67SourceInterface;
                flow_source = value;
            }
            const ssrc = if (flow.ssrc != 0) flow.ssrc else if (file.ssrc == 0) 0 else file.ssrc +% @as(u32, @intCast(index));
            try appendFlow(&prepared, count, dest, port, flow.channels, flow.channelOffset, flow_payload_type, ssrc, flow_ttl, flow_source);
            offset += flow.channels;
            count += 1;
        }
    } else {
        // Both modes derive a plan when flows are omitted; explicit rows are
        // optional in either mode.
        var counts = [_]u16{0} ** max_flows;
        var flow_count: u8 = 1;
        if (total <= flow_cap) {
            counts[0] = total;
        } else {
            const target = flow_cap;
            flow_count = @intCast((@as(u32, total) + target - 1) / target);
            if (flow_count > max_flows) return error.Aes67PayloadTooLarge;
            balancedCounts(total, flow_count, &counts);
        }
        const base = base_group orelse return error.Aes67BaseGroupRequired;
        if (flow_count > 1 and !base.multicast) return error.Aes67UnicastRequiresExplicitFlows;
        var index: usize = 0;
        var derived_offset: u16 = 0;
        while (index < flow_count) : (index += 1) {
            const dest_address = if (base.multicast)
                allocateFlowGroup(base.address, @intCast(index)) orelse return error.InvalidAes67Destination
            else
                base.address;
            const ssrc = if (file.ssrc == 0) 0 else file.ssrc +% @as(u32, @intCast(index));
            try appendFlow(&prepared, index, .{ .address = dest_address, .multicast = base.multicast }, file.basePort, counts[index], derived_offset, file.payloadType, ssrc, file.ttl, source);
            derived_offset += counts[index];
        }
        count = flow_count;
        offset = total;
    }
    if (offset != total or count == 0) return error.InvalidAes67FlowChannels;
    prepared.flow_count = @intCast(count);
    prepared.abi_flow_mode = @intFromEnum(file.flowMode);
    prepared.dest_ip = prepared.flows[0].dest_ip;
    prepared.dest_text = prepared.flows[0].dest_text;
    prepared.port = prepared.flows[0].port;
    prepared.payload_type = prepared.flows[0].payload_type;
    prepared.ssrc = prepared.flows[0].ssrc;
    for (prepared.flows[0..prepared.flow_count]) |flow| {
        if (flow.multicast and flow.source_ip == null) return error.Aes67MulticastRequiresSourceInterface;
    }
    prepared.multicast = prepared.flows[0].multicast;
    prepared.ttl = prepared.flows[0].ttl;

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