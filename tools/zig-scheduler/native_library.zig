//! Module-local runtime dependencies, independent of the compiler's PATH.
const std=@import("std");
pub fn open(path:[]const u8) !std.DynLib {
    const allocator=std.heap.page_allocator;
    const absolute=try std.fs.path.resolve(allocator,&.{path});
    defer allocator.free(absolute);
    const wide=try std.unicode.utf8ToUtf16LeAllocZ(allocator,absolute);
    defer allocator.free(wide);
    // LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS.
    const module=std.os.windows.kernel32.LoadLibraryExW(wide.ptr,null,0x1100) orelse {
        std.debug.print("Native module load failed: {s}, Win32={d}; check module-local runtime dependencies\n",.{absolute,@intFromEnum(std.os.windows.kernel32.GetLastError())});
        return error.NativeLibraryLoad;
    };
    return .{.inner=.{.dll=module}};
}
