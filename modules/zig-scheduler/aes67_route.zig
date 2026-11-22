//! Active AES67 hand-off: one Rust writer per input and one AES67 owner as
//! the only consumer. The Bridge thread publishes the elected input and epoch.
const std = @import("std");
const Stream = @import("output_stream.zig").Stream;
const Atomic = std.atomic.Value;
const no_source = std.math.maxInt(u8);
const slots = 8;

const Entry = struct { epoch: u64, lease: Stream.Lease };

pub const Input = struct {
    route: *Route,
    source: u8,
    entries: [slots]Entry = undefined,
    written: Atomic(usize) = Atomic(usize).init(0),
    read: Atomic(usize) = Atomic(usize).init(0),
    received: Atomic(u64) = Atomic(u64).init(0),
    released: Atomic(u64) = Atomic(u64).init(0),
    tail_frames: Atomic(u64) = Atomic(u64).init(0),
    queue_dropped_frames: Atomic(u64) = Atomic(u64).init(0),
    // Writer-only continuity tracking, including blocks released immediately.
    next_frame: u64 = 0,
    tail_seen: bool = false,

    fn push(self: *Input, entry: Entry) bool {
        const written = self.written.load(.monotonic);
        if (written -% self.read.load(.acquire) == slots) return false;
        self.entries[written % slots] = entry;
        self.written.store(written +% 1, .release);
        return true;
    }

    fn peek(self: *const Input) ?Entry {
        const read = self.read.load(.monotonic);
        if (read == self.written.load(.acquire)) return null;
        return self.entries[read % slots];
    }

    fn advance(self: *Input) void {
        self.read.store(self.read.load(.monotonic) +% 1, .release);
    }

    fn empty(self: *const Input) bool {
        return self.read.load(.monotonic) == self.written.load(.acquire);
    }

    pub fn releaseLease(self: *Input, lease: Stream.Lease) void {
        lease.release_fn(lease.token);
        _ = self.released.fetchAdd(1, .acq_rel);
    }

    pub fn popSelected(self: *Input, epoch: u64) ?Stream.Lease {
        for (0..slots) |_| {
            const entry = self.peek() orelse return null;
            if (self.route.requested.load(.acquire) != epoch) return null;
            self.advance();
            if (entry.epoch == epoch) return entry.lease;
            self.releaseLease(entry.lease);
        }
        return null;
    }

    fn discard(self: *Input, keep_requested: bool) void {
        for (0..slots) |_| {
            const entry = self.peek() orelse break;
            // A new election may have arrived since the owner sampled it.
            // Preserve new-generation blocks until the owner catches up.
            if (keep_requested and entry.epoch == self.route.requested.load(.acquire)) break;
            self.advance();
            self.releaseLease(entry.lease);
        }
    }
};

pub const Stats = struct { received: u64 = 0, released: u64 = 0, tail_frames: u64 = 0, queue_dropped_frames: u64 = 0 };

pub const Route = struct {
    inputs: [4]Input = undefined,
    channels: u32,
    drain_timeout_ms: u32,
    // Low byte encodes source, upper bits change at each election transition.
    // Tagging descriptors also rejects a late publication across A -> B -> A.
    requested: Atomic(u64) = Atomic(u64).init(no_source),
    finishing: Atomic(bool) = Atomic(bool).init(false),
    failed: Atomic(bool) = Atomic(bool).init(false),

    pub fn init(self: *Route, channels: u32, drain_timeout_ms: u32) void {
        self.* = .{ .channels = channels, .drain_timeout_ms = drain_timeout_ms };
        for (&self.inputs, 0..) |*input, index| input.* = .{ .route = self, .source = @intCast(index) };
    }

    // Only the Bridge calls select. The owner independently applies the request.
    pub fn select(self: *Route, source: ?usize) void {
        const id: u8 = if (source) |index| @intCast(index) else no_source;
        const previous = self.requested.load(.monotonic);
        if (@as(u8, @truncate(previous)) == id) return;
        self.requested.store(((previous +% 256) & ~@as(u64, 255)) | id, .release);
    }

    pub fn selected(epoch: u64) ?u32 {
        const index: u8 = @truncate(epoch);
        return if (index == no_source) null else index;
    }

    pub fn discardInactive(self: *Route, selected_source: ?u32) void {
        for (&self.inputs, 0..) |*input, index| {
            if (selected_source != @as(u32, @intCast(index))) input.discard(true);
        }
    }

    pub fn discardAll(self: *Route) void {
        for (&self.inputs) |*input| input.discard(false);
    }

    pub fn ended(self: *const Route) bool {
        if (!self.finishing.load(.acquire)) return false;
        for (&self.inputs) |*input| if (!input.empty()) return false;
        return true;
    }

    pub fn stats(self: *const Route) Stats {
        var totals = Stats{};
        for (&self.inputs) |*input| {
            // Read release first so a concurrent push cannot report more
            // released than received for that source.
            totals.released += input.released.load(.acquire);
            totals.received += input.received.load(.monotonic);
            totals.tail_frames += input.tail_frames.load(.monotonic);
            totals.queue_dropped_frames += input.queue_dropped_frames.load(.monotonic);
        }
        return totals;
    }
};

