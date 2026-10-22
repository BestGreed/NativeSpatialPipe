//! One backend owner thread. No endpoint polling or session ownership decisions.
const std = @import("std");
pub const Operation = enum { open, start, stop, close };
pub const Outcome = enum { ok, failed, stale, expired, cancelled };
pub const Phase = enum { idle, executing, faulted, cleanup_failed, exited };
pub const Result = struct { id: u64, generation: u64, operation: Operation, outcome: Outcome, failure: ?anyerror = null, detail: [256:0]u8 = std.mem.zeroes([256:0]u8) };
pub const Snapshot = struct { phase: Phase, generation: u64, owned_generation: ?u64, outstanding: usize, closing: bool, failure: ?anyerror, detail: [256:0]u8 };

/// Backend.Config must own its data. Backend methods run ONLY on this worker.
/// The returned allocation must stay alive until tryDestroy succeeds. A blocked
/// driver cannot be cancelled safely; callers time out without freeing it.
pub fn Worker(comptime Backend: type) type {
    return struct {
        const Self = @This();
        const capacity = 8;
        const Command = struct { id: u64, generation: u64, op: Operation, config: ?Backend.Config, timeout_ms: ?u32, submitted_at: std.time.Instant };
        allocator: std.mem.Allocator,
        backend: Backend,
        thread: std.Thread = undefined,
        mutex: std.Thread.Mutex = .{},
        wake: std.Thread.Condition = .{},
        commands: [capacity]Command = undefined,
        results: [capacity]Result = undefined,
        head: usize = 0,
        count: usize = 0,
        result_head: usize = 0,
        result_count: usize = 0,
        outstanding: usize = 0,
        next_id: u64 = 1,
        generation: u64 = 1,
        owned: ?u64 = null,
        started: bool = false,
        phase: Phase = .idle,
        closing: bool = false,
        retry_cleanup: bool = false,
        failure: ?anyerror = null,
        detail: [256:0]u8 = std.mem.zeroes([256:0]u8),

        pub fn create(allocator: std.mem.Allocator, backend: Backend) !*Self {
            const self = try allocator.create(Self);
            errdefer allocator.destroy(self);
            self.* = .{ .allocator = allocator, .backend = backend };
            self.thread = try std.Thread.spawn(.{}, run, .{self});
            return self;
        }
        pub fn setGeneration(self: *Self, generation: u64) !void {
            self.mutex.lock();
            defer self.mutex.unlock();
            if (self.closing) return error.Closing;
            if (generation <= self.generation) return error.GenerationMustIncrease;
            self.generation = generation;
            self.wake.signal();
        }
        pub fn submit(self: *Self, generation: u64, op: Operation, config: ?Backend.Config, timeout_ms: ?u32) !u64 {
            self.mutex.lock();
            defer self.mutex.unlock();
            if (self.closing) return error.Closing;
            if (generation != self.generation) return error.StaleGeneration;
            if ((op == .open) != (config != null)) return error.InvalidConfig;
            if (self.outstanding == capacity) return error.QueueFull;
            if (self.next_id == std.math.maxInt(u64)) return error.CommandIdExhausted;
            const id = self.next_id;
            self.next_id += 1;
            self.commands[(self.head + self.count) % capacity] = .{ .id = id, .generation = generation, .op = op, .config = config, .timeout_ms = timeout_ms, .submitted_at = try std.time.Instant.now() };
            self.count += 1;
            self.outstanding += 1;
            self.wake.signal();
            return id;
        }
        pub fn poll(self: *Self) ?Result {
            self.mutex.lock();
            defer self.mutex.unlock();
            if (self.result_count == 0) return null;
            const result = self.results[self.result_head];
            self.result_head = (self.result_head + 1) % capacity;
            self.result_count -= 1;
            self.outstanding -= 1;
            return result;
        }
        pub fn snapshot(self: *Self) Snapshot {
            self.mutex.lock();
            defer self.mutex.unlock();
            return .{ .phase = self.phase, .generation = self.generation, .owned_generation = self.owned, .outstanding = self.outstanding, .closing = self.closing, .failure = self.failure, .detail = self.detail };
        }
        /// Also explicitly retries previously failed shutdown. Does not wait.
        pub fn requestExit(self: *Self) void {
            self.mutex.lock();
            defer self.mutex.unlock();
            self.closing = true;
            self.retry_cleanup = true;
            self.wake.signal();
        }
        pub fn tryDestroy(self: *Self) bool {
            self.mutex.lock();
            const exited = self.phase == .exited;
            self.mutex.unlock();
            if (!exited) return false;
            self.thread.join();
            const allocator = self.allocator;
            allocator.destroy(self);
            return true;
        }
        fn publish(self: *Self, result: Result) void {
            // Reservation at submit covers queued + executing + unread results.
            self.results[(self.result_head + self.result_count) % capacity] = result;
            self.result_count += 1;
        }
        fn errorDetail(self: *Self, failure: ?anyerror) [256:0]u8 {
            var detail = std.mem.zeroes([256:0]u8);
            if (failure) |err| {
                const text = if (@hasDecl(Backend, "errorDetail")) self.backend.errorDetail(err) else @errorName(err);
                const len = @min(text.len, 256);
                @memcpy(detail[0..len], text[0..len]);
            }
            return detail;
        }
        // Called with mutex held; driver calls must release it.
        fn cleanup(self: *Self) bool {
            if (self.owned == null) return true;
            self.phase = .executing;
            self.mutex.unlock();
            const err: ?anyerror = blk: {
                self.backend.stop() catch |e| break :blk e;
                // Only this worker accesses started while outside mutex.
                self.started = false;
                self.backend.close() catch |e| break :blk e;
                break :blk null;
            };
            self.mutex.lock();
            self.failure = err;
            self.detail = self.errorDetail(err);
            if (err != null) {
                self.phase = .cleanup_failed;
                return false;
            }
            self.owned = null;
            self.phase = .idle;
            return true;
        }
        fn invoke(self: *Self, cmd: Command) !void {
            switch (cmd.op) {
                .open => {
                    if (self.owned != null) return error.BackendAlreadyOpen;
                    try self.backend.open(cmd.config.?);
                },
                .start => {
                    if (self.owned == null) return error.BackendNotOpen;
                    try self.backend.start();
                },
                .stop => {
                    if (self.owned == null) return error.BackendNotOpen;
                    try self.backend.stop();
                },
                .close => {
                    if (self.started) return error.StopBeforeClose;
                    try self.backend.close();
                },
            }
        }
        fn expired(cmd: Command) bool {
            const timeout = cmd.timeout_ms orelse return false;
            const now = std.time.Instant.now() catch return true;
            return now.since(cmd.submitted_at) >= @as(u64, timeout) * std.time.ns_per_ms;
        }
        fn run(self: *Self) void {
            self.mutex.lock();
            defer self.mutex.unlock();
            while (true) {
                if (self.closing) {
                    while (self.count > 0) {
                        const cmd = self.commands[self.head];
                        self.head = (self.head + 1) % capacity;
                        self.count -= 1;
                        self.publish(.{ .id = cmd.id, .generation = cmd.generation, .operation = cmd.op, .outcome = .cancelled });
                    }
                    if (self.retry_cleanup) {
                        self.retry_cleanup = false;
                        if (self.cleanup()) {
                            self.phase = .exited;
                            return;
                        }
                    }
                    self.wake.wait(&self.mutex);
                    continue;
                }
                if (self.owned != null and self.owned.? != self.generation and self.phase != .cleanup_failed)
                    _ = self.cleanup();
                if (self.count == 0) {
                    if (@hasDecl(Backend, "pump")) {
                        if (self.started and self.phase == .idle and self.owned == self.generation) {
                            self.phase = .executing;
                            self.mutex.unlock();
                            const failure: ?anyerror = blk: {
                                self.backend.pump() catch |e| break :blk e;
                                break :blk null;
                            };
                            self.mutex.lock();
                            self.phase = .idle;
                            if (failure) |err| {
                                self.failure = err;
                                self.detail = self.errorDetail(err);
                                self.phase = .faulted;
                            }
                            // Bounded scheduling wait, not a per-block accumulation target.
                            if (!self.closing and self.count == 0) self.wake.timedWait(&self.mutex, std.time.ns_per_ms) catch {};
                            continue;
                        }
                    }
                    self.wake.wait(&self.mutex);
                    continue;
                }
                const cmd = self.commands[self.head];
                self.head = (self.head + 1) % capacity;
                self.count -= 1;
                var result = Result{ .id = cmd.id, .generation = cmd.generation, .operation = cmd.op, .outcome = .ok };
                if (cmd.generation != self.generation) {
                    result.outcome = .stale;
                    self.publish(result);
                    continue;
                }
                if (expired(cmd)) {
                    result.outcome = .expired;
                    self.publish(result);
                    continue;
                }
                // A failed cleanup retains old resources; never start a new generation over them.
                if ((self.phase == .cleanup_failed or self.phase == .faulted) and cmd.op != .stop and cmd.op != .close) {
                    result.outcome = .failed;
                    result.failure = error.CleanupRequired;
                    self.publish(result);
                    continue;
                }
                self.phase = .executing;
                self.mutex.unlock();
                const failure: ?anyerror = blk: {
                    self.invoke(cmd) catch |e| break :blk e;
                    break :blk null;
                };
                self.mutex.lock();
                if (failure == null) switch (cmd.op) {
                    .open => {
                        self.owned = cmd.generation;
                    },
                    .start => {
                        self.started = true;
                    },
                    .stop => {
                        self.started = false;
                    },
                    .close => {
                        self.owned = null;
                    },
                };
                self.failure = failure;
                self.detail = self.errorDetail(failure);
                self.phase = if (failure != null and (cmd.op == .stop or cmd.op == .close)) .cleanup_failed else .idle;
                result.failure = failure;
                result.detail = self.detail;
                if (failure != null) result.outcome = .failed;
                if (self.closing or cmd.generation != self.generation) {
                    result.outcome = if (self.closing) .cancelled else .stale;
                    _ = self.cleanup();
                } else if (expired(cmd)) {
                    result.outcome = .expired;
                    _ = self.cleanup();
                }
                self.publish(result);
            }
        }
    };
}
