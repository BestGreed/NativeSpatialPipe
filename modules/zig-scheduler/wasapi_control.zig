//! Explicit shared/exclusive endpoint sink. Audio stays in native Rust/C++ code.
const std=@import("std");
const u=@import("common.zig");
const processing=@import("output_processing.zig");
const playback=@import("desktop_playback.zig");
pub const Release=*const fn(?*anyopaque) callconv(.C) void;
pub const Publish=*const fn(?*anyopaque,u64,[*]const f32,u32,u32,u32,?*anyopaque,Release) callconv(.C) c_int;
const VolumeRead = *const fn (?*anyopaque) callconv(.C) f64;
const VolumeSet = *const fn (?*anyopaque, f64) callconv(.C) void;
const VolumeControl = struct {
    read: VolumeRead, set: VolumeSet, pipeline: ?*anyopaque, reference: f64,
    attenuation: ?f64 = null,
};
pub const Session=struct {
    lib:std.DynLib,handle:?*anyopaque,
    publish:Publish,
    closeFn:*const fn(?*anyopaque) callconv(.C) void,
    drainFn:*const fn(?*anyopaque,u32) callconv(.C) c_int,
    errorFn:*const fn(?*anyopaque) callconv(.C) i32,
    statsFn:*const fn(?*anyopaque,[*]u8,u32) callconv(.C) void,
    drain_timeout:u32,mode:[]const u8,
    volume: ?VolumeControl = null,
    pub fn init(path:[]const u8,endpoint:[]const u8,timeout:u32,mode:[]const u8,period:u32) !Session {
        var lib=try u.library(path);errdefer lib.close();
        const version=try u.symbol(*const fn() callconv(.C) u32,&lib,"nsp_wasapi_abi_version");
        if(version()!=1)return error.WasapiAbiMismatch;
        const open=try u.symbol(*const fn([*:0]const u16) callconv(.C) ?*anyopaque,&lib,"nsp_wasapi_open");
        const close=try u.symbol(*const fn(?*anyopaque) callconv(.C) void,&lib,"nsp_wasapi_close");
        const publish=try u.symbol(Publish,&lib,"nsp_wasapi_publish");
        const drain_fn=try u.symbol(*const fn(?*anyopaque,u32) callconv(.C) c_int,&lib,"nsp_wasapi_drain");
        const err=try u.symbol(*const fn(?*anyopaque) callconv(.C) i32,&lib,"nsp_wasapi_error");
        const stats=try u.symbol(*const fn(?*anyopaque,[*]u8,u32) callconv(.C) void,&lib,"nsp_wasapi_stats");
        const wide=try u.wide(endpoint);defer u.a.free(wide);
        const handle=if(std.mem.eql(u8,mode,"exclusive")) blk:{
            const open_v2=try u.symbol(*const fn([*:0]const u16,u32,u32) callconv(.C) ?*anyopaque,&lib,"nsp_wasapi_open_v2");
            break :blk open_v2(wide.ptr,1,period) orelse return error.WasapiExclusiveEndpointOpen;
        }else open(wide.ptr) orelse return error.WasapiEndpointOpen;
        return .{.lib=lib,.handle=handle,.publish=publish,.closeFn=close,.drainFn=drain_fn,.errorFn=err,.statsFn=stats,.drain_timeout=timeout,.mode=mode};
    }
    pub fn bind(self:*Session,lib:*std.DynLib,pipeline:?*anyopaque,config:processing.Config,loudness:?playback.Loudness) !void {
        const native=processing.NativeConfig.from(config);
        if (loudness) |profile| {
            const Bind = *const fn (?*anyopaque,*const processing.NativeConfig,Publish,?*anyopaque,f64,[*]const playback.LoudnessLevel,usize) callconv(.C) c_int;
            const bindFn = try u.symbol(Bind,lib,"nsp_pipeline_set_native_pool_output_v2");
            const read = try u.symbol(VolumeRead,&self.lib,"nsp_wasapi_hardware_volume_db");
            const set = try u.symbol(VolumeSet,lib,"nsp_pipeline_set_hardware_volume_db");
            if(bindFn(pipeline,&native,self.publish,self.handle,profile.referenceSplDb,profile.levels.ptr,profile.levels.len)!=0)
                return error.WasapiPoolBind;
            self.volume = .{ .read=read, .set=set, .pipeline=pipeline, .reference=profile.referenceSplDb };
            self.pollVolume();
            return;
        }
        const Bind=*const fn(?*anyopaque,*const processing.NativeConfig,Publish,?*anyopaque) callconv(.C) c_int;
        const bindFn=try u.symbol(Bind,lib,"nsp_pipeline_set_native_pool_output");
        if(bindFn(pipeline,&native,self.publish,self.handle)!=0)return error.WasapiPoolBind;
    }
    pub fn pollVolume(self: *Session) void {
        if (self.volume) |*volume| {
            const db = volume.read(self.handle);
            volume.attenuation = if (std.math.isFinite(db)) db else null;
            volume.set(volume.pipeline,db);
        }
    }
    pub fn failed(self:*Session) bool {return self.errorFn(self.handle)<0;}
    pub fn report(self:*Session,path:[]const u8,endpoint:[]const u8,source:u32) !void {
        var bytes:[2048]u8=std.mem.zeroes([2048]u8);
        self.statsFn(self.handle,&bytes,bytes.len);
        var stats=try u.parse(std.mem.sliceTo(&bytes,0));defer stats.deinit();
        const Loudness = struct { hardwareAttenuationDb: ?f64, referenceSplDb: f64, estimatedSplDb: ?f64 };
        const loudness: ?Loudness = if (self.volume) |volume| .{
            .hardwareAttenuationDb=volume.attenuation,
            .referenceSplDb=volume.reference,
            .estimatedSplDb=if(volume.attenuation) |db| volume.reference+db else null,
        } else null;
        try u.writeJson(path,.{.outputBackend="wasapi",.endpointId=endpoint,.mode=self.mode,.outputLayout="2.0",.sourceStream=source,.state=if(self.failed()) "faulted" else "running",.stats=stats.value,.loudness=loudness});
    }
    /// Caller must finish every bound Rust producer before draining or closing.
    pub fn drain(self:*Session) !void {
        if(self.drainFn(self.handle,self.drain_timeout)!=0)return error.WasapiDrainFailed;
    }
    pub fn deinit(self:*Session) void {self.closeFn(self.handle);self.lib.close();}
};
