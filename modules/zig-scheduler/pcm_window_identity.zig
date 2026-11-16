//! Control-plane display labels for ordinary application PCM sources.
//! The caller supplies an already observed PID plus process creation FILETIME.
//! Titles never establish ownership: only an exact live process generation and
//! exactly one visible top-level HWND can produce a matched label. A window or
//! process may change after return; this is an observation, not an audio lease.
const std = @import("std");
const c = @cImport({
    @cDefine("_WIN32_WINNT", "0x0A00");
    @cDefine("WIN32_LEAN_AND_MEAN", "1");
    @cInclude("windows.h");
});

pub const ProcessKey = struct {
    pid: u32,
    /// Raw Windows process-creation FILETIME, in 100 ns units. Not QPC time.
    created_filetime: u64,
};

pub const State = enum { matched, unknown, ambiguous };
pub const Reason = enum {
    unique_visible_top_level,
    invalid_identity,
    process_unavailable,
    process_query_failed,
    process_exited,
    process_generation_mismatch,
    enumeration_failed,
    no_visible_top_level,
    multiple_visible_top_level,
    window_changed,
    title_unavailable,
    title_too_long,
    invalid_title_encoding,
};

pub const Result = struct {
    state: State = .unknown,
    reason: Reason = .invalid_identity,
    pid: u32,
    created_filetime: u64,
    observed_created_filetime: ?u64 = null,
    /// GetTickCount64 observation time; use only to expire this display label.
    observed_monotonic_ms: u64 = 0,
    window_count: u32 = 0,
    hwnd: ?usize = null,
    title: ?[]u8 = null,
    os_error: u32 = 0,

    pub fn deinit(self: *Result, allocator: std.mem.Allocator) void {
        if (self.title) |title| allocator.free(title);
        self.title = null;
    }
};

const Candidates = struct {
    pid: u32,
    count: u32 = 0,
    only: ?usize = null,

    fn observe(self: *Candidates, hwnd: usize, owner_pid: u32, visible: bool, top_level: bool) void {
        if (owner_pid != self.pid or !visible or !top_level) return;
        self.count +|= 1;
        self.only = if (self.count == 1) hwnd else null;
    }

    fn apply(self: Candidates, result: *Result) void {
        result.window_count = self.count;
        if (self.count == 0) {
            result.state = .unknown;
            result.reason = .no_visible_top_level;
        } else if (self.count != 1) {
            result.state = .ambiguous;
            result.reason = .multiple_visible_top_level;
        }
    }
};

fn enumerate(window: c.HWND, parameter: c.LPARAM) callconv(std.os.windows.WINAPI) c.BOOL {
    const candidates: *Candidates = @ptrFromInt(@as(usize, @bitCast(parameter)));
    var pid: c.DWORD = 0;
    if (c.GetWindowThreadProcessId(window, &pid) == 0) return 1;
    // EnumWindows enumerates top-level windows. Check GA_ROOT as well so the
    // final revalidation uses exactly the same predicate. Owned dialogs also
    // count; a title, owner-chain or foreground preference must not break a tie.
    candidates.observe(@intFromPtr(window), pid, c.IsWindowVisible(window) != 0,
        c.GetAncestor(window, c.GA_ROOT) == window);
    return 1;
}

// HWND is an opaque value and is never dereferenced.
fn opaqueWindowHandle(handle: usize) c.HWND {
    @setRuntimeSafety(false);
    return @ptrFromInt(handle);
}

fn verifyProcess(process: c.HANDLE, key: ProcessKey, result: *Result) bool {
    const wait = c.WaitForSingleObject(process, 0);
    if (wait == c.WAIT_OBJECT_0) {
        result.reason = .process_exited;
        return false;
    }
    if (wait != c.WAIT_TIMEOUT) {
        result.reason = .process_query_failed;
        result.os_error = c.GetLastError();
        return false;
    }
    var created: c.FILETIME = undefined;
    var exited: c.FILETIME = undefined;
    var kernel: c.FILETIME = undefined;
    var user: c.FILETIME = undefined;
    if (c.GetProcessTimes(process, &created, &exited, &kernel, &user) == 0) {
        result.reason = .process_query_failed;
        result.os_error = c.GetLastError();
        return false;
    }
    const observed = (@as(u64, created.dwHighDateTime) << 32) | created.dwLowDateTime;
    result.observed_created_filetime = observed;
    if (observed != key.created_filetime) {
        result.reason = .process_generation_mismatch;
        return false;
    }
    return true;
}

