//! Shared immutable per-session playback settings; also used by FSA/CLI clients.
const std = @import("std");
const processing = @import("output_processing.zig");
const layout = @import("output_layout.zig");
pub const Limiter = struct { enabled: bool = false, ceiling: f64 = 1, releaseMs: f64 = 50, lookaheadFrames: u32 = 0 };
pub const OutputProcessing = struct {
    bypass: bool = false,
    // Always renderer-standard order, before device routing; source is 48 kHz.
    trim: [12]f64 = [_]f64{1} ** 12,
    delayFrames: [12]u32 = [_]u32{0} ** 12,
    limiter: Limiter = .{},
};
pub const PositionMapping = struct { mode: []const u8 = "scaled", reference: f64 = 1 };
pub const Routing = struct { sourceChannelIndices: ?[12]u32 = null, physicalChannels: ?[12]u32 = null };
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
    renderer: []const u8 = "roar",
    mode: ?[]const u8 = null,
    bridgeDll: []const u8,
    rendererDll: []const u8 = "",
    asioDll: []const u8,
    driver: []const u8,
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
    startupTimeoutMs: u32 = 5000,
    rebufferTimeoutMs: u32 = 1000,
    maxRebuffers: u32 = 3,
    drainTimeoutMs: u32 = 5000,
    pub fn processingConfig(self: Config) processing.Config {
        const supplied=self.outputProcessing orelse OutputProcessing{};
        var result=processing.Config{.bypass=supplied.bypass,.master=self.master,.limiter=supplied.limiter.enabled,.ceiling=supplied.limiter.ceiling,
            .release_ms=supplied.limiter.releaseMs,.lookahead_frames=supplied.limiter.lookaheadFrames};
        @memcpy(result.trim[0..12],&supplied.trim);
        @memcpy(result.delay_frames[0..12],&supplied.delayFrames);
        return result;
    }
    pub fn sources(self: Config) ![12]u32 {
        if(std.mem.eql(u8,self.channelOrder,"custom")) {
            const route=self.routing orelse return error.CustomRoutingRequired;
            const indices=route.sourceChannelIndices orelse return error.CustomRoutingRequired;
            for(indices) |index| if(index>=12) return error.InvalidSourceChannel;
            return indices;
        }
        if(self.routing) |route| if(route.sourceChannelIndices!=null) return error.CustomRoutingNeedsCustomOrder;
        return layout.sources(self.channelOrder);
    }
    pub fn physical(self: Config) ![12]u32 {
        const indices=if(self.routing) |route| route.physicalChannels orelse identity() else identity();
        for(indices,0..) |index,i| {
            if(index>255) return error.InvalidPhysicalChannel;
            for(indices[0..i]) |previous| if(previous==index) return error.DuplicatePhysicalChannel;
        }
        return indices;
    }
    fn identity() [12]u32 {return .{0,1,2,3,4,5,6,7,8,9,10,11};}
    pub fn labels(self: Config) ![12][]const u8 {
        var result:[12][]const u8=undefined;
        for(try self.sources(),0..) |source,i| result[i]=layout.olr_labels[source];
        return result;
    }
    /// Apply existing controls only; no driver operation or scheduling here.
    pub fn applyOutput(self: Config, target: anytype) !void {
        const source=try self.sources();const physical_channels=try self.physical();
        for(0..12) |i| {target.sources[i]=source[i];target.physical[i]=@intCast(physical_channels[i]);}
        target.sample_rate=self.sampleRate;target.buffer_frames=@intCast(self.bufferFrames);
        target.processing=self.processingConfig();target.clock_sync=self.clockSync;
        target.prebuffer_frames=self.prebufferFrames;target.startup_timeout_ms=self.startupTimeoutMs;
        target.rebuffer_timeout_ms=self.rebufferTimeoutMs;target.max_rebuffers=self.maxRebuffers;target.drain_timeout_ms=self.drainTimeoutMs;
    }
    pub fn validate(self: Config) !void {
        _ = try select(self.renderer,self.mode);
        if (self.sourceStream > 3 or self.bufferFrames > 65536 or !std.math.isFinite(self.sampleRate) or self.sampleRate < 8000 or self.sampleRate > 384000 or
            !std.math.isFinite(self.master) or self.master < 0 or self.master > 16 or
            self.prebufferFrames > 48000 or self.startupTimeoutMs==0 or self.rebufferTimeoutMs==0 or self.drainTimeoutMs==0 or
            (!std.mem.eql(u8,self.channelOrder,"olr") and !std.mem.eql(u8,self.channelOrder,"dvs-speakers") and !std.mem.eql(u8,self.channelOrder,"custom")) or
            !std.mem.startsWith(u8,self.driver,"asio:") or self.driver.len > 4095 or std.mem.indexOfScalar(u8,self.driver,0) != null) return error.InvalidDesktopPlayback;
        for ([_][]const u8{self.bridgeDll,self.asioDll}) |path| try absolute(path);
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
