const std = @import("std");
const pool = @import("output_stream.zig");
const S = pool.Stream;
test "all layout pool callbacks use configured dimensions and preserve lease ownership" {
    for([_]u32{8,10,12,14,16}) |channels| {
        const stream=try S.create(std.testing.allocator);defer stream.destroy();
        try stream.configure(.{.channels=channels,.master=0.5});
        var data=[_]f32{0.25} ** (480*16);var written:u32=0;
        try std.testing.expectEqual(@as(c_int,0),pool.processPool(stream,0,&data,480,channels,&written));
        try std.testing.expectEqual(@as(u32,480),written);
        for(data[0..480*channels]) |sample| try std.testing.expectEqual(@as(f32,0.125),sample);
        var token=Token{};
        try std.testing.expectEqual(@as(c_int,0),pool.publishPool(stream,0,&data,480,channels,0,&token,Token.release));
        const block=stream.popLease().?;try std.testing.expect(block.pcm==data[0..].ptr);
        stream.releaseLease(block);try std.testing.expectEqual(@as(u32,1),token.returns);
    }
}
const Token = struct {
    returns: u32 = 0,
    fn release(context: ?*anyopaque) callconv(.C) void {
        const self: *Token = @ptrCast(@alignCast(context.?));self.returns += 1;
    }
    fn lease(self: *Token, pcm: []const f32) S.Lease {
        return .{.pcm=pcm.ptr,.frames=@intCast(pcm.len/S.channels),.token=self,.release_fn=release};
    }
};
test "lease retains exact pool address and returns once after consumer completes" {
    const value = try S.create(std.testing.allocator);defer value.destroy();
    const pcm = [_]f32{0.25} ** S.samples;var token=Token{};
    try value.pushLease(0,token.lease(&pcm),false);
    const block=value.popLease().?;
    try std.testing.expect(block.pcm==&pcm and token.returns==0);
    value.finish();try std.testing.expect(value.ended());
    value.releaseLease(block);try std.testing.expect(token.returns==1);
    try std.testing.expect(value.snapshot().pool_blocks_received==value.snapshot().pool_blocks_released);
}
test "short renderer and DSP tails preserve independent counts and order" {
    const value=try S.create(std.testing.allocator);defer value.destroy();
    const pcm=[_]f32{0.125} ** S.samples;var tokens=[_]Token{.{},.{},.{}};
    try value.pushLease(0,tokens[0].lease(&pcm),false);
    try value.pushLease(480,tokens[1].lease(pcm[0..32*12]),false);
    try std.testing.expectError(error.OutputDiscontinuity,value.pushLease(960,tokens[2].lease(&pcm),false));
    try std.testing.expect(tokens[2].returns==0);
    try value.pushLease(512,tokens[2].lease(pcm[0..20*12]),true);
    value.finish();
    for([_]u32{480,32,20}) |frames| {const block=value.popLease().?;try std.testing.expect(block.frames==frames);value.releaseLease(block);}
    try std.testing.expect(value.snapshot().received_frames==512 and value.snapshot().dsp_tail_frames==20 and value.ended());
    for(tokens) |token| try std.testing.expect(token.returns==1);
}
test "overflow abort returns queued leases while rejected lease stays with Rust" {
    const value=try S.create(std.testing.allocator);defer value.destroy();
    const pcm=[_]f32{0.25} ** S.samples;var tokens=[_]Token{.{}} ** (S.slots+1);
    for(0..S.slots) |i| try value.pushLease(i*S.frames,tokens[i].lease(&pcm),false);
    try std.testing.expect(pool.publishPool(value,S.slots*S.frames,&pcm,S.frames,S.channels,0,&tokens[S.slots],Token.release)!=0);
    try std.testing.expectEqual(@as(?anyerror,error.OutputQueueFull),value.snapshot().failure);
    try std.testing.expect(value.popLease()==null);
    for(tokens[0..S.slots]) |token| try std.testing.expect(token.returns==1);
    try std.testing.expect(tokens[S.slots].returns==0);
}
test "abort does not release an in-flight submit; teardown releases remaining leases" {
    const value=try S.create(std.testing.allocator);
    const pcm=[_]f32{0.25} ** S.samples;var tokens=[_]Token{.{},.{}};
    try value.pushLease(0,tokens[0].lease(&pcm),false);try value.pushLease(480,tokens[1].lease(&pcm),false);
    const held=value.popLease().?;value.fail(error.DeviceFailure);
    try std.testing.expect(tokens[0].returns==0 and tokens[1].returns==1);
    value.releaseLease(held);value.destroy();
    try std.testing.expect(tokens[0].returns==1 and tokens[1].returns==1);
}
test "pool processor changes the caller block in place and drains tail without scratch" {
    const value=try S.create(std.testing.allocator);defer value.destroy();
    var config=@import("output_processing.zig").Config{.master=0.5};config.delay_frames[0]=2;
    try value.configure(config);var pcm=[_]f32{1} ** 12;var written:u32=0;
    try std.testing.expect(pool.processPool(value,0,&pcm,1,12,&written)==0 and written==1);
    try std.testing.expect(pcm[0]==0 and pcm[1]==0.5);
    var tail:[S.samples]f32=undefined;
    try std.testing.expect(pool.processPool(value,1,&tail,S.frames,12,&written)==0 and written==2);
    try std.testing.expect(tail[12]==0.5);
    try std.testing.expect(pool.processPool(value,1,&tail,S.frames,12,&written)==0 and written==0);
}

test "EOF backpressure has a bounded timeout and returns every queued lease" {
    const value=try S.create(std.testing.allocator);defer value.destroy();
    const pcm=[_]f32{0.25} ** S.samples;var tokens=[_]Token{.{}} ** (S.slots+1);
    for(0..S.slots) |i| try value.pushLease(i*S.frames,tokens[i].lease(&pcm),false);
    value.tail_started=try std.time.Instant.now();value.tail_timeout_ms=1;
    try std.testing.expect(pool.publishPool(value,S.slots*S.frames,&pcm,S.frames,S.channels,1,&tokens[S.slots],Token.release)!=0);
    try std.testing.expectEqual(@as(?anyerror,error.OutputTailTimeout),value.snapshot().failure);
    for(tokens[0..S.slots]) |token| try std.testing.expect(token.returns==1);
    try std.testing.expect(tokens[S.slots].returns==0);
}
