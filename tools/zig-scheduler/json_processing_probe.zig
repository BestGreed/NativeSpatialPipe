//! Offline reference using the same JSON contract; no capture or device opened.
const std=@import("std");
const profile=@import("desktop_playback.zig");
const processing=@import("output_processing.zig");
const a=std.heap.page_allocator;
pub fn main() !void {
    const args=try std.process.argsAlloc(a);defer std.process.argsFree(a,args);
    if(args.len!=4)return error.Arguments;
    const json=try std.fs.cwd().readFileAlloc(a,args[1],1024*1024);defer a.free(json);
    var parsed=try profile.parse(a,json);defer parsed.deinit();
    const bytes=try std.fs.cwd().readFileAlloc(a,args[2],512*1024*1024);defer a.free(bytes);
    if(bytes.len%48!=0)return error.InvalidPcmLength;
    const samples=try a.alloc(f32,bytes.len/4);defer a.free(samples);
    for(samples,0..) |*sample,i| sample.*=@bitCast(std.mem.readInt(u32,bytes[i*4..][0..4],.little));
    var processor=try processing.Processor.init(a,parsed.value.processingConfig());defer processor.deinit();
    var output=try std.fs.cwd().createFile(args[3],.{.exclusive=true});defer output.close();
    const sources=try parsed.value.sources();
    var scratch:[480*12]f32=undefined;
    var first:usize=0;
    while(first<samples.len){
        const end=@min(first+480*12,samples.len);const block=samples[first..end];
        try processor.process(block);
        for(0..block.len/12) |frame| for(sources,0..) |source,ch| {scratch[frame*12+ch]=block[frame*12+source];};
        try output.writeAll(std.mem.sliceAsBytes(scratch[0..block.len]));first=end;
    }
    processor.finish();
    while(true){
        const frames=try processor.drain(samples[0..@min(samples.len,480*12)]);
        if(frames==0)break;
        for(0..frames) |frame| for(sources,0..) |source,ch| {scratch[frame*12+ch]=samples[frame*12+source];};
        try output.writeAll(std.mem.sliceAsBytes(scratch[0..frames*12]));
    }
}
