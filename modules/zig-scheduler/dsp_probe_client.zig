//! Diagnostic client of the current Rust DSP. No PCM algorithm lives in Zig.
const std=@import("std");
const p=@import("output_processing.zig");
const Run=*const fn(?*anyopaque,u32,[*]f32,u32,u32,*u32) callconv(.C) c_int;
const Destroy=*const fn(?*anyopaque) callconv(.C) void;
pub const Processor=struct {
    lib:std.DynLib, handle:?*anyopaque, config:p.Config,
    run:*const fn(?*anyopaque,u32,[*]f32,u32,u32,*u32) callconv(.C) c_int,
    destroy:*const fn(?*anyopaque) callconv(.C) void,
    pub fn init(_:std.mem.Allocator, config:p.Config,path:[]const u8) !Processor {
        try config.validate();var lib=try std.DynLib.open(path);errdefer lib.close();
        const Create=*const fn(*const p.NativeConfig) callconv(.C) ?*anyopaque;
        const create=lib.lookup(Create,"nsp_output_dsp_create") orelse return error.MissingRustDsp;
        const run=lib.lookup(Run,"nsp_output_dsp_process") orelse return error.MissingRustDsp;
        const destroy=lib.lookup(Destroy,"nsp_output_dsp_destroy") orelse return error.MissingRustDsp;
        const native=p.NativeConfig.from(config);const handle=create(&native) orelse return error.DspCreate;
        return .{.lib=lib,.handle=handle,.config=config,.run=run,.destroy=destroy};
    }
    pub fn deinit(self:*Processor) void {self.destroy(self.handle);self.lib.close();self.*=undefined;}
    pub fn process(self:*Processor,pcm:[]f32) !void {
        if(pcm.len==0)return;
        if(pcm.len%self.config.channels!=0)return error.InvalidOutputBlock;
        var written:u32=0;
        if(self.run(self.handle,0,pcm.ptr,@intCast(pcm.len/self.config.channels),self.config.channels,&written)!=0)return error.RustDspProcess;
        if(written!=pcm.len/self.config.channels)return error.RustDspLength;
    }
    pub fn finish(_: *Processor) void {} // drain operation finalizes idempotently in Rust.
    pub fn drain(self:*Processor,pcm:[]f32) !u32 {
        if(pcm.len==0 or pcm.len%self.config.channels!=0)return error.InvalidOutputBlock;
        var written:u32=0;
        if(self.run(self.handle,1,pcm.ptr,@intCast(pcm.len/self.config.channels),self.config.channels,&written)!=0)return error.RustDspDrain;
        return written;
    }
    pub fn timing(self:*const Processor) p.Timing {return self.config.timing();}
};
