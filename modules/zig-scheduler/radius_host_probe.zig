//! Actual Zig host ABI -> native pipeline -> reliable sink, no device.
const std = @import("std");
const native = @import("native.zig");
const Stream = @import("output_stream.zig").Stream;
const a = std.heap.page_allocator;
pub fn main() !void {
    const args = try std.process.argsAlloc(a);
    defer std.process.argsFree(a,args);
    if (args.len != 5) return error.Arguments;
    const scaled = std.mem.eql(u8,args[3],"scaled");
    const reference = try std.fmt.parseFloat(f64,args[4]);
    const stream = try Stream.create(a);
    defer stream.destroy();
    var pipe = try native.Pipeline.initMapping(args[1],args[2],64,scaled,reference);
    defer pipe.close();
    try pipe.bindOutput(stream);
    const Create = *const fn(u32,u32,u32,u32,u32,f64) callconv(.C) ?*anyopaque;
    const Render = *const fn(?*anyopaque,u64,u32,[*]const u8,usize,[*]f32,usize) callconv(.C) c_int;
    const Destroy = *const fn(?*anyopaque) callconv(.C) void;
    const create = pipe.lib.lookup(Create,"nsp_packet_create") orelse return error.Export;
    const render = pipe.lib.lookup(Render,"nsp_packet_render") orelse return error.Export;
    const destroy = pipe.lib.lookup(Destroy,"nsp_packet_destroy") orelse return error.Export;
    const engine = create(480,48000,0,0,@intFromBool(scaled),reference) orelse return error.EngineCreate;
    defer destroy(engine);
    const expected = try a.alloc(f32,24*Stream.samples);
    defer a.free(expected);
    var raw: [32+480*4]u8 = undefined;
    var planar: [Stream.samples]f32 = undefined;
    const radii = [_]f32{0.125,0.25,0.5,0.9,1,2,4,128};
    for (0..24) |block| {
        const radius = radii[block%radii.len];
        const fields = [_]u32{1,480,1,if(block==0) 5 else 4,
            @bitCast(0.6*radius),@bitCast(-0.2*radius),@bitCast(-0.8*radius),@bitCast(@as(f32,0.25))};
        for(fields,0..) |value,i| std.mem.writeInt(u32,raw[i*4..][0..4],value,.little);
        for(0..480) |f| {
            const value: f32 = @floatCast(0.01*std.math.sin(@as(f64,@floatFromInt(block*480+f))*0.03));
            std.mem.writeInt(u32,raw[32+f*4..][0..4],@bitCast(value),.little);
        }
        if(render(engine,block,1,&raw,raw.len,&planar,planar.len)!=0) return error.SyncRender;
        for(0..480) |f| for(0..12) |ch| {expected[block*Stream.samples+f*12+ch]=planar[ch*480+f];};
        if(pipe.submit(pipe.handle,block,1,&raw,raw.len)!=0) return error.Submit;
    }
    var report = try pipe.finish();
    defer report.deinit();
    var block: [Stream.samples]f32 = undefined;
    for(0..24) |i| {
        if(!stream.pop(&block) or !std.mem.eql(u8,std.mem.asBytes(&block),std.mem.sliceAsBytes(expected[i*Stream.samples..][0..Stream.samples]))) return error.SinkMismatch;
    }
    if(stream.pop(&block)) return error.ExtraOutput;
    const file = try std.fs.cwd().readFileAlloc(a,args[2],expected.len*4);
    defer a.free(file);
    if(!std.mem.eql(u8,file,std.mem.sliceAsBytes(expected))) return error.FileMismatch;
    std.debug.print("Zig position mapping {s}/{d}: synchronous, sink and file PCM exact\n",.{args[3],reference});
}
