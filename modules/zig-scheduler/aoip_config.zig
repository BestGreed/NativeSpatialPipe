//! Experimental network output configuration. No DSP or PCM processing here.
const std=@import("std");
pub const File=struct {
    interfaceIp:[]const u8, masterIp:[]const u8="", mac:[]const u8,
    name:[]const u8="NSP Native AoIP", prebufferFrames:u32=960,
    txLatencyUs:u32=4000, framesPerPacket:u32=32,
};
pub const Native=extern struct {
    size:u32=@sizeOf(Native), version:u32=2, interface_ip:[4]u8, master_ip:[4]u8,
    mac:[12]u8, name:[32]u8, prebuffer_frames:u32, tx_latency_us:u32=4000, frames_per_packet:u32=32,
};
fn ip(text:[]const u8) ![4]u8 {
    var parts=std.mem.splitScalar(u8,text,'.');var out:[4]u8=undefined;
    for(&out) |*v| v.*=try std.fmt.parseInt(u8,parts.next() orelse return error.InvalidAoipIp,10);
    if(parts.next()!=null)return error.InvalidAoipIp;
    return out;
}
pub fn parse(allocator:std.mem.Allocator,json:[]const u8) !Native {
    if(json.len>4096)return error.InvalidAoipConfig;
    const parsed=try std.json.parseFromSlice(File,allocator,json,.{});defer parsed.deinit();
    return native(parsed.value);
}
pub fn native(f:File) !Native {
    if(f.txLatencyUs<250 or f.txLatencyUs>100000 or (f.framesPerPacket!=0 and (f.framesPerPacket<2 or f.framesPerPacket>256)))return error.InvalidAoipConfig;
    if(f.mac.len!=12 or f.name.len==0 or f.name.len>31 or f.prebufferFrames<32 or f.prebufferFrames>4800)return error.InvalidAoipConfig;
    for(f.mac) |c| if(!std.ascii.isHex(c))return error.InvalidAoipConfig;
    for(f.name) |c| if(c<32 or c>126)return error.InvalidAoipConfig;
    const master = std.mem.trim(u8, f.masterIp, " \t\r\n");
    const master_ip = if (master.len == 0) [_]u8{0} ** 4 else try ip(master);
    if (master.len != 0 and (master_ip[0] == 0 or master_ip[0] >= 224)) return error.InvalidAoipIp;
    var n=Native{.interface_ip=try ip(f.interfaceIp),.master_ip=master_ip,.mac=undefined,.name=[_]u8{0}**32,.prebuffer_frames=f.prebufferFrames};
    @memcpy(&n.mac,f.mac);@memcpy(n.name[0..f.name.len],f.name);
    if(n.interface_ip[0]==0 or n.interface_ip[0]==127 or n.interface_ip[0]>=224)return error.InvalidAoipIp;
    n.tx_latency_us=f.txLatencyUs;n.frames_per_packet=f.framesPerPacket;
    return n;
}
