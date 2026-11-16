//! Opt-in lifecycle glue. The session owner remains the existing host.
const std = @import("std");
const asio = @import("asio_control.zig");
pub const Stream = asio.Stream;
pub const Session = struct {
    allocator: std.mem.Allocator,
    stream: *Stream,
    worker: *asio.Worker,
    retired: bool = false,
    wait_ms: u64,
    detail: [256:0]u8 = std.mem.zeroes([256:0]u8),
    pub fn create(allocator: std.mem.Allocator, value: asio.Backend.Config) !*Session {
        const self = try allocator.create(Session);
        errdefer allocator.destroy(self);
        const stream = try Stream.create(allocator);
        errdefer stream.destroy();
        try stream.configure(value.processing);
        stream.tail_timeout_ms = value.drain_timeout_ms;
        const worker = try asio.Worker.create(allocator, .{});
        // Once spawned, storage must survive until worker confirms exit.
        self.* = .{ .allocator = allocator, .stream = stream, .worker = worker, .wait_ms = @as(u64, value.drain_timeout_ms) + 5000 };
        var config = value;
        config.stream = stream;
        _ = worker.submit(1, .open, config, null) catch unreachable;
        self.waitCommand() catch |err| {
            self.retire();
            return err;
        };
        _ = worker.submit(1, .start, null, null) catch unreachable;
        self.waitCommand() catch |err| {
            self.retire();
            return err;
        };
        return self;
    }
    fn waitCommand(self: *Session) !void {
        // Host setup runs in its own process; coordination API is separate.
        // Do not free an in-flight native operation merely because it is slow.
        while (true) {
            if (self.worker.poll()) |result| {
                if (result.outcome != .ok) return result.failure orelse error.OutputCommandFailed;
                return;
            }
            std.time.sleep(std.time.ns_per_ms);
        }
    }
    /// Must follow pipeline.finish, which joins all sink callbacks.
    pub fn finish(self: *Session) void {
        if (self.retired) return;
        self.stream.finish();
        var timer = std.time.Timer.start() catch unreachable;
        while (true) {
            const status = self.stream.snapshot();
            if (status.state == .done or status.failure != null) break;
            if (timer.read() / std.time.ns_per_ms > self.wait_ms) {
                self.stream.fail(error.OutputFinishTimeout);
                break;
            }
            std.time.sleep(std.time.ns_per_ms);
        }
        self.detail = self.worker.snapshot().detail;
        self.retire();
    }
    fn retire(self: *Session) void {
        self.worker.requestExit();
        // Preserve the owner process/resources if native cleanup cannot finish.
        // No repeated driver reset/reopen and no forced thread termination.
        while (!self.worker.tryDestroy()) std.time.sleep(std.time.ns_per_ms * 10);
        self.retired = true;
    }
    pub fn destroy(self: *Session) void {
        std.debug.assert(self.retired);
        const allocator = self.allocator;
        self.stream.destroy();
        allocator.destroy(self);
    }
};
