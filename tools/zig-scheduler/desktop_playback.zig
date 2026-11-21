//! Shared immutable per-session playback settings; also used by FSA/CLI clients.
const std = @import("std");
const processing = @import("output_processing.zig");
const layout = @import("output_layout.zig");
pub const Limiter = struct { enabled: bool = false, ceiling: f64 = 1, releaseMs: f64 = 50, lookaheadFrames: u32 = 0 };
pub const OutputProcessing = struct {
    bypass: bool = false,
    // Always renderer-standard order, before device routing; source is 48 kHz.
    trim: ?[]const f64 = null,
    delayFrames: ?[]const u32 = null,
    limiter: Limiter = .{},
};
pub const PositionMapping = struct { mode: []const u8 = "scaled", reference: f64 = 1 };
pub const Routing = struct { sourceChannelIndices: ?[]const u32 = null, physicalChannels: ?[]const u32 = null };
pub const RoarCustomLayout=struct{version:u32=1,measurementFile:?[]const u8=null};
pub const Recording=struct{pcm:bool=false,telemetrySnapshots:bool=false};
/// AES67/RTP network sink parameters. Only read when outputBackend is "aes67".
/// The sender is 48 kHz L24 only; sampleRate is fixed by the sink contract.
pub const Network = struct {
    destIp: []const u8 = "239.69.1.1",
    port: u16 = 5004,
    framesPerPacket: u16 = 48,
    ttl: u8 = 32,
    payloadType: u8 = 96,
    ssrc: u32 = 0,
    srcInterfaceIp: []const u8 = "",
    ptpGmid: []const u8 = "",
    ptpDomain: u16 = 0,
    ringPackets: u16 = 64,
    prebufferFrames: u32 = 480,
    /// Optional absolute path to nsp_aes67.dll; empty = <root>/build/aes67-output/nsp_aes67.dll.
    dll: []const u8 = "",
    pub fn validate(self: Network, channels: usize) !void {
        if (channels == 0 or channels > 16) return error.Aes67ChannelCount;
        if (self.port == 0) return error.InvalidNetworkPort;
        if (self.framesPerPacket != 48 and self.framesPerPacket != 96 and self.framesPerPacket != 192 and self.framesPerPacket != 480) return error.InvalidFramesPerPacket;
        if (self.ttl == 0) return error.InvalidNetworkTtl;
        if (self.payloadType > 127) return error.InvalidPayloadType;
        if (self.ptpDomain > 127) return error.InvalidPtpDomain;
        if (self.ringPackets < 1 or self.ringPackets > 4096) return error.InvalidRingPackets;
        if (self.prebufferFrames > 48000) return error.InvalidNetworkPrebuffer;
        const address = std.net.Address.parseIp4(self.destIp, self.port) catch return error.InvalidDestinationIp;
        _ = address;
        // 0.0.0.0 and 255.255.255.255 are never valid media destinations.
        if (std.mem.eql(u8, self.destIp, "0.0.0.0") or std.mem.eql(u8, self.destIp, "255.255.255.255")) return error.InvalidDestinationIp;
        if (self.srcInterfaceIp.len != 0) {
            _ = std.net.Address.parseIp4(self.srcInterfaceIp, 0) catch return error.InvalidSourceInterfaceIp;
            if (std.mem.eql(u8, self.srcInterfaceIp, "0.0.0.0")) return error.InvalidSourceInterfaceIp;
        }
        try validateGmid(self.ptpGmid);
        if (self.dll.len != 0) {
            if (!std.fs.path.isAbsolute(self.dll) or self.dll.len > 1024 or std.mem.indexOfScalar(u8, self.dll, 0) != null) return error.InvalidDesktopPlaybackPath;
        }
    }
    fn validateGmid(value: []const u8) !void {
        if (value.len == 0) return;
        // IEEE 1588 grandmaster id: 8 hyphen-separated hex octets (XX-XX-...-XX).
        if (value.len != 23) return error.InvalidPtpGmid;
        for (value, 0..) |ch, i| {
            if ((i % 3) == 2) {
                if (ch != '-') return error.InvalidPtpGmid;
            } else if (!std.ascii.isHex(ch)) return error.InvalidPtpGmid;
        }
    }
};
// Backend identity and backend-specific mode are separate. Legacy `olr` is
// accepted only as shorthand for ROAR's OLR mode, never as another backend.
pub const Selection = struct { backend: []const u8, mode: ?[]const u8 };
pub fn select(renderer: []const u8, mode: ?[]const u8) !Selection {
    if (std.mem.eql(u8,renderer,"roar") or std.mem.eql(u8,renderer,"olr")) {
        if (mode) |value| if (!std.mem.eql(u8,value,"olr")) return error.InvalidRendererMode;
        return .{.backend="roar",.mode="olr"};
    }
    if (std.mem.eql(u8,renderer,"lira")) {
        if (mode != null) return error.InvalidRendererMode;
        return .{.backend="lira",.mode=null};
    }
    return error.InvalidDesktopPlayback;
}
pub const Config = struct {
    recording:Recording=.{},
    /// Output device boundary. "asio" keeps the original device path; "aes67"
    /// replaces it with the RTP/L24 network sink described by `network`.
    outputBackend: []const u8 = "asio",
    network: ?Network = null,
    outputLayout: []const u8 = "7.1.4",
    roarCustomLayout: ?RoarCustomLayout = null,
    renderer: []const u8 = "roar",
    mode: ?[]const u8 = null,
    bridgeDll: []const u8,
    rendererDll: []const u8 = "",
    /// Device module and selector. Optional at parse time so the AES67 backend
    /// can omit them; validate() still requires both for outputBackend="asio".
    asioDll: []const u8 = "",
    driver: []const u8 = "",
    sourceStream: u32 = 0,
    channelOrder: []const u8 = "olr",
    sampleRate: f64 = 48000,
    bufferFrames: u32 = 0,
    master: f64 = 1,
    outputProcessing: ?OutputProcessing = null,
    positionMapping: ?PositionMapping = null,
    routing: ?Routing = null,
    clockSync: bool = true,
    prebufferFrames: u32 = 0,
    // Deprecated compatibility fields: no-input output is always resident.
    startupTimeoutMs: u32 = 0,
    rebufferTimeoutMs: u32 = 0,
    maxRebuffers: u32 = 0,
    drainTimeoutMs: u32 = 5000,
    pub fn preset(self:Config) !layout.presets.Preset {
        if(std.mem.eql(u8,self.outputLayout,"roar-custom-6dof"))return error.ReservedCustomLayoutNotReady;
        return layout.presets.get(self.outputLayout) orelse error.UnsupportedOutputLayout;
    }
    pub fn channelCount(self:Config) usize {return (self.preset() catch unreachable).labels.len;}
    pub fn processingConfig(self: Config) processing.Config {
        const supplied=self.outputProcessing orelse OutputProcessing{};
        var result=processing.Config{.channels=@intCast(self.channelCount()),.bypass=supplied.bypass,.master=self.master,.limiter=supplied.limiter.enabled,.ceiling=supplied.limiter.ceiling,
            .release_ms=supplied.limiter.releaseMs,.lookahead_frames=supplied.limiter.lookaheadFrames};
        if(supplied.trim) |values| @memcpy(result.trim[0..self.channelCount()],values);
        if(supplied.delayFrames) |values| @memcpy(result.delay_frames[0..self.channelCount()],values);
        return result;
    }
    pub fn sources(self: Config) ![]const u32 {
        const count=(try self.preset()).labels.len;
        if(std.mem.eql(u8,self.channelOrder,"custom")) {
            const route=self.routing orelse return error.CustomRoutingRequired;
            const indices=route.sourceChannelIndices orelse return error.CustomRoutingRequired;
            if(indices.len!=count)return error.InvalidSourceChannelCount;
            for(indices) |index| if(index>=count) return error.InvalidSourceChannel;
            return indices;
        }
        if(self.routing) |route| if(route.sourceChannelIndices!=null) return error.CustomRoutingNeedsCustomOrder;
        if(std.mem.eql(u8,self.channelOrder,"olr"))return layout.identity[0..count];
        if(std.mem.eql(u8,self.channelOrder,"dvs-speakers")){
            if(!std.mem.eql(u8,self.outputLayout,"7.1.4"))return error.DvsOrderRequires714;
            return &layout.dvs_sources;
        }
        return error.UnknownOutputOrder;
    }
    pub fn physical(self: Config) ![]const u32 {
        const count=(try self.preset()).labels.len;
        const indices=if(self.routing) |route| route.physicalChannels orelse layout.identity[0..count] else layout.identity[0..count];
        if(indices.len!=count)return error.InvalidPhysicalChannelCount;
        for(indices,0..) |index,i| {
            if(index>255) return error.InvalidPhysicalChannel;
            for(indices[0..i]) |previous| if(previous==index)return error.DuplicatePhysicalChannel;
        }
        return indices;
    }
    pub fn labels(self: Config) ![16][]const u8 {
        var result:[16][]const u8=[_] []const u8{""} ** 16;
        const definition=try self.preset();
        for(try self.sources(),0..) |source,i| result[i]=definition.labels[source];
        return result;
    }
    /// Apply existing controls only; no driver operation or scheduling here.
    pub fn applyOutput(self: Config, target: anytype) !void {
        const source=try self.sources();const physical_channels=try self.physical();
        target.source_channels=@intCast(self.channelCount());target.output_channels=@intCast(self.channelCount());
        for(0..self.channelCount()) |i| {target.sources[i]=source[i];target.physical[i]=@intCast(physical_channels[i]);}
        target.sample_rate=self.sampleRate;target.buffer_frames=@intCast(self.bufferFrames);
        target.processing=self.processingConfig();target.clock_sync=self.clockSync;
        target.prebuffer_frames=self.prebufferFrames;target.startup_timeout_ms=0;
        target.rebuffer_timeout_ms=0;target.max_rebuffers=0;target.drain_timeout_ms=self.drainTimeoutMs;
    }
    pub fn validate(self: Config) !void {
        const aes67_backend = std.mem.eql(u8,self.outputBackend,"aes67");
        if (!aes67_backend and !std.mem.eql(u8,self.outputBackend,"asio")) return error.InvalidOutputBackend;
        if (aes67_backend) {
            try (self.network orelse return error.NetworkConfigRequired).validate((try self.preset()).labels.len);
        } else if (self.network != null) return error.NetworkConfigWithAsioBackend;
        _ = try select(self.renderer,self.mode);
        const count=(try self.preset()).labels.len;
        if(self.roarCustomLayout) |custom| {
            if(std.mem.eql(u8,self.renderer,"lira"))return error.RoarOptionsWithLiraBackend;
            if(custom.version!=1)return error.InvalidCustomLayoutSlot;
            if(custom.measurementFile) |path| try absolute(path);
        }
        if(self.outputProcessing) |dsp| {
            if(dsp.trim) |v| if(v.len!=count)return error.InvalidCalibrationChannelCount;
            if(dsp.delayFrames) |v| if(v.len!=count)return error.InvalidCalibrationChannelCount;
        }
        if (self.sourceStream > 3 or self.bufferFrames > 65536 or !std.math.isFinite(self.sampleRate) or self.sampleRate < 8000 or self.sampleRate > 384000 or
            !std.math.isFinite(self.master) or self.master < 0 or self.master > 16 or
            self.prebufferFrames > 48000 or self.drainTimeoutMs==0 or
            (!std.mem.eql(u8,self.channelOrder,"olr") and !std.mem.eql(u8,self.channelOrder,"dvs-speakers") and !std.mem.eql(u8,self.channelOrder,"custom")) or
            !deviceSelector(self.driver,aes67_backend)) return error.InvalidDesktopPlayback;
        try absolute(self.bridgeDll);
        // The ASIO module path is ignored by the AES67 backend (mutually exclusive).
        if (!aes67_backend) try absolute(self.asioDll);
        if (std.mem.eql(u8,self.renderer,"lira")) try absolute(self.rendererDll);
        _=try self.sources();_=try self.physical();
        try self.processingConfig().validate();
        if(self.positionMapping) |mapping| {
            if(std.mem.eql(u8,self.renderer,"lira")) return error.OlrOptionsWithLiraBackend;
            if((!std.mem.eql(u8,mapping.mode,"scaled") and !std.mem.eql(u8,mapping.mode,"direction")) or
                !std.math.isFinite(mapping.reference) or mapping.reference<=0 or
                (std.mem.eql(u8,mapping.mode,"direction") and mapping.reference!=1)) return error.InvalidPositionMapping;
        }
    }
    fn absolute(path: []const u8) !void {
        if (!std.fs.path.isAbsolute(path) or path.len > 1024 or std.mem.indexOfScalar(u8,path,0) != null) return error.InvalidDesktopPlaybackPath;
    }
    /// ASIO requires an `asio:` driver selector; the AES67 sink accepts an empty
    /// or legacy selector and never opens a device.
    fn deviceSelector(driver: []const u8, aes67_backend: bool) bool {
        if (driver.len > 4095 or std.mem.indexOfScalar(u8,driver,0) != null) return false;
        if (aes67_backend) return true;
        return std.mem.startsWith(u8,driver,"asio:");
    }
};

/// Windows editors may write a UTF-8 BOM; it is not part of the JSON document.
/// The same typed validation is used before endpoint/session side effects.
pub fn parse(allocator: std.mem.Allocator, bytes: []const u8) !std.json.Parsed(Config) {
    const json=if(std.mem.startsWith(u8,bytes,"\xef\xbb\xbf")) bytes[3..] else bytes;
    var result=try std.json.parseFromSlice(Config,allocator,json,.{.allocate=.alloc_always});
    errdefer result.deinit();
    try result.value.validate();
    return result;
}