fn sameWindow(window: c.HWND, pid: u32) bool {
    var owner: c.DWORD = 0;
    return c.IsWindow(window) != 0 and c.IsWindowVisible(window) != 0 and
        c.GetAncestor(window, c.GA_ROOT) == window and
        c.GetWindowThreadProcessId(window, &owner) != 0 and owner == pid;
}

/// Control thread only. Does not inspect window titles to select a process,
/// search parent/child processes, activate windows, or change process rights.
/// The caller owns Result.title and releases it with Result.deinit(allocator).
/// OS uncertainty is returned explicitly; allocation failure is an error.
pub fn resolve(allocator: std.mem.Allocator, key: ProcessKey) !Result {
    var result = Result{ .pid = key.pid, .created_filetime = key.created_filetime,
        .observed_monotonic_ms = c.GetTickCount64() };
    if (key.pid == 0 or key.created_filetime == 0) return result;
    const process = c.OpenProcess(c.PROCESS_QUERY_LIMITED_INFORMATION | c.SYNCHRONIZE, 0, key.pid);
    if (process == null) {
        result.reason = .process_unavailable;
        result.os_error = c.GetLastError();
        return result;
    }
    defer _ = c.CloseHandle(process);
    if (!verifyProcess(process, key, &result)) return result;

    var candidates = Candidates{ .pid = key.pid };
    c.SetLastError(0);
    if (c.EnumWindows(enumerate, @bitCast(@intFromPtr(&candidates))) == 0) {
        result.reason = .enumeration_failed;
        result.os_error = c.GetLastError();
        return result;
    }
    // Recheck the held process handle: an exited process cannot provide a
    // trustworthy label even if a recycled HWND/PID appeared in enumeration.
    if (!verifyProcess(process, key, &result)) return result;
    candidates.apply(&result);
    const handle = candidates.only orelse return result;
    const window = opaqueWindowHandle(handle);
    if (!sameWindow(window, key.pid)) {
        result.reason = .window_changed;
        return result;
    }

    var text: [4096]u16 = undefined;
    c.SetLastError(0);
    const length = c.GetWindowTextW(window, &text, text.len);
    if (length <= 0) {
        result.reason = .title_unavailable;
        result.os_error = c.GetLastError();
        return result;
    }
    if (length >= text.len - 1) {
        result.reason = .title_too_long;
        return result;
    }
    if (!sameWindow(window, key.pid)) {
        result.reason = .window_changed;
        return result;
    }
    if (!verifyProcess(process, key, &result)) return result;
    const title = std.unicode.utf16LeToUtf8Alloc(allocator, text[0..@intCast(length)]) catch |err| switch (err) {
        error.OutOfMemory => return err,
        else => {
            result.reason = .invalid_title_encoding;
            return result;
        },
    };
    result.state = .matched;
    result.reason = .unique_visible_top_level;
    result.hwnd = handle;
    result.title = title;
    result.observed_monotonic_ms = c.GetTickCount64();
    return result;
}

test "only the exact PID and visible top-level window enter the candidate set" {
    var candidates = Candidates{ .pid = 44 };
    candidates.observe(100, 45, true, true);
    candidates.observe(101, 44, false, true);
    candidates.observe(102, 44, true, false);
    try std.testing.expectEqual(@as(u32, 0), candidates.count);
    candidates.observe(103, 44, true, true);
    try std.testing.expectEqual(@as(u32, 1), candidates.count);
    try std.testing.expectEqual(@as(?usize, 103), candidates.only);
}

test "multiple windows remain ambiguous without title or foreground tie breaking" {
    var candidates = Candidates{ .pid = 44 };
    candidates.observe(100, 44, true, true);
    candidates.observe(101, 44, true, true);
    var result = Result{ .pid = 44, .created_filetime = 123 };
    candidates.apply(&result);
    try std.testing.expectEqual(State.ambiguous, result.state);
    try std.testing.expectEqual(Reason.multiple_visible_top_level, result.reason);
    try std.testing.expectEqual(@as(u32, 2), result.window_count);
    try std.testing.expect(candidates.only == null and result.title == null and result.hwnd == null);
}

