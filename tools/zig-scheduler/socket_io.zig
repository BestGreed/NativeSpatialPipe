//! Control-plane sockets only. std.net.Stream uses ReadFile on Windows, whose
//! wait is not bounded by SO_RCVTIMEO. Use nonblocking Winsock with one deadline.
const std = @import("std");
const c = @import("common.zig").c;
pub const SocketIO = struct {
    stream: std.net.Stream,
    deadline: u64,
    pub const Error = error{ SocketSetup, SocketRead, SocketWrite, SocketWait, SocketTimeout };
    pub fn init(stream: std.net.Stream, milliseconds: u32) Error!SocketIO {
        var enabled: c_ulong = 1;
        if (c.ioctlsocket(@intFromPtr(stream.handle), @as(c_long, @bitCast(@as(u32, 0x8004667e))), &enabled) != 0) return error.SocketSetup;
        return .{ .stream = stream, .deadline = c.GetTickCount64() + milliseconds };
    }
    fn wait(self: *SocketIO, events: c_short) Error!void {
        const now = c.GetTickCount64();
        if (now >= self.deadline) return error.SocketTimeout;
        var descriptor = c.WSAPOLLFD{ .fd = @intFromPtr(self.stream.handle), .events = events, .revents = 0 };
        const result = c.WSAPoll(&descriptor, 1, @intCast(self.deadline - now));
        if (result == 0) return error.SocketTimeout;
        if (result < 0) return error.SocketWait;
    }
    pub fn read(self: *SocketIO, buffer: []u8) Error!usize {
        if (buffer.len == 0) return 0;
        while (true) {
            if (c.GetTickCount64() >= self.deadline) return error.SocketTimeout;
            const result = c.recv(@intFromPtr(self.stream.handle), @ptrCast(buffer.ptr), @intCast(@min(buffer.len, std.math.maxInt(c_int))), 0);
            if (result >= 0) return @intCast(result);
            if (c.WSAGetLastError() != c.WSAEWOULDBLOCK) return error.SocketRead;
            try self.wait(c.POLLRDNORM);
        }
    }
    pub fn write(self: *SocketIO, bytes: []const u8) Error!usize {
        if (bytes.len == 0) return 0;
        while (true) {
            if (c.GetTickCount64() >= self.deadline) return error.SocketTimeout;
            const result = c.send(@intFromPtr(self.stream.handle), @ptrCast(bytes.ptr), @intCast(@min(bytes.len, std.math.maxInt(c_int))), 0);
            if (result > 0) return @intCast(result);
            if (result == 0 or c.WSAGetLastError() != c.WSAEWOULDBLOCK) return error.SocketWrite;
            try self.wait(c.POLLWRNORM);
        }
    }
    pub fn reader(self: *SocketIO) std.io.Reader(*SocketIO, Error, read) {
        return .{ .context = self };
    }
    pub fn writer(self: *SocketIO) std.io.Writer(*SocketIO, Error, write) {
        return .{ .context = self };
    }
};
