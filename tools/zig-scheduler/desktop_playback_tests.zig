const std=@import("std");
const Config=@import("desktop_playback.zig").Config;
fn valid() Config{return .{.bridgeDll="C:\\fixture\\bridge.dll",.asioDll="C:\\fixture\\asio.dll",.driver="asio:test"};}

test "diagnostic archives default off and are independent of playback DSP" {
    var value=valid();try value.validate();
    try std.testing.expect(!value.recording.pcm and !value.recording.telemetrySnapshots);
    value.recording.pcm=true;try value.validate();
    try std.testing.expect(!value.recording.telemetrySnapshots and value.processingConfig().neutral());
    value.recording.pcm=false;value.recording.telemetrySnapshots=true;try value.validate();
    try std.testing.expect(!value.recording.pcm and value.processingConfig().neutral());
}

test "seven layouts share channel counts, routing and DSP dimensions across backends" {
    for(@import("layout_presets.zig").presets) |preset| {
        var value=valid();value.outputLayout=preset.id;
        for([_][]const u8{"roar","lira"}) |backend| {
            value.renderer=backend;value.rendererDll="C:\\fixture\\lira.dll";
            try value.validate();
            try std.testing.expectEqual(preset.labels.len,value.channelCount());
            try std.testing.expectEqual(preset.labels.len,(try value.sources()).len);
            try std.testing.expectEqual(preset.labels.len,(try value.physical()).len);
            try std.testing.expectEqual(preset.labels.len,value.processingConfig().channels);
            const labels=try value.labels();
            for(preset.labels,0..) |label,i| try std.testing.expectEqualStrings(label,labels[i]);
        }
    }
}
test "calibration cardinality, DVS wiring and reserved 6DoF slot fail before startup" {
    var value=valid();value.outputLayout="9.1.6";
    value.outputProcessing=.{.trim=&([_]f64{1} ** 12)};
    try std.testing.expectError(error.InvalidCalibrationChannelCount,value.validate());
    value.outputProcessing=null;value.channelOrder="dvs-speakers";
    try std.testing.expectError(error.DvsOrderRequires714,value.validate());
    value.channelOrder="olr";value.roarCustomLayout=.{.measurementFile="C:\\future\\measurement.json"};try value.validate();
    value.outputLayout="roar-custom-6dof";
    try std.testing.expectError(error.ReservedCustomLayoutNotReady,value.validate());
    value.outputLayout="9.1.6";value.renderer="lira";value.rendererDll="C:\\fixture\\lira.dll";
    try std.testing.expectError(error.RoarOptionsWithLiraBackend,value.validate());
}
test "desktop config separates ROAR backend from its OLR mode and LIRA" {
    var value=valid();try value.validate();value.renderer="lira";
    try std.testing.expectError(error.InvalidDesktopPlaybackPath,value.validate());
    value.rendererDll="C:\\fixture\\lira.dll";try value.validate();
    value.channelOrder="dvs-speakers";value.sourceStream=3;value.sampleRate=96000;value.bufferFrames=128;value.master=0.25;try value.validate();
}
test "legacy OLR selector resolves only to ROAR OLR, not a third backend" {
    const selection=@import("desktop_playback.zig").select;
    const current=try selection("roar","olr");
    const legacy=try selection("olr",null);
    try std.testing.expectEqualStrings("roar",current.backend);
    try std.testing.expectEqualStrings(current.backend,legacy.backend);
    try std.testing.expectEqualStrings(current.mode.?,legacy.mode.?);
    const lira=try selection("lira",null);
    try std.testing.expectEqualStrings("lira",lira.backend);
    try std.testing.expect(lira.mode==null);
    try std.testing.expectError(error.InvalidRendererMode,selection("lira","olr"));
    try std.testing.expectError(error.InvalidRendererMode,selection("roar","obr"));
    try std.testing.expectError(error.InvalidRendererMode,selection("olr","obr"));
    var old=valid();old.renderer="olr";try old.validate();
}
test "invalid playback cannot reach endpoint activation" {
    var value=valid();value.renderer="other";try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
    value=valid();value.bridgeDll="relative.dll";try std.testing.expectError(error.InvalidDesktopPlaybackPath,value.validate());
    value=valid();value.master=std.math.nan(f64);try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
    value=valid();value.sourceStream=4;try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
    value=valid();value.bufferFrames=65537;try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
    value=valid();value.sampleRate=0;try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
    value=valid();value.channelOrder="guess";try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
}