test "no visible window is explicit unknown" {
    const candidates = Candidates{ .pid = 44 };
    var result = Result{ .pid = 44, .created_filetime = 123 };
    candidates.apply(&result);
    try std.testing.expectEqual(State.unknown, result.state);
    try std.testing.expectEqual(Reason.no_visible_top_level, result.reason);
}

test "missing generation is never accepted and PID reuse is rejected before window lookup" {
    var absent = try resolve(std.testing.allocator, .{ .pid = c.GetCurrentProcessId(), .created_filetime = 0 });
    defer absent.deinit(std.testing.allocator);
    try std.testing.expectEqual(Reason.invalid_identity, absent.reason);
    var created: c.FILETIME = undefined;
    var exited: c.FILETIME = undefined;
    var kernel: c.FILETIME = undefined;
    var user: c.FILETIME = undefined;
    try std.testing.expect(c.GetProcessTimes(c.GetCurrentProcess(), &created, &exited, &kernel, &user) != 0);
    const generation = (@as(u64, created.dwHighDateTime) << 32) | created.dwLowDateTime;
    var reused = try resolve(std.testing.allocator, .{ .pid = c.GetCurrentProcessId(), .created_filetime = generation + 1 });
    defer reused.deinit(std.testing.allocator);
    try std.testing.expectEqual(State.unknown, reused.state);
    try std.testing.expectEqual(Reason.process_generation_mismatch, reused.reason);
    try std.testing.expectEqual(@as(?u64, generation), reused.observed_created_filetime);
    try std.testing.expect(reused.title == null and reused.hwnd == null and reused.window_count == 0);
}

test "native enumeration labels one owned window and rejects two identical titles" {
    // Own test windows only: 1 px off-screen tool windows cannot take focus or
    // appear in the taskbar. This exercises Win32 ownership and UTF-16 title
    // retrieval without interacting with any user application's windows.
    const class = std.unicode.utf8ToUtf16LeStringLiteral("STATIC");
    const title = std.unicode.utf8ToUtf16LeStringLiteral("PCM identity fixture 音频");
    const style = c.WS_POPUP | c.WS_VISIBLE;
    const extended = c.WS_EX_TOOLWINDOW | c.WS_EX_NOACTIVATE;
    const first = c.CreateWindowExW(extended, class, title, style, -32000, -32000, 1, 1,
        null, null, c.GetModuleHandleW(null), null);
    try std.testing.expect(first != null);
    defer _ = c.DestroyWindow(first);
    var created: c.FILETIME = undefined;
    var exited: c.FILETIME = undefined;
    var kernel: c.FILETIME = undefined;
    var user: c.FILETIME = undefined;
    try std.testing.expect(c.GetProcessTimes(c.GetCurrentProcess(), &created, &exited, &kernel, &user) != 0);
    const key = ProcessKey{ .pid = c.GetCurrentProcessId(),
        .created_filetime = (@as(u64, created.dwHighDateTime) << 32) | created.dwLowDateTime };
    var single = try resolve(std.testing.allocator, key);
    defer single.deinit(std.testing.allocator);
    try std.testing.expectEqual(State.matched, single.state);
    try std.testing.expectEqual(@as(?usize, @intFromPtr(first)), single.hwnd);
    try std.testing.expectEqualStrings("PCM identity fixture 音频", single.title.?);
    const second = c.CreateWindowExW(extended, class, title, style, -32000, -32000, 1, 1,
        null, null, c.GetModuleHandleW(null), null);
    try std.testing.expect(second != null);
    defer _ = c.DestroyWindow(second);
    var multiple = try resolve(std.testing.allocator, key);
    defer multiple.deinit(std.testing.allocator);
    try std.testing.expectEqual(State.ambiguous, multiple.state);
    try std.testing.expectEqual(@as(u32, 2), multiple.window_count);
    try std.testing.expect(multiple.title == null and multiple.hwnd == null);
}
