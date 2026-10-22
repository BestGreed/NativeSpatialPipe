const std = @import("std");
const u = @import("common.zig");
const host = @import("host.zig");
const backend = @import("backend.zig");
const control = @import("control.zig");
pub fn main() void {
    execute() catch |err| {
        std.debug.print("NSp Zig scheduler: {s}\n", .{@errorName(err)});
        std.process.exit(20);
    };
}
fn execute() !void {
    const args = try std.process.argsAlloc(u.a);
    defer std.process.argsFree(u.a, args);
    if (args.len < 2) return error.CommandRequired;
    const exe = try std.fs.selfExePathAlloc(u.a);
    defer u.a.free(exe);
    const inferred = std.fs.path.dirname(std.fs.path.dirname(std.fs.path.dirname(exe) orelse return error.RootRequired) orelse return error.RootRequired) orelse return error.RootRequired;
    const root = u.option(args, "--root") orelse inferred;
    if (std.mem.eql(u8,args[1],"validate-playback")) {
        const profile=@import("desktop_playback.zig");
        const path=u.option(args,"--playback-config") orelse return error.PlaybackConfigRequired;
        const bytes=try u.read(path);defer u.a.free(bytes);
        var parsed=try profile.parse(u.a,bytes);defer parsed.deinit();
        const processing=parsed.value.processingConfig();
        const selection=try profile.select(parsed.value.renderer,parsed.value.mode);
        const result=try u.json(.{.valid=true,.renderer=selection.backend,.rendererMode=selection.mode,.playback=parsed.value,
            .sourceChannels=@as(u32,12),.sourceSampleRate=@as(u32,48000),.sourceChannelIndices=try parsed.value.sources(),
            .physicalChannelIndices=try parsed.value.physical(),.outputChannelLabels=try parsed.value.labels(),
            .effectiveOutputProcessing=.{.bypass=processing.bypass,.effectiveBypass=processing.bypass or processing.neutral(),.placement="rust-output-pool",.master=processing.master,.trim=processing.trim[0..12],.delayFrames=processing.delay_frames[0..12],
                .limiter=.{.enabled=processing.limiter,.ceiling=processing.ceiling,.releaseMs=processing.release_ms,
                    .configuredLookaheadFrames=processing.lookahead_frames,.effectiveLookaheadFrames=if(processing.limiter and !processing.bypass) processing.lookahead_frames else @as(u32,0)}}});
        defer u.a.free(result);try std.io.getStdOut().writer().print("{s}\n",.{result});return;
    }
    if (std.mem.eql(u8, args[1], "host")) return host.run(args, root);
    const local = try std.process.getEnvVarOwned(u.a, "LOCALAPPDATA");
    defer u.a.free(local);
    const defaultHome = try u.join(&.{ local, "NativeSpatialPipe" });
    defer u.a.free(defaultHome);
    const home = u.option(args, "--home") orelse defaultHome;
    if (std.mem.eql(u8, args[1], "serve")) return backend.serve(args, root, home);
    if (std.mem.eql(u8, args[1], "backend-start")) return control.ensure(args, root, home);
    if (std.mem.eql(u8, args[1], "request")) {
        const action = u.option(args, "--action") orelse "status";
        var value = try u.parse(u.option(args, "--json") orelse "{}");
        defer value.deinit();
        const reply = try backend.request(home, action, value.value);
        defer u.a.free(reply);
        try std.io.getStdOut().writer().print("{s}\n", .{reply});
        return;
    }
    std.process.exit(try control.run(args, root, home));
}
