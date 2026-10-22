//! Shared interleaved PCM processing, independent of renderer and device.
const std = @import("std");
pub const Config = struct {
    channels: u32 = 12,
    sample_rate: u32 = 48000,
    master: f64 = 1,
    trim: [32]f64 = [_]f64{1} ** 32,
    delay_frames: [32]u32 = [_]u32{0} ** 32,
    limiter: bool = false,
    ceiling: f64 = 1,
    release_ms: f64 = 50,
    lookahead_frames: u32 = 0,
    bypass: bool = false,
    pub fn neutral(config: Config) bool {
        if (config.master != 1 or config.limiter) return false;
        for (0..config.channels) |ch| if (config.trim[ch] != 1 or config.delay_frames[ch] != 0) return false;
        return true;
    }
    pub fn validate(config: Config) !void {
        if (config.channels == 0 or config.channels > 32 or config.sample_rate != 48000 or
            !std.math.isFinite(config.master) or config.master < 0 or config.master > 16 or
            !std.math.isFinite(config.ceiling) or config.ceiling <= 0 or config.ceiling > 1 or
            !std.math.isFinite(config.release_ms) or config.release_ms < 0 or config.release_ms > 10000 or
            config.lookahead_frames > 4800) return error.InvalidOutputConfig;
        for (0..config.channels) |ch| {
            if (!std.math.isFinite(config.trim[ch]) or config.trim[ch] < 0 or config.trim[ch] > 16 or
                config.delay_frames[ch] > 48000) return error.InvalidOutputConfig;
        }
    }
};
pub const Timing = struct { calibration_max_frames: u32, lookahead_frames: u32, tail_frames: u32 };
pub const Processor = struct {
    const Peak = struct { index: u64, value: f64 };
    allocator: std.mem.Allocator,
    config: Config, // Immutable for this instance; reconfiguration needs a new stream instance.
    delay: []f32,
    pending: []f32,
    peaks: []Peak,
    max_delay: u32,
    lookahead: u32,
    frame: u64 = 0,
    peak_head: usize = 0,
    peak_count: usize = 0,
    envelope: f64 = 1,
    release: f64,
    finishing: bool = false,
    tail_left: u32 = 0,
    has_input: bool = false,

    pub fn init(allocator: std.mem.Allocator, config: Config) !Processor {
        try config.validate();
        var max_delay: u32 = 0;
        for (0..config.channels) |ch| {
            max_delay = @max(max_delay, config.delay_frames[ch]);
        }
        if (config.bypass) max_delay = 0;
        const lookahead = if (config.limiter and !config.bypass) config.lookahead_frames else 0;
        const delay = try allocator.alloc(f32, if (max_delay == 0) 0 else (max_delay + 1) * config.channels);
        errdefer allocator.free(delay);
        const pending = try allocator.alloc(f32, if (lookahead == 0) 0 else (lookahead + 1) * config.channels);
        errdefer allocator.free(pending);
        const peaks = try allocator.alloc(Peak, if (config.limiter and !config.bypass) lookahead + 1 else 0);
        @memset(delay, 0); @memset(pending, 0);
        return .{ .allocator = allocator, .config = config, .delay = delay, .pending = pending,
            .peaks = peaks, .max_delay = max_delay, .lookahead = lookahead,
            .release = if (config.release_ms == 0) 0 else @exp(-1.0 / (config.release_ms * 0.001 * 48000.0)) };
    }
    pub fn deinit(self: *Processor) void {
        self.allocator.free(self.delay); self.allocator.free(self.pending); self.allocator.free(self.peaks);
        self.* = undefined;
    }
    pub fn timing(self: *const Processor) Timing {
        return .{ .calibration_max_frames = self.max_delay, .lookahead_frames = self.lookahead,
            .tail_frames = self.max_delay + self.lookahead };
    }
    /// Mutable owned block; no allocation and no hidden output queue.
    /// Validation precedes mutation, so rejection does not advance state.
    pub fn process(self: *Processor, samples: []f32) !void {
        if (self.finishing) return error.InputFinished;
        const channels = self.config.channels;
        if (samples.len % channels != 0) return error.InvalidOutputBlock;
        const frames = samples.len / channels;
        if (frames > std.math.maxInt(u64) - self.frame) return error.FrameOverflow;
        // Input shape/lifecycle still checked; do not scan or rewrite bypassed PCM.
        // Device submission retains its own mandatory nonfinite validation.
        if (self.config.bypass or self.config.neutral()) {
            self.frame += frames;
            if (frames > 0) self.has_input = true;
            return;
        }
        for (samples, 0..) |sample, i| {
            const value = @as(f64, sample) * self.config.master * self.config.trim[i % channels];
            if (!std.math.isFinite(sample) or !std.math.isFinite(value) or @abs(value) > std.math.floatMax(f32)) return error.InvalidOutputSample;
        }
        if (frames > 0) self.has_input = true;
        self.processUnchecked(samples);
    }
    fn processUnchecked(self: *Processor, samples: []f32) void {
        const channels: usize = self.config.channels;
        var at: usize = 0;
        while (at < samples.len) : (at += channels) {
            var aligned: [32]f32 = undefined;
            var peak: f64 = 0;
            for (0..channels) |ch| {
                const gain = self.config.master * self.config.trim[ch];
                const value: f32 = if (gain == 1) samples[at + ch] else @floatCast(@as(f64, samples[at + ch]) * gain);
                if (self.max_delay == 0) { aligned[ch] = value; } else {
                    const length: u64 = self.max_delay + 1;
                    self.delay[@as(usize, @intCast(self.frame % length)) * channels + ch] = value;
                    const read = (self.frame % length + length - self.config.delay_frames[ch]) % length;
                    aligned[ch] = self.delay[@as(usize, @intCast(read)) * channels + ch];
                }
                peak = @max(peak, @abs(@as(f64, aligned[ch])));
            }
            if (!self.config.limiter) {
                @memcpy(samples[at..at + channels], aligned[0..channels]);
            } else {
                const oldest = self.frame -| self.lookahead;
                while (self.peak_count > 0 and self.peaks[self.peak_head].index < oldest) {
                    self.peak_head = (self.peak_head + 1) % self.peaks.len; self.peak_count -= 1;
                }
                while (self.peak_count > 0) {
                    const back = (self.peak_head + self.peak_count - 1) % self.peaks.len;
                    if (self.peaks[back].value > peak) break;
                    self.peak_count -= 1;
                }
                self.peaks[(self.peak_head + self.peak_count) % self.peaks.len] = .{ .index = self.frame, .value = peak };
                self.peak_count += 1;
                const maximum = self.peaks[self.peak_head].value;
                const target = if (maximum > self.config.ceiling) self.config.ceiling / maximum else 1;
                self.envelope = @min(target, self.release * self.envelope + (1 - self.release));
                if (self.lookahead > 0) {
                    const length: u64 = self.lookahead + 1;
                    @memcpy(self.pending[@as(usize, @intCast(self.frame % length)) * channels..][0..channels], aligned[0..channels]);
                    if (self.frame < self.lookahead) { @memset(samples[at..at + channels], 0); } else {
                        const read = @as(usize, @intCast((self.frame - self.lookahead) % length)) * channels;
                        for (0..channels) |ch| samples[at + ch] = @floatCast(@as(f64, self.pending[read + ch]) * self.envelope);
                    }
                } else {
                    for (0..channels) |ch| samples[at + ch] = @floatCast(@as(f64, aligned[ch]) * self.envelope);
                }
            }
            self.frame += 1;
        }
    }
    pub fn finish(self: *Processor) void {
        if (self.finishing) return;
        self.finishing = true;
        self.tail_left = if (self.has_input) self.timing().tail_frames else 0;
    }
    /// Writes up to output.len/channels tail frames. Call until it returns zero;
    /// only then signal device finish_input. Returned length is in frames.
    pub fn drain(self: *Processor, output: []f32) !u32 {
        if (!self.finishing) return error.InputNotFinished;
        if (output.len == 0 or output.len % self.config.channels != 0) return error.InvalidOutputBlock;
        const frames: u32 = @intCast(@min(output.len / self.config.channels, self.tail_left));
        if (frames > std.math.maxInt(u64) - self.frame) return error.FrameOverflow;
        const samples = output[0..@as(usize, frames) * self.config.channels];
        @memset(samples, 0); self.processUnchecked(samples); self.tail_left -= frames;
        return frames;
    }
};
