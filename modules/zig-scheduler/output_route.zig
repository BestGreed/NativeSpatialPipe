const std=@import("std");
const u=@import("common.zig");
pub const Route=struct{
    lib:std.DynLib,handle:?*anyopaque,
    selectFn:*const fn(?*anyopaque,u32) callconv(.C) c_int,
    destroyFn:*const fn(?*anyopaque) callconv(.C) void,
    pub fn init(path:[]const u8,network:@import("aoip_config.zig").Native,channels:u32) !Route{
        var lib=try u.library(path);errdefer lib.close();
        const Create=*const fn(*const @import("aoip_config.zig").Native,u32) callconv(.C) ?*anyopaque;
        const create=try u.symbol(Create,&lib,"nsp_aoip_route_create");
        const selectFn=try u.symbol(@TypeOf(@as(Route,undefined).selectFn),&lib,"nsp_aoip_route_select");
        const destroyFn=try u.symbol(@TypeOf(@as(Route,undefined).destroyFn),&lib,"nsp_aoip_route_destroy");
        const handle=create(&network,channels) orelse return error.OutputRouteCreate;
        return .{.lib=lib,.handle=handle,.selectFn=selectFn,.destroyFn=destroyFn};
    }
    pub fn select(self:*Route,source:?usize) !void{
        if(self.selectFn(self.handle,if(source) |i| @intCast(i) else std.math.maxInt(u32))!=0)return error.OutputRouteBusy;
    }
    pub fn deinit(self:*Route) void{self.destroyFn(self.handle);self.lib.close();}
};
