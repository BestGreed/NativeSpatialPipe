// Offline ABI consumer using the actual scheduler's Zig version. No driver open.
const std = @import("std");
const c = @cImport({
    @cDefine("NSP_ASIO_STATIC", "1");
    @cInclude("asio_output_c.h");
});
comptime {
    if (@sizeOf(c.nsp_asio_config) != 64 or @sizeOf(c.nsp_asio_stats) != 96)
        @compileError("NSP ASIO x64 layout mismatch");
    if (@offsetOf(c.nsp_asio_config, "driver_id_utf8") != 8 or
        @offsetOf(c.nsp_asio_config, "device_sample_rate") != 48 or
        @offsetOf(c.nsp_asio_stats, "submitted_frames") != 32)
        @compileError("NSP ASIO field offset mismatch");
}
pub fn main() !void {
    const a = std.heap.page_allocator;
    const args = try std.process.argsAlloc(a);
    defer std.process.argsFree(a, args);
    if (args.len != 2) return error.DllPathRequired;
    var dll = try std.DynLib.open(args[1]);
    defer dll.close();
    const version = dll.lookup(*const @TypeOf(c.nsp_asio_abi_version), "nsp_asio_abi_version") orelse return error.MissingVersion;
    if (version() != c.NSP_ASIO_ABI_VERSION) return error.AbiVersionMismatch;
    // Resolve all entry points using their actual C-imported function types.
    inline for (.{ "open", "start", "submit", "prime", "service", "finish_input", "hold", "get_stats", "stop", "close", "last_error" }) |name| {
        _ = dll.lookup(*const @TypeOf(@field(c, "nsp_asio_" ++ name)), "nsp_asio_" ++ name) orelse return error.MissingExport;
    }
    const open = dll.lookup(*const @TypeOf(c.nsp_asio_open), "nsp_asio_open").?;
    const close = dll.lookup(*const @TypeOf(c.nsp_asio_close), "nsp_asio_close").?;
    const lastError = dll.lookup(*const @TypeOf(c.nsp_asio_last_error), "nsp_asio_last_error").?;
    var config = std.mem.zeroes(c.nsp_asio_config);
    config.struct_size = 8; // Fail before COM/driver initialization.
    config.abi_version = c.NSP_ASIO_ABI_VERSION;
    var handle: c.nsp_asio_handle = null;
    if (open(&config, &handle) != c.NSP_ASIO_INVALID or handle != null) return error.InvalidConfigAccepted;
    var message: [256]u8 = undefined;
    const size = lastError(&message, message.len);
    if (size <= 1 or size > message.len or message[size - 1] != 0) return error.InvalidErrorCopy;
    if (close(&handle) != 0 or handle != null) return error.NullCloseFailed;
    std.debug.print("Zig 0.13 host: C layouts, typed exports, invalid open/error copy/null close passed; no driver opened.\n", .{});
}
