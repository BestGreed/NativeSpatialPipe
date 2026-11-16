const std=@import("std");
const p=@import("aoip_config.zig");
test "omitted and blank master select discovery" {
    const omitted = try p.parse(std.testing.allocator,
        \\{"interfaceIp":"192.168.50.1","mac":"f0d7afd04b1d"}
    );
    const blank = try p.native(.{.interfaceIp="192.168.50.1",.masterIp="  ",.mac="f0d7afd04b1d"});
    try std.testing.expectEqual([4]u8{0,0,0,0}, omitted.master_ip);
    try std.testing.expectEqual(omitted.master_ip, blank.master_ip);
    try std.testing.expectError(error.InvalidAoipIp, p.native(.{.interfaceIp="192.168.50.1",.masterIp="0.0.0.0",.mac="f0d7afd04b1d"}));
}
test "network configuration maps to fixed versioned Rust ABI" {
    const n=try p.parse(std.testing.allocator,
        \\{"interfaceIp":"192.168.50.1","masterIp":"192.168.50.2","mac":"f0d7afd04b1d"}
    );
    try std.testing.expectEqual(@as(usize,72),@sizeOf(p.Native));
    try std.testing.expectEqual(@as(u32,2),n.version);
    try std.testing.expectEqualSlices(u8,&.{192,168,50,1},&n.interface_ip);
    try std.testing.expectEqual(@as(u32,960),n.prebuffer_frames);
    try std.testing.expectEqual(@as(u32,4000),n.tx_latency_us);
    try std.testing.expectEqual(@as(u32,32),n.frames_per_packet);
}

test "JSON latency and packet size cross the native ABI without experimental environment" {
    var f=p.File{.interfaceIp="192.168.50.1",.masterIp="192.168.50.2",.mac="f0d7afd04b1d",.txLatencyUs=1200,.framesPerPacket=0,.prebufferFrames=64};
    var n=try p.native(f);try std.testing.expect(n.tx_latency_us==1200 and n.frames_per_packet==0);
    try std.testing.expectEqual(@as(u32,64),n.prebuffer_frames);
    f.prebufferFrames=31;try std.testing.expectError(error.InvalidAoipConfig,p.native(f));f.prebufferFrames=64;
    f.framesPerPacket=64;n=try p.native(f);try std.testing.expectEqual(@as(u32,64),n.frames_per_packet);
    f.framesPerPacket=1;try std.testing.expectError(error.InvalidAoipConfig,p.native(f));
    f.framesPerPacket=257;try std.testing.expectError(error.InvalidAoipConfig,p.native(f));
    f.framesPerPacket=32;f.txLatencyUs=249;try std.testing.expectError(error.InvalidAoipConfig,p.native(f));
    f.txLatencyUs=100001;try std.testing.expectError(error.InvalidAoipConfig,p.native(f));
}
test "network configuration rejects unknown fields and invalid packet source settings" {
    try std.testing.expectError(error.UnknownField,p.parse(std.testing.allocator,
        \\{"interfaceIp":"192.168.50.1","masterIp":"192.168.50.2","mac":"f0d7afd04b1d","typo":1}
    ));
    try std.testing.expectError(error.InvalidAoipConfig,p.parse(std.testing.allocator,
        \\{"interfaceIp":"192.168.50.1","masterIp":"192.168.50.2","mac":"f0d7afd04b1d","prebufferFrames":0}
    ));
}
