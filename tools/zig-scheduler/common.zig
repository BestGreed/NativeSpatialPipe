const std = @import("std");
pub const c = @cImport({
    @cDefine("_WIN32_WINNT", "0x0A00");
    @cDefine("WIN32_LEAN_AND_MEAN", "1");
    @cInclude("winsock2.h");
    @cInclude("windows.h");
    @cInclude("tlhelp32.h");
    @cInclude("Native-Hook.h");
});
pub const a = std.heap.page_allocator;
pub const Value = std.json.Value;
pub fn fmt(comptime f: []const u8, args: anytype) ![]u8 {
    return std.fmt.allocPrint(a, f, args);
}
pub fn join(parts: []const []const u8) ![]u8 {
    return std.fs.path.resolve(a, parts);
}
pub fn wide(s: []const u8) ![:0]u16 {
    return std.unicode.utf8ToUtf16LeAllocZ(a, s);
}
pub fn exists(path: []const u8) bool {
    std.fs.cwd().access(path, .{}) catch return false;
    return true;
}
pub fn read(path: []const u8) ![]u8 {
    return std.fs.cwd().readFileAlloc(a, path, 8 * 1024 * 1024);
}
pub fn parse(bytes: []const u8) !std.json.Parsed(Value) {
    return std.json.parseFromSlice(Value, a, std.mem.trimLeft(u8, bytes, "\xef\xbb\xbf"), .{ .allocate = .alloc_always });
}
pub fn field(v: Value, key: []const u8) Value {
    return if (v == .object) v.object.get(key) orelse .null else .null;
}
pub fn string(v: Value) ![]const u8 {
    return if (v == .string) v.string else error.ExpectedString;
}
pub fn number(v: Value) !u64 {
    return if (v == .integer and v.integer >= 0) @intCast(v.integer) else error.ExpectedInteger;
}
pub fn yes(v: Value) bool {
    return v == .bool and v.bool;
}
pub fn json(value: anytype) ![]u8 {
    return std.json.stringifyAlloc(a, value, .{});
}
pub fn write(path: []const u8, bytes: []const u8) !void {
    if (std.fs.path.dirname(path)) |parent| try std.fs.cwd().makePath(parent);
    var nonce: [8]u8 = undefined;
    std.crypto.random.bytes(&nonce);
    const tmp = try fmt("{s}.{s}.tmp", .{ path, std.fmt.fmtSliceHexLower(&nonce) });
    defer a.free(tmp);
    const file = try std.fs.cwd().createFile(tmp, .{});
    {
        errdefer file.close();
        try file.writeAll(bytes);
        try file.sync();
    }
    file.close();
    errdefer std.fs.cwd().deleteFile(tmp) catch {};
    const from = try wide(tmp);
    defer a.free(from);
    const to = try wide(path);
    defer a.free(to);
    if (c.MoveFileExW(from.ptr, to.ptr, c.MOVEFILE_REPLACE_EXISTING | c.MOVEFILE_WRITE_THROUGH) == 0) return error.AtomicReplaceFailed;
}
pub fn writeJson(path: []const u8, value: anytype) !void {
    const bytes = try json(value);
    defer a.free(bytes);
    try write(path, bytes);
}
pub fn load(path: []const u8) !std.json.Parsed(Value) {
    const bytes = try read(path);
    defer a.free(bytes);
    return parse(bytes);
}
pub fn remove(path: []const u8) void {
    std.fs.cwd().deleteFile(path) catch {};
}
pub fn option(args: []const []const u8, key: []const u8) ?[]const u8 {
    for (args, 0..) |arg, i| if (std.mem.eql(u8, arg, key) and i + 1 < args.len) return args[i + 1];
    return null;
}
pub fn flag(args: []const []const u8, key: []const u8) bool {
    for (args) |arg| {
        if (std.mem.eql(u8, arg, key)) return true;
    }
    return false;
}
pub const Process = struct {
    handle: c.HANDLE,
    pid: u32,
    pub fn close(self: *Process) void {
        _ = c.CloseHandle(self.handle);
        self.handle = null;
    }
};
pub fn quote(args: []const []const u8) ![]u8 {
    var text = std.ArrayList(u8).init(a);
    errdefer text.deinit();
    for (args, 0..) |arg, index| {
        if (index != 0) try text.append(' ');
        try text.append('"');
        var slashes: usize = 0;
        for (arg) |ch| {
            if (ch == '\\') {
                slashes += 1;
                continue;
            }
            try text.appendNTimes('\\', slashes * (if (ch == '"') @as(usize, 2) else 1) + @intFromBool(ch == '"'));
            slashes = 0;
            try text.append(ch);
        }
        try text.appendNTimes('\\', slashes * 2);
        try text.append('"');
    }
    return text.toOwnedSlice();
}
pub fn spawnLog(args: []const []const u8, log: ?[]const u8) !Process {
    const command = try quote(args);
    defer a.free(command);
    const w = try wide(command);
    defer a.free(w);
    var startup = std.mem.zeroes(c.STARTUPINFOW);
    startup.cb = @sizeOf(c.STARTUPINFOW);
    var info = std.mem.zeroes(c.PROCESS_INFORMATION);
    var attrs = std.mem.zeroes(c.SECURITY_ATTRIBUTES);
    attrs.nLength = @sizeOf(c.SECURITY_ATTRIBUTES);
    attrs.bInheritHandle = 1;
    const logW = try wide(log orelse "NUL");
    defer a.free(logW);
    const file = c.CreateFileW(logW.ptr, c.GENERIC_WRITE, c.FILE_SHARE_READ | c.FILE_SHARE_WRITE, &attrs, c.CREATE_ALWAYS, c.FILE_ATTRIBUTE_NORMAL, null);
    if (file == c.INVALID_HANDLE_VALUE) return error.ChildLog;
    defer _ = c.CloseHandle(file);
    const nul = try wide("NUL");
    defer a.free(nul);
    const input = c.CreateFileW(nul.ptr, c.GENERIC_READ, c.FILE_SHARE_READ | c.FILE_SHARE_WRITE, &attrs, c.OPEN_EXISTING, 0, null);
    if (input == c.INVALID_HANDLE_VALUE) return error.ChildInput;
    defer _ = c.CloseHandle(input);
    startup.dwFlags = c.STARTF_USESTDHANDLES;
    startup.hStdInput = input;
    startup.hStdOutput = file;
    startup.hStdError = file;
    if (c.CreateProcessW(null, w.ptr, null, null, 1, c.CREATE_NO_WINDOW, null, null, &startup, &info) == 0) return error.ChildSpawn;
    _ = c.CloseHandle(info.hThread);
    return .{ .handle = info.hProcess, .pid = info.dwProcessId };
}
pub fn spawn(args: []const []const u8) !Process {
    return spawnLog(args, null);
}
pub fn done(child: *Process) !?u32 {
    if (c.WaitForSingleObject(child.handle, 0) != c.WAIT_OBJECT_0) return null;
    var code: u32 = 0;
    if (c.GetExitCodeProcess(child.handle, &code) == 0) return error.ChildExitCode;
    return code;
}
pub fn run(args: []const []const u8) ![]u8 {
    const tmpDir = try std.process.getEnvVarOwned(a, "TEMP");
    defer a.free(tmpDir);
    var nonce: [16]u8 = undefined;
    std.crypto.random.bytes(&nonce);
    const path = try fmt("{s}/nsp-zig-{s}.log", .{ tmpDir, std.fmt.fmtSliceHexLower(&nonce) });
    defer a.free(path);
    var child = try spawnLog(args, path);
    defer child.close();
    if (c.WaitForSingleObject(child.handle, 40000) != c.WAIT_OBJECT_0) return error.ChildTimeout;
    defer remove(path);
    if ((try done(&child)).? != 0) {
        const failure = read(path) catch null;
        if (failure) |bytes| {
            defer a.free(bytes);
            std.debug.print("Native child failed: {s}\n", .{bytes});
        }
        return error.ChildFailed;
    }
    return read(path);
}
pub fn processIdentity(pid: u32) !?u64 {
    const h = c.OpenProcess(c.PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
    if (h == null) {
        if (c.GetLastError() == c.ERROR_INVALID_PARAMETER) return null;
        return error.ProcessAccessDenied;
    }
    defer _ = c.CloseHandle(h);
    var created: c.FILETIME = undefined;
    var exited: c.FILETIME = undefined;
    var kernel: c.FILETIME = undefined;
    var user: c.FILETIME = undefined;
    if (c.GetProcessTimes(h, &created, &exited, &kernel, &user) == 0) return error.ProcessIdentityFailed;
    return (@as(u64, created.dwHighDateTime) << 32) | created.dwLowDateTime;
}
pub fn debugPrivilege() !void {
    var token: c.HANDLE = null;
    if (c.OpenProcessToken(c.GetCurrentProcess(), c.TOKEN_ADJUST_PRIVILEGES | c.TOKEN_QUERY, &token) == 0) return error.TokenAccess;
    defer _ = c.CloseHandle(token);
    var privileges = std.mem.zeroes(c.TOKEN_PRIVILEGES);
    privileges.PrivilegeCount = 1;
    const name = try wide("SeDebugPrivilege");
    defer a.free(name);
    if (c.LookupPrivilegeValueW(null, name.ptr, &privileges.Privileges[0].Luid) == 0) return error.PrivilegeLookup;
    privileges.Privileges[0].Attributes = c.SE_PRIVILEGE_ENABLED;
    if (c.AdjustTokenPrivileges(token, 0, &privileges, @sizeOf(c.TOKEN_PRIVILEGES), null, null) == 0 or c.GetLastError() != 0) return error.PrivilegeUnavailable;
}
pub fn audioPid() !u32 {
    const snap = c.CreateToolhelp32Snapshot(c.TH32CS_SNAPPROCESS, 0);
    if (snap == c.INVALID_HANDLE_VALUE) return error.ProcessInventory;
    defer _ = c.CloseHandle(snap);
    var entry = std.mem.zeroes(c.PROCESSENTRY32W);
    entry.dwSize = @sizeOf(c.PROCESSENTRY32W);
    var more = c.Process32FirstW(snap, &entry);
    var pid: u32 = 0;
    while (more != 0) : (more = c.Process32NextW(snap, &entry)) {
        const name = try std.unicode.utf16LeToUtf8Alloc(a, std.mem.sliceTo(&entry.szExeFile, 0));
        defer a.free(name);
        if (std.ascii.eqlIgnoreCase(name, "audiodg.exe")) {
            if (pid != 0) return error.MultipleAudioHosts;
            pid = entry.th32ProcessID;
        }
    }
    if (pid == 0) return error.NoAudioHost;
    return pid;
}
pub const Modules = struct {
    names: std.ArrayList([]u8),
    pub fn deinit(self: *Modules) void {
        for (self.names.items) |name| a.free(name);
        self.names.deinit();
    }
    pub fn has(self: *Modules, name: []const u8) bool {
        for (self.names.items) |item| {
            if (std.ascii.eqlIgnoreCase(item, name)) return true;
        }
        return false;
    }
};
pub fn modules(pid: u32) !Modules {
    const snap = c.CreateToolhelp32Snapshot(c.TH32CS_SNAPMODULE | c.TH32CS_SNAPMODULE32, pid);
    if (snap == c.INVALID_HANDLE_VALUE) return error.ModuleInventory;
    defer _ = c.CloseHandle(snap);
    var result = Modules{ .names = std.ArrayList([]u8).init(a) };
    errdefer result.deinit();
    var entry = std.mem.zeroes(c.MODULEENTRY32W);
    entry.dwSize = @sizeOf(c.MODULEENTRY32W);
    var more = c.Module32FirstW(snap, &entry);
    if (more == 0) return error.ModuleInventory;
    while (more != 0) : (more = c.Module32NextW(snap, &entry)) try result.names.append(try std.unicode.utf16LeToUtf8Alloc(a, std.mem.sliceTo(&entry.szModule, 0)));
    return result;
}
pub fn checkModules(pid: u32) !bool {
    var inventory = try modules(pid);
    defer inventory.deinit();
    for (inventory.names.items) |name| {
        const lower = try std.ascii.allocLowerString(a, name);
        defer a.free(lower);
        for ([_][]const u8{ "frida", "gum-js", "quickjs", "v8" }) |part| if (std.mem.indexOf(u8, lower, part) != null) return error.RuntimeAlreadyPresent;
    }
    return inventory.has("nsp_native_hook.dll");
}
pub fn checkTarget(pid: u32) !u64 {
    try debugPrivilege();
    const h = c.OpenProcess(0x43a, 0, pid);
    if (h == null) return error.ProcessAccessDenied;
    defer _ = c.CloseHandle(h);
    var protection: u32 = 0;
    if (c.GetProcessInformation(h, 7, &protection, 4) == 0 or protection != 0xfffffffe) return error.ProtectedTarget;
    var path: [32768]u16 = undefined;
    var count: u32 = path.len;
    if (c.QueryFullProcessImageNameW(h, 0, &path, &count) == 0) return error.TargetImage;
    const name = try std.unicode.utf16LeToUtf8Alloc(a, path[0..count]);
    defer a.free(name);
    var system: [32768]u16 = undefined;
    const size = c.GetSystemDirectoryW(&system, system.len);
    const sys = try std.unicode.utf16LeToUtf8Alloc(a, system[0..size]);
    defer a.free(sys);
    const expected = try join(&.{ sys, "audiodg.exe" });
    defer a.free(expected);
    if (!std.ascii.eqlIgnoreCase(name, expected)) return error.TargetImage;
    const image = try read(expected);
    defer a.free(image);
    var hash: [32]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(image, &hash, .{});
    var digest: [32]u8 = undefined;
    const approved = try std.fmt.hexToBytes(&digest, "3dd24fe6faf0b56f99d5b00f24687b5f3b3e76e15b219e23f0963114a0dce9a5");
    if (!std.mem.eql(u8, &hash, approved)) return error.TargetBuildMismatch;
    return (try processIdentity(pid)) orelse error.TargetExited;
}
pub fn symbol(comptime T: type, lib: *std.DynLib, name: [:0]const u8) !T {
    return lib.lookup(T, name) orelse error.MissingNativeExport;
}
pub fn library(path: []const u8) !std.DynLib {
    // Match ctypes' dependency lookup: bundled libunwind beside the Rust DLL,
    // plus Windows default directories; never search the working directory.
    const normalized = try std.fs.path.resolve(a, &.{path});
    defer a.free(normalized);
    const w = try wide(normalized);
    defer a.free(w);
    const handle = c.LoadLibraryExW(w.ptr, null, 0x1100) orelse {
        std.debug.print("LoadLibraryExW {s}: Win32={d}\n", .{ normalized, c.GetLastError() });
        return error.NativeLibraryLoad;
    };
    return .{ .inner = .{ .dll = @ptrCast(handle) } };
}
pub fn line(stream: std.net.Stream, limit: usize) ![]u8 {
    var bytes = std.ArrayList(u8).init(a);
    errdefer bytes.deinit();
    while (bytes.items.len < limit) {
        var ch: [1]u8 = undefined;
        if (try stream.read(&ch) == 0) return error.EndOfStream;
        if (ch[0] == '\n') return bytes.toOwnedSlice();
        try bytes.append(ch[0]);
    }
    return error.LineTooLong;
}
