const std = @import("std");
const ctl = @import("output_control.zig");
const asio = @import("asio_control.zig");
const expect = std.testing.expect;
const Context = struct {
    entered: std.Thread.ResetEvent = .{},
    release: std.Thread.ResetEvent = .{},
    owner: ?std.Thread.Id = null,
    opens: usize = 0,
    closes: usize = 0,
    fail_stop: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    fail_start: bool = false,
    fn check(self: *Context) !void {
        const id = std.Thread.getCurrentId();
        if (self.owner) |owner| {
            if (owner != id) return error.WrongThread;
        } else self.owner = id;
    }
};
const Fake = struct {
    pub const Config = struct { block: bool = false, value: u32 = 123 };
    context: *Context,
    pub fn open(self: *Fake, config: Config) !void {
        try self.context.check();
        if (config.value != 123) return error.BorrowedConfig;
        if (config.block) {
            self.context.entered.set();
            self.context.release.wait();
        }
        self.context.opens += 1;
    }
    pub fn start(self: *Fake) !void {
        try self.context.check();
        if (self.context.fail_start) return error.DriverStart;
    }
    pub fn stop(self: *Fake) !void {
        try self.context.check();
        if (self.context.fail_stop.load(.acquire)) return error.DriverStop;
    }
    pub fn close(self: *Fake) !void {
        try self.context.check();
        self.context.closes += 1;
    }
};
const Worker = ctl.Worker(Fake);
fn result(worker: *Worker) !ctl.Result {
    var timer = try std.time.Timer.start();
    while (timer.read() < 3 * std.time.ns_per_s) {
        if (worker.poll()) |r| return r;
        std.time.sleep(std.time.ns_per_ms);
    }
    return error.ResultTimeout;
}
fn finish(worker: *Worker) !void {
    worker.requestExit();
    var timer = try std.time.Timer.start();
    while (timer.read() < 3 * std.time.ns_per_s) {
        if (worker.tryDestroy()) return;
        std.time.sleep(std.time.ns_per_ms);
    }
    return error.ExitTimeout;
}
test "blocking open keeps coordinator responsive; config copied and lifecycle same thread" {
    var context = Context{};
    const worker = try Worker.create(std.testing.allocator, .{ .context = &context });
    var config = Fake.Config{ .block = true };
    const id = try worker.submit(1, .open, config, null);
    config.value = 999;
    try context.entered.timedWait(std.time.ns_per_s);
    try expect(worker.snapshot().phase == .executing);
    const start_id = try worker.submit(1, .start, null, null);
    try expect(!worker.tryDestroy());
    context.release.set();
    const opened = try result(worker);
    try expect(opened.id == id and opened.outcome == .ok);
    try expect((try result(worker)).id == start_id);
    _ = try worker.submit(1, .close, null, null);
    try std.testing.expectEqual(@as(?anyerror, error.StopBeforeClose), (try result(worker)).failure);
    _ = try worker.submit(1, .stop, null, null);
    try expect((try result(worker)).outcome == .ok);
    _ = try worker.submit(1, .close, null, null);
    try expect((try result(worker)).outcome == .ok);
    try finish(worker);
    try expect(context.opens == 1 and context.closes == 1 and context.owner.? != std.Thread.getCurrentId());
}
test "generation change cleans stale open and rejects queued old commands" {
    var context = Context{};
    const worker = try Worker.create(std.testing.allocator, .{ .context = &context });
    _ = try worker.submit(1, .open, .{ .block = true }, null);
    try context.entered.timedWait(std.time.ns_per_s);
    _ = try worker.submit(1, .start, null, null);
    try worker.setGeneration(2);
    try std.testing.expectError(error.StaleGeneration, worker.submit(1, .open, .{}, null));
    _ = try worker.submit(2, .open, .{}, null);
    context.release.set();
    try expect((try result(worker)).outcome == .stale);
    try expect((try result(worker)).outcome == .stale);
    try expect((try result(worker)).outcome == .ok);
    try finish(worker);
    try expect(context.opens == 2 and context.closes == 2);
}
test "bounded reservations include unread results; expired commands never execute" {
    var context = Context{};
    const worker = try Worker.create(std.testing.allocator, .{ .context = &context });
    _ = try worker.submit(1, .open, .{ .block = true }, null);
    try context.entered.timedWait(std.time.ns_per_s);
    for (0..7) |_| _ = try worker.submit(1, .start, null, 0);
    try std.testing.expectError(error.QueueFull, worker.submit(1, .start, null, null));
    context.release.set();
    try expect((try result(worker)).outcome == .ok);
    for (0..7) |_| try expect((try result(worker)).outcome == .expired);
    try finish(worker);
}
test "exit cancels queued work; failed cleanup retains worker until explicit retry" {
    var context = Context{};
    context.fail_stop.store(true, .release);
    const worker = try Worker.create(std.testing.allocator, .{ .context = &context });
    _ = try worker.submit(1, .open, .{ .block = true }, null);
    try context.entered.timedWait(std.time.ns_per_s);
    _ = try worker.submit(1, .start, null, null);
    worker.requestExit();
    try std.testing.expectError(error.Closing, worker.submit(1, .open, .{}, null));
    context.release.set();
    try expect((try result(worker)).outcome == .cancelled);
    try expect((try result(worker)).outcome == .cancelled);
    var timer = try std.time.Timer.start();
    while (worker.snapshot().phase != .cleanup_failed and timer.read() < std.time.ns_per_s) std.time.sleep(std.time.ns_per_ms);
    try std.testing.expectEqual(@as(?anyerror, error.DriverStop), worker.snapshot().failure);
    try expect(!worker.tryDestroy() and context.closes == 0);
    context.fail_stop.store(false, .release);
    try finish(worker);
    try expect(context.closes == 1);
}
test "in-flight deadline returns expired and cleans acquired resources" {
    var context = Context{};
    const worker = try Worker.create(std.testing.allocator, .{ .context = &context });
    _ = try worker.submit(1, .open, .{ .block = true }, 100);
    try context.entered.timedWait(std.time.ns_per_s);
    std.time.sleep(120 * std.time.ns_per_ms);
    try expect(!worker.tryDestroy());
    context.release.set();
    try expect((try result(worker)).outcome == .expired);
    try finish(worker);
    try expect(context.opens == 1 and context.closes == 1);
}
test "ASIO production adapter compiles with worker-owned config" {
    std.testing.refAllDeclsRecursive(asio);
    var config = try asio.Backend.Config.init("C:/isolated/NSP.AsioOutput.dll", "asio:test");
    try expect(config.source_channels == 12 and config.sources[11] == 11);
    try config.setOutputOrder("dvs-speakers");
    try expect(config.sources[7] == 3 and config.sources[8] == 10);
    try config.setOutputOrder("olr");
    try expect(config.sources[7] == 7 and config.sources[8] == 8);
}
test "actual ASIO ABI lifecycle stays on owner worker (fake DLL only)" {
    const path = std.process.getEnvVarOwned(std.testing.allocator, "NSP_ASIO_CONTROL_TEST_DLL") catch return error.SkipZigTest;
    defer std.testing.allocator.free(path);
    const worker = try asio.Worker.create(std.testing.allocator, .{});
    var config = try asio.Backend.Config.init(path, "asio:test");
    // Keep API flags compatible with the fixture; no PCM is submitted in N05.
    config.clock_sync = false;
    const ops = [_]ctl.Operation{ .open, .start, .stop, .close };
    for (ops) |op| {
        const id = try worker.submit(1, op, if (op == .open) config else null, null);
        var timer = try std.time.Timer.start();
        const r = while (true) {
            if (worker.poll()) |r| break r;
            if (timer.read() > 3 * std.time.ns_per_s) return error.AsioCommandTimeout;
            std.time.sleep(std.time.ns_per_ms);
        };
        try expect(r.id == id and r.outcome == .ok);
    }
    config.source_channels = 0;
    _ = try worker.submit(1, .open, config, null);
    var error_timer = try std.time.Timer.start();
    const failed = while (true) {
        if (worker.poll()) |r| break r;
        if (error_timer.read() > 3 * std.time.ns_per_s) return error.AsioCommandTimeout;
        std.time.sleep(std.time.ns_per_ms);
    };
    try expect(failed.outcome == .failed);
    try std.testing.expectEqual(@as(?anyerror, error.InvalidAsioInput), failed.failure);
    try expect(std.mem.sliceTo(&failed.detail, 0).len > 0);
    worker.requestExit();
    var timer = try std.time.Timer.start();
    while (!worker.tryDestroy()) {
        if (timer.read() > 3 * std.time.ns_per_s) return error.AsioExitTimeout;
        std.time.sleep(std.time.ns_per_ms);
    }
}