/// A successful callback owns the token even when dropping the block. Rust
/// permits synchronous release, and only Rust releases a rejected publication.
pub fn publishPool(context: ?*anyopaque, first: u64, pcm: [*]const f32, frames: u32, channels: u32, tail: u32, token: ?*anyopaque, release_fn: Stream.Release) callconv(.C) c_int {
    const input: *Input = @ptrCast(@alignCast(context orelse return -22));
    const route = input.route;
    if (channels != route.channels or tail > 1 or token == null or frames == 0 or frames > Stream.frames or
        first != input.next_frame or (tail == 0 and input.tail_seen) or first > std.math.maxInt(u64) - @as(u64, frames))
    {
        route.failed.store(true, .release);
        return -22;
    }
    input.next_frame += frames;
    input.tail_seen = tail == 1;
    _ = input.received.fetchAdd(1, .monotonic);
    if (tail == 1) _ = input.tail_frames.fetchAdd(frames, .monotonic);
    const lease: Stream.Lease = .{ .pcm = pcm, .frames = frames, .token = token, .release_fn = release_fn };
    const epoch = route.requested.load(.acquire);
    var timer: ?std.time.Timer = null;
    while (!route.failed.load(.acquire) and epoch == route.requested.load(.acquire) and Route.selected(epoch) == input.source) {
        if (input.push(.{ .epoch = epoch, .lease = lease })) return 0;
        // Only a selected EOF tail may wait, as on the existing fixed path.
        if (tail == 0) break;
        if (timer == null) timer = std.time.Timer.start() catch break;
        if (timer.?.read() >= @as(u64, route.drain_timeout_ms) * std.time.ns_per_ms) break;
        std.time.sleep(std.time.ns_per_ms);
    }
    if (Route.selected(epoch) == input.source and epoch == route.requested.load(.acquire) and !route.failed.load(.acquire))
        _ = input.queue_dropped_frames.fetchAdd(frames, .monotonic);
    input.releaseLease(lease);
    return 0;
}

fn countRelease(token: ?*anyopaque) callconv(.C) void {
    const calls: *usize = @ptrCast(@alignCast(token.?));
    calls.* += 1;
}

test "active route releases inactive input and rejects stale descriptors across A-B-A" {
    var route: Route = undefined;
    route.init(2, 20);
    const pcm = [_]f32{ 0.25, -0.25 };
    var releases = [_]usize{0} ** 5;
    try std.testing.expectEqual(@as(c_int, 0), publishPool(&route.inputs[0], 0, &pcm, 1, 2, 0, &releases[0], countRelease));
    try std.testing.expectEqual(@as(usize, 1), releases[0]);
    route.select(0);
    try std.testing.expectEqual(@as(c_int, 0), publishPool(&route.inputs[0], 1, &pcm, 1, 2, 0, &releases[1], countRelease));
    route.select(1);
    try std.testing.expectEqual(@as(c_int, 0), publishPool(&route.inputs[1], 0, &pcm, 1, 2, 0, &releases[2], countRelease));
    route.select(0);
    try std.testing.expectEqual(@as(c_int, 0), publishPool(&route.inputs[0], 2, &pcm, 1, 2, 0, &releases[3], countRelease));
    route.discardInactive(1); // Stale owner snapshot must preserve current queue.
    try std.testing.expectEqual(@as(usize, 1), releases[1]);
    try std.testing.expectEqual(@as(usize, 0), releases[3]);
    route.discardInactive(0);
    try std.testing.expectEqual(@as(usize, 1), releases[2]);
    const epoch = route.requested.load(.acquire);
    const fresh = route.inputs[0].popSelected(epoch).?;
    try std.testing.expect(fresh.token == @as(*anyopaque, &releases[3]));
    route.inputs[0].releaseLease(fresh);
    // Inactive DSP tail is returned synchronously and carries no session EOF.
    try std.testing.expectEqual(@as(c_int, 0), publishPool(&route.inputs[1], 1, &pcm, 1, 2, 1, &releases[4], countRelease));
    try std.testing.expect(!route.ended());
    route.finishing.store(true, .release);
    try std.testing.expect(route.ended());
    try std.testing.expectEqualSlices(usize, &([_]usize{1} ** releases.len), &releases);
    try std.testing.expectEqualDeep(Stats{ .received = 5, .released = 5, .tail_frames = 1 }, route.stats());
}

test "saturated live input returns the extra lease without waiting" {
    var route: Route = undefined;
    route.init(2, 20);
    route.select(1);
    const pcm = [_]f32{ 0.25, -0.25 };
    var releases: usize = 0;
    for (0..slots + 1) |i| {
        try std.testing.expectEqual(@as(c_int, 0), publishPool(&route.inputs[1], i, &pcm, 1, 2, 0, &releases, countRelease));
    }
    try std.testing.expectEqual(@as(usize, 1), releases);
    try std.testing.expectEqual(@as(u64, 1), route.stats().queue_dropped_frames);
    route.discardAll();
    try std.testing.expectEqual(@as(usize, slots + 1), releases);
}