test "legacy JSON defaults and partial DSP preserve the existing processing contract" {
    const text=
        \\{"bridgeDll":"C:\\fixture\\bridge.dll","asioDll":"C:\\fixture\\asio.dll","driver":"asio:test",
        \\"master":0.5,"outputProcessing":{"trim":[0.5,1,1,1,1,1,1,1,1,1,1,1],"delayFrames":[0,2,0,0,0,0,0,0,0,0,0,0],"limiter":{"enabled":true,"ceiling":0.8,"releaseMs":25,"lookaheadFrames":13}}}
    ;
    var parsed=try std.json.parseFromSlice(Config,std.testing.allocator,text,.{});defer parsed.deinit();
    try parsed.value.validate();const p=parsed.value.processingConfig();
    try std.testing.expect(p.master==0.5 and p.trim[0]==0.5 and p.trim[1]==1 and p.delay_frames[1]==2);
    try std.testing.expect(p.channels==12 and p.sample_rate==48000 and p.limiter and p.ceiling==0.8 and p.release_ms==25 and p.lookahead_frames==13);
    const old=valid();try old.validate();const neutral=old.processingConfig();
    try std.testing.expect(!neutral.limiter and neutral.lookahead_frames==0 and neutral.master==1);
    for(0..12) |ch| try std.testing.expect(neutral.trim[ch]==1 and neutral.delay_frames[ch]==0);
}

test "DSP resource limits and malformed arrays reject before session creation" {
    const options=@import("desktop_playback.zig");
    var value=valid();var trims=[_]f64{1} ** 12;var delays=[_]u32{0} ** 12;var dsp=options.OutputProcessing{.trim=&trims,.delayFrames=&delays};
    trims[11]=-1;value.outputProcessing=dsp;try std.testing.expectError(error.InvalidOutputConfig,value.validate());
    trims[11]=1;delays[0]=48001;value.outputProcessing=dsp;try std.testing.expectError(error.InvalidOutputConfig,value.validate());
    delays[0]=0;dsp.limiter.ceiling=0;value.outputProcessing=dsp;try std.testing.expectError(error.InvalidOutputConfig,value.validate());
    dsp.limiter.ceiling=1;dsp.limiter.lookaheadFrames=4801;value.outputProcessing=dsp;try std.testing.expectError(error.InvalidOutputConfig,value.validate());
    const malformed=
        \\{"bridgeDll":"C:\\b.dll","asioDll":"C:\\a.dll","driver":"asio:test","outputProcessing":{"trim":[1,1]}}
    ;
    try std.testing.expectError(error.InvalidCalibrationChannelCount,options.parse(std.testing.allocator,malformed));
}

test "routing addresses physical outputs separately from standard speaker DSP order" {
    const options=@import("desktop_playback.zig");var value=valid();
    value.channelOrder="dvs-speakers";
    value.routing=options.Routing{.physicalChannels=&.{11,10,9,8,7,6,5,4,3,2,1,0}};
    try value.validate();const sources=try value.sources();const physical=try value.physical();const labels=try value.labels();
    try std.testing.expect(sources[1]==2 and physical[1]==10);try std.testing.expectEqualStrings("C",labels[1]);
    value.channelOrder="custom";try std.testing.expectError(error.CustomRoutingRequired,value.validate());
    value.routing.?.sourceChannelIndices=&.{0,0,2,3,4,5,6,7,8,9,10,11};try value.validate(); // Explicit fan-out is allowed.
    value.routing.?.physicalChannels=&.{11,11,9,8,7,6,5,4,3,2,1,0};try std.testing.expectError(error.DuplicatePhysicalChannel,value.validate());
    value.routing.?.physicalChannels=null;value.routing.?.sourceChannelIndices=&.{12,0,2,3,4,5,6,7,8,9,10,11};try std.testing.expectError(error.InvalidSourceChannel,value.validate());
    value.routing=null;value.channelOrder="custom";try std.testing.expectError(error.CustomRoutingRequired,value.validate());
}

test "OLR mapping and existing device policy are explicit and backend scoped" {
    const options=@import("desktop_playback.zig");var value=valid();
    value.positionMapping=options.PositionMapping{.mode="scaled",.reference=2};try value.validate();
    value.renderer="lira";value.rendererDll="C:\\fixture\\lira.dll";try std.testing.expectError(error.OlrOptionsWithLiraBackend,value.validate());
    value.renderer="roar";value.positionMapping.?.mode="direction";try std.testing.expectError(error.InvalidPositionMapping,value.validate());
    value.positionMapping.?.reference=1;value.clockSync=false;value.maxRebuffers=0;value.prebufferFrames=960;try value.validate();
    value.startupTimeoutMs=0;value.rebufferTimeoutMs=0;try value.validate();
    value.startupTimeoutMs=5000;value.rebufferTimeoutMs=1000;value.maxRebuffers=3;try value.validate();
    value.drainTimeoutMs=0;try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());value.drainTimeoutMs=5000;
    value.startupTimeoutMs=5000;value.prebufferFrames=48001;try std.testing.expectError(error.InvalidDesktopPlayback,value.validate());
}

test "JSON bypass is explicit and retained separately from neutral auto bypass" {
    var parsed=try @import("desktop_playback.zig").parse(std.testing.allocator,
        "{\"bridgeDll\":\"C:\\\\b.dll\",\"asioDll\":\"C:\\\\a.dll\",\"driver\":\"asio:test\",\"master\":0.25,\"outputProcessing\":{\"bypass\":true}}");
    defer parsed.deinit();
    const config=parsed.value.processingConfig();
    try std.testing.expect(config.bypass and !config.neutral() and config.master==0.25);
}
