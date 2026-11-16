//! DSP configuration only. Runtime PCM processing belongs to Rust.
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
    pub fn timing(config:Config) Timing {
        var delay:u32=0;
        if(!config.bypass) for(config.delay_frames[0..config.channels]) |value| {delay=@max(delay,value);};
        const ahead=if(config.limiter and !config.bypass) config.lookahead_frames else 0;
        return .{.calibration_max_frames=delay,.lookahead_frames=ahead,.tail_frames=delay+ahead};
    }
};
pub const Timing = struct { calibration_max_frames: u32, lookahead_frames: u32, tail_frames: u32 };

pub const NativeConfig = extern struct {
    size:u32=@sizeOf(NativeConfig), version:u32=1, channels:u32, sample_rate:u32,
    bypass:u32, limiter:u32, lookahead_frames:u32, reserved:u32=0,
    master:f64, ceiling:f64, release_ms:f64, trim:[32]f64, delay_frames:[32]u32,
    pub fn from(config:Config) NativeConfig {
        return .{.channels=config.channels,.sample_rate=config.sample_rate,
            .bypass=@intFromBool(config.bypass),.limiter=@intFromBool(config.limiter),
            .lookahead_frames=config.lookahead_frames,.master=config.master,
            .ceiling=config.ceiling,.release_ms=config.release_ms,.trim=config.trim,.delay_frames=config.delay_frames};
    }
};
