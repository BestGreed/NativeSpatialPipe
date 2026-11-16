const std = @import("std");
pub const Row = struct { size: ?u64 = null, last: ?u64 = null, since: u64 = 0, updates: u64 = 0, failed: bool = false };
pub const Routes = struct {
    rows: [4]Row = [_]Row{.{}} ** 4,
    selected: ?usize = null,
    changes: u64 = 0,
    pub fn update(self: *Routes, now: u64, sizes: [4]?u64) ?usize {
        for (&self.rows, sizes) |*row, size| {
            row.failed = size == null;
            if (size) |value| {
                if (row.size) |old| {
                    if (value > old) {
                        if (row.last == null or now - row.last.? > 1000) {
                            row.since = now;
                            row.updates = 0;
                        }
                        row.last = now;
                        row.updates += 1;
                    } else if (value < old) {
                        row.last = null;
                        row.since = 0;
                        row.updates = 0;
                    }
                }
                row.size = value;
            }
        }
        const current = if (self.selected) |i| if (self.live(i, now)) self.selected else null else null;
        var next: ?usize = null;
        for (self.rows, 0..) |row, i| {
            if (!self.live(i, now) or (current != null and i != current.? and row.updates < 2)) continue;
            if (next == null or row.since > self.rows[next.?].since) next = i;
        }
        if (next != self.selected) self.changes += 1;
        self.selected = next;
        return next;
    }
    fn live(self: *Routes, i: usize, now: u64) bool {
        const r = self.rows[i];
        return !r.failed and r.last != null and now - r.last.? <= 1000;
    }
};
test "baseline is not live, two progress updates preempt, stale and resumed routes" {
    var r = Routes{};
    try std.testing.expectEqual(@as(?usize, null), r.update(0, .{ 999, 0, 0, 0 }));
    try std.testing.expectEqual(@as(?usize, 0), r.update(100, .{ 1000, 0, 0, 0 }));
    try std.testing.expectEqual(@as(?usize, 0), r.update(200, .{ 1001, 1, 0, 0 }));
    try std.testing.expectEqual(@as(?usize, 1), r.update(300, .{ 1002, 2, 0, 0 }));
    try std.testing.expectEqual(@as(?usize, null), r.update(1400, .{ 1002, 2, 0, 0 }));
    try std.testing.expectEqual(@as(?usize, 0), r.update(1500, .{ 1003, 2, 0, 0 }));
    try std.testing.expectEqual(@as(?usize, null), r.update(1600, .{ null, 2, 0, 0 }));
}
