//! Dependency resolution smoke only; never opens an audio driver.
const std=@import("std");
pub fn main() !void {
    const args=try std.process.argsAlloc(std.heap.page_allocator);
    defer std.process.argsFree(std.heap.page_allocator,args);
    if(args.len!=3)return error.Arguments;
    var dll=if(std.mem.eql(u8,args[1],"legacy")) try std.DynLib.open(args[2]) else try @import("native_library.zig").open(args[2]);
    defer dll.close();
    const abi=dll.lookup(*const fn() callconv(.C) u32,"nsp_asio_abi_version") orelse return error.MissingAsioExport;
    if(abi()!=1)return error.InvalidAbi;
    try std.io.getStdOut().writer().writeAll("ASIO module loaded; ABI verified; no device opened\n");
}
