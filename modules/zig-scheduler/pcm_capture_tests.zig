const std = @import("std");
const u = @import("common.zig");
const n = @import("native.zig");
const d = @import("desktop_playback.zig");
const Session = @import("pcm_capture.zig").Session;
const SubmitLeased = *const fn(?*anyopaque, u64, u32, [*]const u8, usize, u64, u64) callconv(.C) c_int;

const TestSourceState = struct {
 ready: u32 = 0x0f,
 opened: u32 = 0,
 activated: u32 = 0,
 open_calls: usize = 0,
 close_calls: usize = 0,
 fail_open: ?usize = null,
 order_valid: bool = true,
};
var test_sources = TestSourceState{};

fn testSourceMask(_: ?*anyopaque) callconv(.C) u32 {
 return test_sources.ready;
}
fn testOpenLeased(name: [*:0]const u16, event: [*:0]const u16, handle: ?*anyopaque, _: SubmitLeased) callconv(.C) ?*anyopaque {
 const ring = std.mem.span(name);
 const signal = std.mem.span(event);
 const source: usize = if (ring[ring.len - 2] == '-') @intCast(ring[ring.len - 1] - '0') else 0;
 test_sources.open_calls += 1;
 if (test_sources.fail_open == source) return null;
 if (handle == null or (source != 0 and signal[signal.len - 1] != ring[ring.len - 1])) test_sources.order_valid = false;
 test_sources.opened |= @as(u32, 1) << @intCast(source);
 return @ptrFromInt(source + 1);
}
fn testActivate(_: ?*anyopaque, source: u32) callconv(.C) c_int {
 const bit = @as(u32, 1) << @intCast(source);
 if (test_sources.opened & bit == 0) test_sources.order_valid = false;
 test_sources.activated |= bit;
 return 0;
}
fn testClose(handle: ?*anyopaque) callconv(.C) void {
 const source = @intFromPtr(handle.?) - 1;
 test_sources.opened &= ~(@as(u32, 1) << @intCast(source));
 test_sources.close_calls += 1;
}

test "prepared PCM rings attach once before activation and retry a late source independently" {
 const dll = try std.fs.cwd().realpathAlloc(u.a, "build/roar-native/release/nsp_croksor.dll");
 defer u.a.free(dll);
 const config = d.Config{ .bridgeDll = dll, .asioDll = "unused", .driver = "unused", .sourcePolicy = "unified", .unifiedSlotProfile = .dtsxHtCompact };
 var pipeline = try n.Pipeline.initUnified(dll, "unused.f32", false, 1.0, config);
 defer pipeline.close();
 const name = try u.wide("Local\\NSP-PCM-fixture");
 defer u.a.free(name);
 const event = try u.wide("Local\\NSP-PCM-fixture-ready");
 defer u.a.free(event);
 test_sources = .{};
 var session = Session{
  .captureLib = undefined, .ingressLib = undefined, .capture = null,
  .closeCapture = undefined, .closeIngress = testClose, .captureStats = undefined, .ingressStats = undefined,
  .pipeline = null, .shared = &pipeline, .openIngress = undefined,
  .ingressName = name, .ingressEvent = event, .sourceMask = testSourceMask,
  .openLeasedIngress = testOpenLeased, .activateSource = testActivate,
 };
 defer for (session.ingress) |handle| { if (handle != null) testClose(handle); };
 try session.startIngress();
 try std.testing.expectEqual(@as(u32, 0x0f), test_sources.activated);
 try std.testing.expectEqual(@as(usize, 4), test_sources.open_calls);
 const original = session.ingress[0..4].*;
 test_sources.ready = 0xff;
 test_sources.fail_open = 4;
 try session.startIngress();
 try std.testing.expectEqual(@as(u32, 0xef), test_sources.activated);
 try std.testing.expect(session.ingressErrors[4] != null);
 try std.testing.expect(session.ingressErrors[4].? == error.PcmIngressOpen);
 try std.testing.expectEqualSlices(?*anyopaque, &original, session.ingress[0..4]);
 test_sources.fail_open = null;
 try session.startIngress();
 try std.testing.expectEqual(@as(u32, 0xff), test_sources.activated);
 try std.testing.expect(session.ingressErrors[4] == null);
 const count = test_sources.open_calls;
 try session.startIngress();
 try std.testing.expectEqual(count, test_sources.open_calls);
 try std.testing.expect(test_sources.order_valid);
 try std.testing.expectEqual(@as(usize, 0), test_sources.close_calls);
 try std.testing.expectError(error.PcmSourceUnavailable, session.attachSource(8, name, event));
 var report = try pipeline.finish();
 report.deinit();
}

