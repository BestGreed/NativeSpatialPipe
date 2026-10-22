//! Reliable Rust pool leases -> device owner. Descriptor-only; no PCM storage.
const std = @import("std");
pub const State = enum { waiting, playing, rebuffering, draining, done, faulted };
pub const Status = struct {
    state: State = .waiting,
    received_frames: u64 = 0,
    submitted_frames: u64 = 0,
    callbacks: u64 = 0,
    starvation_frames: u64 = 0,
    rebuffers: u32 = 0,
    device_sample_rate: f64 = 0,
    device_buffer_frames: u32 = 0,
    buffered_source_frames: u64 = 0,
    source_frame: f64 = 0,
    calibration_max_frames: u32 = 0,
    lookahead_frames: u32 = 0,
    prebuffer_frames: u32 = 0,
    rebuffer_wait_ns: u64 = 0,
    failure: ?anyerror = null,
    dsp_bypassed: bool = true,
    dsp_tail_frames: u64 = 0,
    pool_blocks_received: u64 = 0,
    pool_blocks_released: u64 = 0,
};
pub const Stream = struct {
    pub const frames = 480;
    pub const channels = 12;
    pub const slots = 32;
    pub const samples = frames * channels;
    pub const Release = *const fn (?*anyopaque) callconv(.C) void;
    pub const Lease = struct {
        pcm: [*]const f32,
        frames: u32,
        token: ?*anyopaque,
        release_fn: Release,
    };
    allocator: std.mem.Allocator,
    mutex: std.Thread.Mutex = .{},
    leases: [slots]Lease = undefined,
    head: usize = 0,
    count: usize = 0,
    eof: bool = false,
    status: Status = .{},
    processor: ?@import("output_processing.zig").Processor = null,
    pool_library: ?std.DynLib = null,
    tail_started: ?std.time.Instant = null,
    tail_timeout_ms: u32 = 5000,
    pub fn create(allocator: std.mem.Allocator) !*Stream {
        const self = try allocator.create(Stream);
        self.* = .{ .allocator = allocator };
        return self;
    }
    pub fn configure(self: *Stream, config: @import("output_processing.zig").Config) !void {
        if (self.processor != null) return error.OutputAlreadyConfigured;
        self.processor = try @import("output_processing.zig").Processor.init(self.allocator, config);
        self.status.dsp_bypassed = config.bypass or config.neutral();
    }
    pub fn timing(self: *const Stream) @import("output_processing.zig").Timing {
        return if (self.processor) |*processor| processor.timing() else .{ .calibration_max_frames=0, .lookahead_frames=0, .tail_frames=0 };
    }
    /// Pin release callback code until the last outstanding pool lease is returned.
    /// Pipeline teardown may precede device drain; leases never retain Pipeline.
    pub fn holdLibrary(self: *Stream, path: []const u8) !void {
        if (self.pool_library != null) return error.OutputAlreadyBound;
        self.pool_library = try std.DynLib.open(path);
    }
    /// Only after renderer finish and output worker join.
    pub fn destroy(self: *Stream) void {
        self.clearLeases();
        if (self.processor) |*processor| processor.deinit();
        if (self.pool_library) |*lib| lib.close();
        const allocator = self.allocator;
        allocator.destroy(self);
    }
    pub fn pushLease(self: *Stream, first: u64, lease: Lease, tail: bool) !void {
        self.mutex.lock();
        defer self.mutex.unlock();
        if (self.eof or self.status.state == .faulted or self.status.state == .done) return error.OutputClosed;
        if (lease.frames == 0 or lease.frames > frames or first != self.status.received_frames + self.status.dsp_tail_frames or
            (!tail and self.status.dsp_tail_frames != 0) or first > std.math.maxInt(u64) - @as(u64,lease.frames)) return error.OutputDiscontinuity;
        if (self.count == slots) return error.OutputQueueFull;
        self.leases[(self.head + self.count) % slots] = lease;
        self.count += 1;
        if (tail) self.status.dsp_tail_frames += lease.frames else self.status.received_frames += lease.frames;
        self.status.pool_blocks_received += 1;
    }
    pub fn popLease(self: *Stream) ?Lease {
        self.mutex.lock();
        defer self.mutex.unlock();
        if (self.count == 0) return null;
        const lease = self.leases[self.head];
        self.head = (self.head + 1) % slots;
        self.count -= 1;
        return lease;
    }
    pub fn releaseLease(self: *Stream, lease: Lease) void {
        lease.release_fn(lease.token);
        self.mutex.lock();
        defer self.mutex.unlock();
        self.status.pool_blocks_released += 1;
    }
    fn clearLeases(self: *Stream) void {
        while (self.popLease()) |lease| self.releaseLease(lease);
    }
    /// Diagnostic consumer only. Live ASIO uses popLease, without copying PCM.
    pub fn pop(self: *Stream, dest: *[samples]f32) bool {
        const lease = self.popLease() orelse return false;
        defer self.releaseLease(lease);
        @memcpy(dest[0..lease.frames * channels], lease.pcm[0..lease.frames * channels]);
        return true;
    }
    pub fn finish(self: *Stream) void {
        self.mutex.lock();
        defer self.mutex.unlock();
        self.eof = true;
    }
    pub fn ended(self: *Stream) bool {
        self.mutex.lock();
        defer self.mutex.unlock();
        return self.eof and self.count == 0;
    }
    pub fn snapshot(self: *Stream) Status {
        self.mutex.lock();
        defer self.mutex.unlock();
        return self.status;
    }
    pub fn update(self: *Stream, state: State, submitted: u64, callbacks: u64, starvation: u64, rebuffers: u32) void {
        self.mutex.lock();
        defer self.mutex.unlock();
        if (self.status.state == .faulted) return;
        self.status.state = state;
        self.status.submitted_frames = submitted;
        self.status.callbacks = callbacks;
        self.status.starvation_frames = starvation;
        self.status.rebuffers = rebuffers;
    }
    pub fn fail(self: *Stream, failure: anyerror) void {
        self.mutex.lock();
        defer self.mutex.unlock();
        self.status.state = .faulted;
        if (self.status.failure == null) self.status.failure = failure;
        // Return all queued pool blocks immediately on abort. The owner returns
        // any block currently in submit separately, exactly once.
        while (self.count > 0) {
            const lease = self.leases[self.head];
            self.head = (self.head + 1) % slots;self.count -= 1;
            lease.release_fn(lease.token);self.status.pool_blocks_released += 1;
        }
    }
    pub fn metrics(self: *Stream, rate: f64, buffer: u32, available: u64, source: f64, calibration: u32, lookahead: u32, prebuffer: u32, rebuffer_wait_ns: u64) void {
        self.mutex.lock();
        defer self.mutex.unlock();
        self.status.device_sample_rate = rate;
        self.status.device_buffer_frames = buffer;
        self.status.buffered_source_frames = available;
        self.status.source_frame = source;
        self.status.calibration_max_frames = calibration;
        self.status.lookahead_frames = lookahead;
        self.status.prebuffer_frames = prebuffer;
        self.status.rebuffer_wait_ns = rebuffer_wait_ns;
    }
};
/// Runs on the Rust output writer while it exclusively owns the PCM block.
/// op=0 processes in place; op=1 drains DSP tail into another pool block.
pub fn processPool(context: ?*anyopaque, op: u32, pcm: [*]f32, capacity: u32, channels: u32, written: *u32) callconv(.C) c_int {
    const stream: *Stream = @ptrCast(@alignCast(context orelse return -22));
    if (capacity == 0 or capacity > Stream.frames or channels != Stream.channels or op > 1) return -22;
    if (stream.snapshot().failure != null) return -1;
    if (op == 1 and stream.tail_started == null) stream.tail_started = std.time.Instant.now() catch {stream.fail(error.OutputTailClock);return -1;};
    written.* = if (op == 0) capacity else 0;
    if (stream.processor) |*processor| {
        if (op == 0) {
            processor.process(pcm[0..capacity*channels]) catch |err| {stream.fail(err);return -1;};
        } else {
            processor.finish();
            written.* = processor.drain(pcm[0..capacity*channels]) catch |err| {stream.fail(err);return -1;};
        }
    }
    return 0;
}
/// On success ownership transfers to Stream. On failure Rust retains the token.
pub fn publishPool(context: ?*anyopaque, first: u64, pcm: [*]const f32, frames: u32, channels: u32, tail: u32,
    token: ?*anyopaque, release_fn: Stream.Release) callconv(.C) c_int {
    const stream: *Stream = @ptrCast(@alignCast(context orelse return -22));
    if (channels != Stream.channels or tail > 1 or token == null) {stream.fail(error.OutputFormat);return -22;}
    while (true) {
        stream.pushLease(first, .{.pcm=pcm,.frames=frames,.token=token,.release_fn=release_fn}, tail == 1) catch |err| {
            // Only EOF drain may wait: a long calibration/lookahead tail can
            // exceed the descriptor ring. Live queue overflow remains fatal.
            if (err == error.OutputQueueFull and tail == 1) {
                const now=std.time.Instant.now() catch {stream.fail(error.OutputTailClock);return -1;};
                const start=stream.tail_started orelse now;
                if(now.since(start) >= @as(u64,stream.tail_timeout_ms)*std.time.ns_per_ms) {
                    stream.fail(error.OutputTailTimeout);return -1;
                }
                std.time.sleep(std.time.ns_per_ms);continue;
            }
            stream.fail(err);return -22;
        };
        return 0;
    }
}
