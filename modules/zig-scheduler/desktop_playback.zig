//! Shared immutable per-session playback settings; also used by FSA/CLI clients.
const std = @import("std");
const processing = @import("output_processing.zig");
const layout = @import("output_layout.zig");
pub const Limiter = struct { enabled: bool = false, ceiling: f64 = 1, releaseMs: f64 = 50, lookaheadFrames: u32 = 0 };
pub const LoudnessLevel = extern struct { splDb: f64, lowDb: f64, highDb: f64 };
pub const Loudness = struct {
    referenceSplDb: f64,
    levels: []const LoudnessLevel,

    fn validate(self: Loudness) !void {
        if (!std.math.isFinite(self.referenceSplDb) or self.referenceSplDb < 20 or self.referenceSplDb > 100 or
            self.levels.len < 2 or self.levels.len > 16) return error.InvalidLoudnessProfile;
        var prior: f64 = 19;
        for (self.levels) |level| {
            if (!std.math.isFinite(level.splDb) or level.splDb <= prior or level.splDb < 20 or
                !std.math.isFinite(level.lowDb) or level.lowDb < 0 or level.lowDb > 4 or
                !std.math.isFinite(level.highDb) or level.highDb < 0 or level.highDb > 1)
                return error.InvalidLoudnessProfile;
            prior = level.splDb;
        }
        const reference = self.levels[self.levels.len - 1];
        if (reference.splDb != self.referenceSplDb or reference.lowDb != 0 or reference.highDb != 0)
            return error.InvalidLoudnessProfile;
    }
};
pub const OutputProcessing = struct {
    bypass: bool = false,
    // Always renderer-standard order, before device routing; source is 48 kHz.
    trim: ?[]const f64 = null,
    delayFrames: ?[]const u32 = null,
    limiter: Limiter = .{},
    loudness: ?Loudness = null,
};
pub const PositionMapping = struct { mode: []const u8 = "scaled", reference: f64 = 1 };
pub const Routing = struct { sourceChannelIndices: ?[]const u32 = null, physicalChannels: ?[]const u32 = null };
pub const RoarCustomLayout=struct{version:u32=1,measurementFile:?[]const u8=null};
pub const Recording=struct{pcm:bool=false,telemetrySnapshots:bool=false,rawStreamStats:bool=false};
pub const Wasapi=struct{dllPath:[]const u8,endpointId:[]const u8,mode:[]const u8="shared",periodFrames:u32=480};
pub const BrowserIdentity=struct{enabled:bool=false,nodeExe:[]const u8="",agentScript:[]const u8="",backendExe:[]const u8="",identityFile:[]const u8="",devtoolsUrl:[]const u8="http://127.0.0.1:9229",devtoolsActivePortFile:?[]const u8=null,metadataPort:u16=8784,extensionId:[]const u8="",leaseMs:u32=500,sourceTabId:u32=0};
pub const PcmCapture=struct{enabled:bool=false,captureDll:[]const u8="",ingressDll:[]const u8="",inputEndpointId:[]const u8="",sourcePid:u32=0,routeToOutput:bool=false,browserIdentity:?BrowserIdentity=null};
pub const MediaFoundation = struct { enabled: bool = false };
pub const Upmix = struct {
    enabled: bool = false,
    dllPath: ?[]const u8 = null,
    cpuFallbackDll: ?[]const u8 = null,
    modelPath: ?[]const u8 = null,
    freeSurroundDll: ?[]const u8 = null,
};
pub const Lsac = struct {
    enabled: bool = false,
    filterFile: ?[]const u8 = null,
    virtualBass: ?NotebookBass = null,
    loudness: ?Loudness = null,
    gain: ?f64 = null,
    limiter: ?Limiter = null,
};
pub const NotebookBass=struct{enabled:bool=false,amount:f64=0.18,highpassHz:f64=130};
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
pub const UnifiedSlotProfile = enum(u32) { standard = 0, dtsxHtCompact = 1 };
pub const UnifiedPrimaryPolicy = enum { legacy, objectCount };

