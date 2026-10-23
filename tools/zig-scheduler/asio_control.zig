//! ASIO owner adapter. Submits borrowed Rust pool blocks; no PCM staging copy.
const std = @import("std");
const processing = @import("output_processing.zig");
pub const Stream = @import("output_stream.zig").Stream;
const c = @cImport({
    @cDefine("NSP_ASIO_STATIC", "1");
    @cInclude("asio_output_c.h");
});
pub const Backend = struct {
    pub const Config = struct {
        dll_path: [1024:0]u8 = std.mem.zeroes([1024:0]u8),
        driver_id: [4095:0]u8 = std.mem.zeroes([4095:0]u8),
        source_channels: u32 = 12,
        output_channels: u32 = 12,
        sources: [32]u32 = [_]u32{0} ** 32,
        physical: [32]i32 = [_]i32{0} ** 32,
        buffer_frames: i32 = 0,
        sample_rate: f64 = 48000,
        clock_sync: bool = true,
        stream: ?*Stream = null,
        processing: processing.Config = .{},
        prebuffer_frames: u32 = 0,
        // Legacy playback-file controls, accepted but inactive for session output.
        startup_timeout_ms: u32 = 0,
        rebuffer_timeout_ms: u32 = 0,
        max_rebuffers: u32 = 0,
        drain_timeout_ms: u32 = 5000,
        pub fn setOutputOrder(self: *Config, name: []const u8) !void {
            if (self.source_channels != 12 or self.output_channels != 12) return error.OutputOrderRequiresTwelveChannels;
            const indices = try @import("output_layout.zig").sources(name);
            @memcpy(self.sources[0..12], &indices);
        }
        pub fn init(path: []const u8, driver: []const u8) !Config {
            if (path.len == 0 or path.len > 1024 or driver.len == 0 or driver.len > 4095 or
                std.mem.indexOfScalar(u8, path, 0) != null or std.mem.indexOfScalar(u8, driver, 0) != null) return error.InvalidPathOrDriver;
            var config = Config{};
            @memcpy(config.dll_path[0..path.len], path);
            @memcpy(config.driver_id[0..driver.len], driver);
            for (0..32) |i| {
                config.sources[i] = @intCast(i);
                config.physical[i] = @intCast(i);
            }
            return config;
        }
    };
    dll: ?std.DynLib = null,
    handle: c.nsp_asio_handle = null,
    open_fn: *const @TypeOf(c.nsp_asio_open) = undefined,
    start_fn: *const @TypeOf(c.nsp_asio_start) = undefined,
    stop_fn: *const @TypeOf(c.nsp_asio_stop) = undefined,
    close_fn: *const @TypeOf(c.nsp_asio_close) = undefined,
    error_fn: *const @TypeOf(c.nsp_asio_last_error) = undefined,
    submit_fn: *const @TypeOf(c.nsp_asio_submit) = undefined,
    prime_fn: *const @TypeOf(c.nsp_asio_prime) = undefined,
    service_fn: *const @TypeOf(c.nsp_asio_service) = undefined,
    finish_fn: *const @TypeOf(c.nsp_asio_finish_input) = undefined,
    stats_fn: *const @TypeOf(c.nsp_asio_get_stats) = undefined,
    hold_fn: *const @TypeOf(c.nsp_asio_hold) = undefined,
    stream: ?*Stream = null,
    limits: Config = .{},
    timer: ?std.time.Timer = null,
    since: u64 = 0,
    state: @import("output_stream.zig").State = .waiting,
    next_frame: u64 = 0,
    last_starved: u64 = 0,
    rebuffers: u32 = 0,
    rebuffer_wait_ns: u64 = 0,
    device_eof: bool = false,
    last_code: i32 = 0,
    message: [1024]u8 = [_]u8{0} ** 1024,
    fn check(self: *Backend, code: i32) !void {
        self.last_code = code;
        if (code == 0) return;
        _ = self.error_fn(&self.message, self.message.len);
        return switch (code) {
            c.NSP_ASIO_INVALID => error.InvalidAsioInput,
            c.NSP_ASIO_STATE => error.AsioState,
            c.NSP_ASIO_NO_MEMORY => error.OutOfMemory,
            else => error.AsioDeviceFailure,
        };
    }
    pub fn errorDetail(self: *Backend, failure: anyerror) []const u8 {
        return switch (failure) {
            error.InvalidAsioInput, error.AsioState, error.OutOfMemory, error.AsioDeviceFailure => std.mem.sliceTo(&self.message, 0),
            else => @errorName(failure),
        };
    }
    pub fn open(self: *Backend, config: Config) !void {
        if (self.dll != null) return error.AsioAlreadyOpen;
        if (config.stream != null and (config.source_channels != config.stream.?.channel_count or config.processing.channels != config.source_channels or
            config.drain_timeout_ms == 0 or config.prebuffer_frames > 48000)) return error.InvalidPlaybackConfig;
        var dll = try @import("native_library.zig").open(std.mem.sliceTo(&config.dll_path, 0));
        errdefer dll.close();
        const version = dll.lookup(*const @TypeOf(c.nsp_asio_abi_version), "nsp_asio_abi_version") orelse return error.MissingAsioExport;
        if (version() != c.NSP_ASIO_ABI_VERSION) return error.AsioAbiMismatch;
        inline for (.{ "open", "start", "stop", "close" }) |name| {
            @field(self, name ++ "_fn") = dll.lookup(*const @TypeOf(@field(c, "nsp_asio_" ++ name)), "nsp_asio_" ++ name) orelse return error.MissingAsioExport;
        }
        self.error_fn = dll.lookup(*const @TypeOf(c.nsp_asio_last_error), "nsp_asio_last_error") orelse return error.MissingAsioExport;
        if (config.stream != null) {
            inline for (.{ .{ "submit_fn", "submit" }, .{ "prime_fn", "prime" }, .{ "service_fn", "service" }, .{ "finish_fn", "finish_input" }, .{ "stats_fn", "get_stats" }, .{ "hold_fn", "hold" } }) |names| {
                @field(self, names[0]) = dll.lookup(*const @TypeOf(@field(c, "nsp_asio_" ++ names[1])), "nsp_asio_" ++ names[1]) orelse return error.MissingAsioExport;
            }
        }
        var input = std.mem.zeroes(c.nsp_asio_config);
        input.struct_size = @sizeOf(c.nsp_asio_config);
        input.abi_version = c.NSP_ASIO_ABI_VERSION;
        input.driver_id_utf8 = &config.driver_id;
        input.source_channels = config.source_channels;
        input.output_channels = config.output_channels;
        input.source_channel_indices = &config.sources;
        input.physical_channels = &config.physical;
        input.device_buffer_frames = config.buffer_frames;
        input.device_sample_rate = config.sample_rate;
        input.flags = if (config.clock_sync) c.NSP_ASIO_CLOCK_SYNC else 0;
        try self.check(self.open_fn(&input, &self.handle));
        self.dll = dll;
        self.stream = config.stream;
        self.limits = config;
        self.next_frame = 0;
        self.rebuffers = 0;
        self.rebuffer_wait_ns = 0;
        self.last_starved = 0;
        self.device_eof = false;
        self.state = .waiting;
    }
    pub fn start(self: *Backend) !void {
        if (self.handle == null) return error.AsioNotOpen;
        try self.check(self.start_fn(self.handle));
        self.timer = try std.time.Timer.start();
        self.since = 0;
    }
    pub fn stop(self: *Backend) !void {
        if (self.handle != null) try self.check(self.stop_fn(self.handle));
    }
    pub fn close(self: *Backend) !void {
        if (self.handle != null) try self.check(self.close_fn(&self.handle));
        if (self.dll) |*dll| dll.close();
        self.dll = null;
        self.stream = null;
    }
    pub fn pump(self: *Backend) !void {
        const stream = self.stream orelse return;
        self.pumpInner(stream) catch |err| {
            stream.fail(err);
            return err;
        };
    }
    fn pumpInner(self: *Backend, stream: *Stream) !void {
        if (self.state == .done) return;
        if (stream.snapshot().failure) |err| return err;
        // Service/get_stats ALWAYS stay on the lifecycle owner thread.
        try self.check(self.service_fn(self.handle));
        var stats = std.mem.zeroes(c.nsp_asio_stats);
        stats.struct_size = @sizeOf(c.nsp_asio_stats);
        try self.check(self.stats_fn(self.handle, &stats));
        const now = self.timer.?.read();
        if (self.state == .playing and !self.device_eof and stats.starvation_frames > self.last_starved) {
            // Source absence is normal for a resident endpoint. Device callbacks
            // keep writing silence while hold prevents consuming absent source.
            // Count gaps diagnostically; they do not terminate the session.
            self.rebuffers +|= 1;
            try self.check(self.hold_fn(self.handle, 1));
            self.state = .rebuffering;
            self.since = now;
        }
        self.last_starved = stats.starvation_frames;
        // Fixed budget per service pass; do not accumulate an unbounded batch.
        for (0..4) |_| {
            const lease = stream.popLease() orelse break;
            defer stream.releaseLease(lease);
            try self.check(self.submit_fn(self.handle, self.next_frame, lease.pcm, lease.frames));
            self.next_frame += lease.frames;
        }
        if (stream.ended() and !self.device_eof) {
            try self.check(self.finish_fn(self.handle));
            self.device_eof = true;
        }
        try self.check(self.stats_fn(self.handle, &stats));
        const timing = stream.timing();
        // Device minimum covers ring/SRC needs. Reserve the next 480-frame
        // source delivery too, so a recovered stream need not arrive just in
        // time for every callback. Explicit thresholds retain their meaning.
        const prebuffer = if (self.limits.prebuffer_frames == 0)
            stats.minimum_prime_frames + Stream.frames
        else
            @max(stats.minimum_prime_frames, self.limits.prebuffer_frames);
        const ready = stats.buffered_source_frames >= prebuffer or self.device_eof;
        if (self.state == .waiting and ready) {
            if (self.next_frame > 0) try self.check(self.prime_fn(self.handle));
            self.state = if (self.device_eof) .draining else .playing;
            self.since = now;
        } else if (self.state == .rebuffering and ready) {
            // Fill the ring while callbacks are still held. Releasing hold on
            // an empty ring would manufacture another starvation before the
            // next owner service pass.
            try self.check(self.service_fn(self.handle));
            try self.check(self.hold_fn(self.handle, 0));
            self.rebuffer_wait_ns += self.timer.?.read() - self.since;
            self.state = if (self.device_eof) .draining else .playing;
            self.since = now;
        } else if (self.device_eof and self.state == .playing) {
            self.state = .draining;
            self.since = now;
        }
        if (self.device_eof and self.state == .draining and stats.drained != 0) self.state = .done;
        const wait_ns = self.rebuffer_wait_ns + if (self.state == .rebuffering) now - self.since else @as(u64,0);
        stream.metrics(stats.device_sample_rate, stats.device_buffer_frames, stats.buffered_source_frames,
            stats.source_frame, timing.calibration_max_frames, timing.lookahead_frames,
            prebuffer, wait_ns);
        // Only explicit session termination drains and has a bounded deadline.
        // Initial wait and later gaps never mean EOF or device stop.
        if (self.state == .draining and now - self.since >= @as(u64, self.limits.drain_timeout_ms) * std.time.ns_per_ms) return error.OutputTimeout;
        stream.update(self.state, self.next_frame, stats.callbacks, stats.starvation_frames, self.rebuffers);
    }
};
pub const Worker = @import("output_control.zig").Worker(Backend);
