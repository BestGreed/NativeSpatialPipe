const std = @import("std");
const u = @import("common.zig");
const w = @cImport({
    @cDefine("_WIN32_WINNT", "0x0A00");
    @cInclude("windows.h");
    @cInclude("mmdeviceapi.h");
});
const enumerator_class = w.GUID{ .Data1 = 0xbcde0395, .Data2 = 0xe52f, .Data3 = 0x467c, .Data4 = .{ 0x8e, 0x3d, 0xc4, 0x57, 0x92, 0x91, 0x69, 0x2e } };
const enumerator_iid = w.GUID{ .Data1 = 0xa95664d2, .Data2 = 0x9614, .Data3 = 0x4f35, .Data4 = .{ 0xa7, 0x46, 0xde, 0x8d, 0xb6, 0x36, 0x17, 0xe6 } };

pub fn absent(endpoint: []const u8) !bool {
    const prefix = "{0.0.0.00000000}.{";
    if (endpoint.len != prefix.len + 37 or !std.mem.startsWith(u8, endpoint, prefix) or endpoint[endpoint.len - 1] != '}')
        return error.InvalidRenderEndpoint;
    for (endpoint[prefix.len .. endpoint.len - 1], 0..) |byte, index| {
        const valid = switch (index) {
            8, 13, 18, 23 => byte == '-',
            else => std.ascii.isHex(byte),
        };
        if (!valid) return error.InvalidRenderEndpoint;
    }
    const id = try u.wide(endpoint);
    defer u.a.free(id);
    const handle = w.LoadLibraryExW(std.unicode.utf8ToUtf16LeStringLiteral("ole32.dll"), null, w.LOAD_LIBRARY_SEARCH_SYSTEM32) orelse
        return error.EndpointComLibrary;
    var ole: std.DynLib = .{ .inner = .{ .dll = @ptrCast(handle) } };
    defer ole.close();
    const initialize = try u.symbol(@TypeOf(&w.CoInitializeEx), &ole, "CoInitializeEx");
    const uninitialize = try u.symbol(@TypeOf(&w.CoUninitialize), &ole, "CoUninitialize");
    const create = try u.symbol(@TypeOf(&w.CoCreateInstance), &ole, "CoCreateInstance");
    if (initialize(null, w.COINIT_MULTITHREADED) < 0) return error.EndpointComInit;
    defer uninitialize();
    var enumerator: ?*w.IMMDeviceEnumerator = null;
    if (create(&enumerator_class, null, w.CLSCTX_INPROC_SERVER, &enumerator_iid, @ptrCast(&enumerator)) < 0)
        return error.EndpointEnumerator;
    const devices = enumerator orelse return error.EndpointEnumerator;
    defer _ = devices.lpVtbl.*.Release.?(devices);
    var endpoint_device: ?*w.IMMDevice = null;
    const result = devices.lpVtbl.*.GetDevice.?(devices, id.ptr, @ptrCast(&endpoint_device));
    if (@as(u32, @bitCast(result)) == 0x80070490) return true; // HRESULT_FROM_WIN32(ERROR_NOT_FOUND)
    if (result < 0) return error.EndpointLookup;
    const device = endpoint_device orelse return error.EndpointLookup;
    defer _ = device.lpVtbl.*.Release.?(device);
    var state: w.DWORD = 0;
    if (device.lpVtbl.*.GetState.?(device, &state) < 0) return error.EndpointState;
    return state == w.DEVICE_STATE_NOTPRESENT or state == w.DEVICE_STATE_UNPLUGGED;
}