pub const Config = struct {
    outputBackend:[]const u8="asio",
    sourcePolicy:[]const u8="fixed",
    unifiedSlotProfile: UnifiedSlotProfile = .standard,
    unifiedPrimaryPolicy: UnifiedPrimaryPolicy = .legacy,
    aoip:?@import("aoip_config.zig").File=null,
    aes67:?@import("aes67_config.zig").File=null,
    wasapi:?Wasapi=null,
    pcmCapture:?PcmCapture=null,
    mediaFoundation:?MediaFoundation=null,
    wasapiUpmix:?Upmix=null,
    lsac:?Lsac=null,
    browserIdentity:?BrowserIdentity=null,
    recording:Recording=.{},
    outputLayout: []const u8 = "7.1.4",
    roarCustomLayout: ?RoarCustomLayout = null,
    renderer: []const u8 = "roar",
    mode: ?[]const u8 = null,
    bridgeDll: []const u8,
    notebookFilterFile: ?[]const u8 = null,
    notebookVirtualBass: ?NotebookBass = null,
    rendererDll: []const u8 = "",
    asioDll: []const u8="",
    driver: []const u8="",
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
    pub fn browserBinding(self: Config) ?BrowserIdentity {
        return self.browserIdentity orelse if (self.pcmCapture) |capture| capture.browserIdentity else null;
    }
    pub fn lsacEnabled(self: Config) bool {
        return if (self.lsac) |adapter| adapter.enabled else self.notebookFilterFile != null;
    }
    pub fn filterFile(self: Config) ?[]const u8 {
        if (!self.lsacEnabled()) return null;
        return if (self.lsac) |adapter| adapter.filterFile else self.notebookFilterFile;
    }
    pub fn virtualBass(self: Config) ?NotebookBass {
        if (!self.lsacEnabled()) return null;
        return if (self.lsac) |adapter| adapter.virtualBass else self.notebookVirtualBass;
    }
    pub fn preset(self: Config) !layout.presets.Preset {
        if(std.mem.eql(u8,self.outputLayout,"roar-custom-6dof"))return error.ReservedCustomLayoutNotReady;
        // Legacy stereo profiles described the device output, not the renderer.
        if (self.lsac == null and self.notebookFilterFile != null and std.mem.eql(u8,self.outputLayout,"2.0"))
            return layout.presets.get("7.1.4").?;
        return layout.presets.get(self.outputLayout) orelse error.UnsupportedOutputLayout;
    }
    pub fn deviceLayout(self: Config) []const u8 {
        return if (self.lsacEnabled()) "2.0" else self.outputLayout;
    }
    pub fn channelCount(self: Config) usize {
        return if (self.lsacEnabled()) 2 else (self.preset() catch unreachable).labels.len;
    }
    pub fn processingConfig(self: Config) processing.Config {
        const supplied=self.outputProcessing orelse OutputProcessing{};
        const adapter = if (self.lsacEnabled()) self.lsac else null;
        const gain = if (adapter) |profile| profile.gain orelse self.master else self.master;
        const limiter = if (adapter) |profile| profile.limiter orelse supplied.limiter else supplied.limiter;
        var result=processing.Config{.channels=@intCast(self.channelCount()),.bypass=supplied.bypass,.master=gain,.limiter=limiter.enabled,.ceiling=limiter.ceiling,
            .release_ms=limiter.releaseMs,.lookahead_frames=limiter.lookaheadFrames};
        if(supplied.trim) |values| @memcpy(result.trim[0..self.channelCount()],values);
        if(supplied.delayFrames) |values| @memcpy(result.delay_frames[0..self.channelCount()],values);
        return result;
    }
    pub fn loudness(self: Config) ?Loudness {
        if (!self.lsacEnabled()) return null;
        return if (self.lsac) |adapter| adapter.loudness
            else if (self.outputProcessing) |output| output.loudness else null;
    }
    pub fn sources(self: Config) ![]const u32 {
        const count=self.channelCount();
        if(std.mem.eql(u8,self.outputBackend,"aoip"))return layout.identity[0..count];
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
        const count=self.channelCount();
        if(std.mem.eql(u8,self.outputBackend,"aoip"))return layout.identity[0..count];
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
        const stereo = [_][]const u8{"L","R"};
        const labels_for_device = if (self.lsacEnabled()) &stereo else definition.labels;
        for(try self.sources(),0..) |source,i| result[i]=labels_for_device[source];
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
    pub fn validateSpatialFormat(self: Config, active_format: ?[]const u8) !void {
        if (self.unifiedSlotProfile == .dtsxHtCompact and
            !std.mem.eql(u8, active_format orelse "", "DTSXForHomeTheater"))
            return error.CompactSlotsRequireActiveDtsxHt;
    }

    /// Saved Unified profiles follow the session endpoint. Resolve it before
    /// loading capture or output modules.
    pub fn resolvePcmInput(self: *Config, allocator: std.mem.Allocator, endpoint: []const u8) !void {
        if (std.mem.eql(u8, self.sourcePolicy, "unified")) {
            if (self.pcmCapture) |*capture| {
                if (capture.enabled) {
                    if (!validRenderEndpoint(endpoint)) return error.InvalidPcmInputEndpoint;
                    capture.inputEndpointId = try allocator.dupe(u8, endpoint);
                }
            }
        }
        try self.validateRuntime();
    }

    pub fn validateRuntime(self: Config) !void {
        try self.validate();
        if (self.pcmCapture) |capture| {
            if (capture.enabled) {
                if (!validRenderEndpoint(capture.inputEndpointId)) return error.InvalidPcmInputEndpoint;
                if (self.wasapi) |sink| {
                    if (std.ascii.eqlIgnoreCase(sink.endpointId, capture.inputEndpointId))
                        return error.PcmInputEqualsOutput;
                }
            }
        }
    }

    pub fn validate(self: Config) !void {
        const network=std.mem.eql(u8,self.outputBackend,"aoip");
        const aes67=std.mem.eql(u8,self.outputBackend,"aes67");
        const wasapi=std.mem.eql(u8,self.outputBackend,"wasapi");
        const unified = std.mem.eql(u8, self.sourcePolicy, "unified");
        if (!unified and (self.unifiedSlotProfile != .standard or self.unifiedPrimaryPolicy != .legacy))
            return error.UnifiedInputRequired;
        const core = try self.preset();
        const renderer = try select(self.renderer,self.mode);
        if (self.lsac != null and (self.notebookFilterFile != null or self.notebookVirtualBass != null or
            (self.outputProcessing != null and self.outputProcessing.?.loudness != null))) return error.ConflictingLsacConfig;
        if (self.lsacEnabled()) {
            if (!wasapi or core.code != 714) return error.LsacRequires714Wasapi;
            try absolute(self.filterFile() orelse return error.LsacFilterRequired);
            if (self.virtualBass()) |bass| {
                if(!std.math.isFinite(bass.amount) or bass.amount<0 or bass.amount>0.5 or !std.math.isFinite(bass.highpassHz) or (bass.highpassHz!=0 and (bass.highpassHz<80 or bass.highpassHz>180)))return error.InvalidVirtualBass;
            }
            if (self.loudness()) |profile| try profile.validate();
        } else if (self.notebookVirtualBass != null or (self.outputProcessing != null and self.outputProcessing.?.loudness != null)) {
            return error.LsacAdapterRequired;
        }
        if (self.wasapiUpmix) |upmix| if (upmix.enabled) {
            if (!unified or core.code != 714 or self.pcmCapture == null or !self.pcmCapture.?.enabled)
                return error.UpmixRequiresUnifiedPcm714;
            try absolute(upmix.dllPath orelse return error.UpmixPathRequired);
            try absolute(upmix.modelPath orelse return error.UpmixPathRequired);
            try absolute(upmix.freeSurroundDll orelse return error.UpmixPathRequired);
            if (upmix.cpuFallbackDll) |fallback| try absolute(fallback);
        };
        if (!unified and !std.mem.eql(u8, self.sourcePolicy, "fixed") and !std.mem.eql(u8, self.sourcePolicy, "active")) return error.InvalidSourcePolicy;
        if (unified and (!std.mem.eql(u8, renderer.backend, "roar") or self.sourceStream != 0))
            return error.UnifiedInputRequiresRoarStreamZero;
        if (unified and self.pcmCapture != null and self.pcmCapture.?.routeToOutput)
            return error.UnifiedInputConflictsWithDedicatedPcm;

        if(!network and !aes67 and !wasapi and !std.mem.eql(u8,self.outputBackend,"asio"))return error.InvalidOutputBackend;
        if(self.aoip) |settings| _=try @import("aoip_config.zig").native(settings);
        if(self.aes67) |settings| _=try @import("aes67_config.zig").prepare(settings,core.labels.len);
        if(network) {
            if(self.aoip==null)return error.AoipConfigRequired;
            // Keep inactive ASIO settings for switching back; native AoIP uses
            // standard renderer channels and its own buffering configuration.
            if(self.sampleRate!=48000)return error.AoipRequires48000;
        } else if(aes67) {
            // The RTP transport replaces the device side only. Channels and
            // order come from outputLayout/sources(); no device is opened.
            if(self.aes67==null)return error.Aes67ConfigRequired;
            if(self.sampleRate!=48000)return error.Aes67Requires48000;
        } else if(wasapi) {
            const settings=self.wasapi orelse return error.WasapiConfigRequired;
            if(!std.mem.eql(u8,settings.mode,"shared") and !std.mem.eql(u8,settings.mode,"exclusive"))return error.InvalidWasapiMode;
            if(settings.periodFrames<144 or settings.periodFrames>1920)return error.InvalidWasapiPeriod;
            try absolute(settings.dllPath);
            if(!validRenderEndpoint(settings.endpointId))return error.InvalidWasapiEndpoint;
            if(self.sampleRate!=48000 or !self.lsacEnabled())return error.WasapiRequiresLsac48000;
            if(!unified and !std.mem.eql(u8,self.sourcePolicy,"fixed"))return error.WasapiRequiresFixedSource;
            if(!std.mem.eql(u8,self.channelOrder,"olr"))return error.WasapiRequiresStandardOrder;
            if(self.routing) |route| {
                if(route.sourceChannelIndices) |v| {if(v.len!=2 or v[0]!=0 or v[1]!=1)return error.WasapiRequiresStandardOrder;}
                if(route.physicalChannels) |v| {if(v.len!=2 or v[0]!=0 or v[1]!=1)return error.WasapiRequiresStandardOrder;}
            }
        } else {
            if(!std.mem.startsWith(u8,self.driver,"asio:") or self.driver.len>4095 or std.mem.indexOfScalar(u8,self.driver,0)!=null)return error.InvalidDesktopPlayback;
            try absolute(self.asioDll);
        }
        if (self.browserIdentity != null and self.pcmCapture != null and self.pcmCapture.?.browserIdentity != null)
            return error.ConflictingBrowserIdentityConfig;
        if(self.browserBinding()) |identity| {if(identity.enabled) {
                if(self.pcmCapture==null or !self.pcmCapture.?.enabled)return error.BrowserIdentityRequiresPcm;
                try absolute(identity.nodeExe);try absolute(identity.agentScript);try absolute(identity.backendExe);try absolute(identity.identityFile);
                if(identity.devtoolsActivePortFile) |file| try absolute(file);
                if(identity.leaseMs<100 or identity.leaseMs>5000 or identity.metadataPort==0 or identity.extensionId.len!=32)return error.InvalidBrowserIdentity;
                for(identity.extensionId) |ch| {if(ch<'a' or ch>'p')return error.InvalidBrowserIdentity;}
                if(!std.mem.startsWith(u8,identity.devtoolsUrl,"http://127.0.0.1:") and !std.mem.startsWith(u8,identity.devtoolsUrl,"http://localhost:"))return error.InvalidBrowserIdentity;
        }}
        if(self.pcmCapture) |capture| {
            if(capture.routeToOutput and !capture.enabled)return error.PcmCaptureDisabled;
            if(capture.enabled) {
                try absolute(capture.captureDll);try absolute(capture.ingressDll);
                if (!unified) {
                    if (!validRenderEndpoint(capture.inputEndpointId)) return error.InvalidPcmInputEndpoint;
                    if (self.wasapi) |sink| {
                        if (std.ascii.eqlIgnoreCase(sink.endpointId, capture.inputEndpointId))
                            return error.PcmInputEqualsOutput;
                    }
                }
                if(capture.routeToOutput and !wasapi)return error.PcmOutputRequiresWasapi;
            }
        }
        const count=self.channelCount();
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
            (!std.mem.eql(u8,self.channelOrder,"olr") and !std.mem.eql(u8,self.channelOrder,"dvs-speakers") and !std.mem.eql(u8,self.channelOrder,"custom"))) return error.InvalidDesktopPlayback;
        try absolute(self.bridgeDll);
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
pub fn validRenderEndpoint(id:[]const u8) bool {
    const prefix="{0.0.0.00000000}.";
    if(!std.mem.startsWith(u8,id,prefix) or id.len!=prefix.len+38)return false;
    const guid=id[prefix.len..];
    if(guid[0]!='{' or guid[37]!='}')return false;
    for(guid[1..37],1..) |ch,i| {
        if(i==9 or i==14 or i==19 or i==24){if(ch!='-')return false;}
        else if(!std.ascii.isHex(ch))return false;
    }
    return true;
}

/// Windows editors may write a UTF-8 BOM; it is not part of the JSON document.
/// The same typed validation is used before endpoint/session side effects.
pub fn parse(allocator: std.mem.Allocator, bytes: []const u8) !std.json.Parsed(Config) {
    const json=if(std.mem.startsWith(u8,bytes,"\xef\xbb\xbf")) bytes[3..] else bytes;
    var result=try std.json.parseFromSlice(Config,allocator,json,.{.allocate=.alloc_always});
    errdefer result.deinit();
    try result.value.validate();
    return result;
}
