//! Control-thread primary selection. The caller supplies fresh receiver snapshots;
//! this module owns no audio buffers, threads, timers, allocations or I/O.
//! Times use one monotonic millisecond clock. Pipeline IDs must be unique in a
//! snapshot, and last_progress_ms must advance only when that source progresses.
const std = @import("std");

pub const Candidate = struct {
    pipeline: usize,
    dynamic_objects: u32,
    last_progress_ms: u64,
    eligible: bool = true,
};

pub const Config = struct {
    stable_lead_ms: u64 = 500,
    stale_after_ms: u64 = 1000,
    /// Zero is treated as one: automatic preemption always needs a strict lead.
    min_lead_objects: u32 = 1,
};

pub const Reason = enum {
    unchanged,
    initial,
    manual,
    incumbent_unavailable,
    stable_lead,
    no_candidate,
};

pub const Decision = struct {
    selected: ?usize,
    previous: ?usize,
    changed: bool,
    reason: Reason,
};

pub const Election = struct {
    config: Config = .{},
    selected: ?usize = null,
    challenger: ?usize = null,
    challenger_since_ms: ?u64 = null,
    last_update_ms: ?u64 = null,
    changes: u64 = 0,

    pub fn init(config: Config) Election {
        return .{ .config = config };
    }

    /// Call asynchronously from the control loop, never once per audio block.
    /// A valid manual choice wins immediately, including a static-only stream.
    /// An unavailable manual choice falls back to automatic election and will
    /// retake priority when it becomes available. Automatic candidates require
    /// at least one dynamic object; ties retain the live incumbent. If there is
    /// no incumbent, equal scores are resolved by the smallest pipeline ID.
    pub fn update(self: *Election, now_ms: u64, candidates: []const Candidate, manual: ?usize) Decision {
        // A clock reset must never make an old challenger appear stable.
        if (self.last_update_ms) |last| {
            if (now_ms < last) self.clearChallenger();
        }
        self.last_update_ms = now_ms;

        if (manual) |id| {
            if (self.find(candidates, id, now_ms, false) != null) {
                self.clearChallenger();
                return self.choose(id, .manual);
            }
        }

        const current = if (self.selected) |id| self.find(candidates, id, now_ms, true) else null;
        var best = current;
        for (candidates) |candidate| {
            if (!self.available(candidate, now_ms) or candidate.dynamic_objects == 0) continue;
            if (best == null or candidate.dynamic_objects > best.?.dynamic_objects or
                (candidate.dynamic_objects == best.?.dynamic_objects and
                best.?.pipeline != self.selected and candidate.pipeline < best.?.pipeline))
            {
                best = candidate;
            }
        }

        if (best == null) {
            self.clearChallenger();
            return self.choose(null, .no_candidate);
        }
        if (current == null) {
            self.clearChallenger();
            return self.choose(best.?.pipeline, if (self.selected == null) .initial else .incumbent_unavailable);
        }
        const next = best.?;
        const incumbent = current.?;
        if (next.pipeline == incumbent.pipeline or next.dynamic_objects <= incumbent.dynamic_objects or
            next.dynamic_objects - incumbent.dynamic_objects < @max(@as(u32, 1), self.config.min_lead_objects))
        {
            self.clearChallenger();
            return self.choose(incumbent.pipeline, .unchanged);
        }

        if (self.challenger != next.pipeline) {
            self.challenger = next.pipeline;
            self.challenger_since_ms = now_ms;
        }
        if (now_ms - self.challenger_since_ms.? >= self.config.stable_lead_ms) {
            self.clearChallenger();
            return self.choose(next.pipeline, .stable_lead);
        }
        return self.choose(incumbent.pipeline, .unchanged);
    }

    fn available(self: *const Election, candidate: Candidate, now_ms: u64) bool {
        return candidate.eligible and candidate.last_progress_ms <= now_ms and
            now_ms - candidate.last_progress_ms <= self.config.stale_after_ms;
    }

    fn find(self: *const Election, candidates: []const Candidate, id: usize, now_ms: u64, dynamic_only: bool) ?Candidate {
        for (candidates) |candidate| {
            if (candidate.pipeline == id and self.available(candidate, now_ms) and
                (!dynamic_only or candidate.dynamic_objects > 0)) return candidate;
        }
        return null;
    }

    fn clearChallenger(self: *Election) void {
        self.challenger = null;
        self.challenger_since_ms = null;
    }

    fn choose(self: *Election, next: ?usize, reason: Reason) Decision {
        const previous = self.selected;
        const changed = previous != next;
        if (changed) self.changes +|= 1;
        self.selected = next;
        return .{ .selected = next, .previous = previous, .changed = changed, .reason = reason };
    }
};

fn row(id: usize, count: u32, now: u64) Candidate {
    return .{ .pipeline = id, .dynamic_objects = count, .last_progress_ms = now };
}

test "initial selection uses dynamic count and stable tie ordering" {
    var election = Election{};
    const first = election.update(0, &.{ row(4, 0, 0), row(3, 8, 0), row(1, 8, 0), row(2, 4, 0) }, null);
    try std.testing.expectEqual(@as(?usize, 1), first.selected);
    try std.testing.expect(first.changed);
    try std.testing.expectEqual(Reason.initial, first.reason);
    // The lower ID does not displace a live equal-score incumbent.
    const tied = election.update(10, &.{ row(0, 8, 10), row(1, 8, 10) }, null);
    try std.testing.expectEqual(@as(?usize, 1), tied.selected);
    try std.testing.expect(!tied.changed);
}

test "challenger must continuously lead before takeover" {
    var election = Election.init(.{ .stable_lead_ms = 100 });
    _ = election.update(0, &.{row(0, 8, 0)}, null);
    try std.testing.expectEqual(@as(?usize, 0), election.update(10, &.{ row(0, 8, 10), row(1, 12, 10) }, null).selected);
    try std.testing.expectEqual(@as(?usize, 0), election.update(109, &.{ row(0, 8, 109), row(1, 12, 109) }, null).selected);
    const takeover = election.update(110, &.{ row(0, 8, 110), row(1, 12, 110) }, null);
    try std.testing.expectEqual(@as(?usize, 1), takeover.selected);
    try std.testing.expectEqual(Reason.stable_lead, takeover.reason);
    try std.testing.expectEqual(@as(u64, 2), election.changes);
}

test "tie or different leader resets the stability interval" {
    var election = Election.init(.{ .stable_lead_ms = 100 });
    _ = election.update(0, &.{row(0, 8, 0)}, null);
    _ = election.update(10, &.{ row(0, 8, 10), row(1, 12, 10) }, null);
    _ = election.update(90, &.{ row(0, 8, 90), row(1, 8, 90) }, null);
    _ = election.update(100, &.{ row(0, 8, 100), row(1, 12, 100) }, null);
    try std.testing.expectEqual(@as(?usize, 0), election.update(110, &.{ row(0, 8, 110), row(1, 12, 110) }, null).selected);
    _ = election.update(150, &.{ row(0, 8, 150), row(1, 12, 150), row(2, 14, 150) }, null);
    try std.testing.expectEqual(@as(?usize, 0), election.update(249, &.{ row(0, 8, 249), row(2, 14, 249) }, null).selected);
    try std.testing.expectEqual(@as(?usize, 2), election.update(250, &.{ row(0, 8, 250), row(2, 14, 250) }, null).selected);
}

test "missing stale ineligible and zero-dynamic incumbents exit immediately" {
    var election = Election.init(.{ .stale_after_ms = 50 });
    _ = election.update(0, &.{row(0, 8, 0)}, null);
    const stale = election.update(51, &.{ row(0, 8, 0), row(1, 4, 51) }, null);
    try std.testing.expectEqual(@as(?usize, 1), stale.selected);
    try std.testing.expectEqual(Reason.incumbent_unavailable, stale.reason);
    var disabled = row(1, 4, 52);
    disabled.eligible = false;
    try std.testing.expectEqual(@as(?usize, 2), election.update(52, &.{ disabled, row(2, 2, 52) }, null).selected);
    try std.testing.expectEqual(@as(?usize, 3), election.update(53, &.{ row(2, 0, 53), row(3, 1, 53) }, null).selected);
    const empty = election.update(54, &.{}, null);
    try std.testing.expectEqual(@as(?usize, null), empty.selected);
    try std.testing.expectEqual(Reason.no_candidate, empty.reason);
}

test "manual priority supports static source and falls back while unavailable" {
    var election = Election.init(.{ .stable_lead_ms = 100, .stale_after_ms = 50 });
    _ = election.update(0, &.{row(0, 20, 0)}, null);
    const manual = election.update(1, &.{ row(0, 20, 1), row(1, 0, 1) }, 1);
    try std.testing.expectEqual(@as(?usize, 1), manual.selected);
    try std.testing.expectEqual(Reason.manual, manual.reason);
    try std.testing.expectEqual(@as(?usize, 1), election.update(2, &.{ row(0, 100, 2), row(1, 0, 2) }, 1).selected);
    try std.testing.expectEqual(@as(?usize, 0), election.update(53, &.{ row(0, 20, 53), row(1, 0, 2) }, 1).selected);
    try std.testing.expectEqual(@as(?usize, 1), election.update(54, &.{ row(0, 20, 54), row(1, 0, 54) }, 1).selected);
    // Releasing a static manual selection immediately restores auto selection.
    try std.testing.expectEqual(@as(?usize, 0), election.update(55, &.{ row(0, 20, 55), row(1, 0, 55) }, null).selected);
}

test "minimum margin zero duration and maximum count are safe" {
    var election = Election.init(.{ .stable_lead_ms = 0, .min_lead_objects = 2 });
    _ = election.update(0, &.{row(0, 1, 0)}, null);
    try std.testing.expectEqual(@as(?usize, 0), election.update(1, &.{ row(0, 1, 1), row(1, 2, 1) }, null).selected);
    try std.testing.expectEqual(@as(?usize, 1), election.update(2, &.{ row(0, 1, 2), row(1, std.math.maxInt(u32), 2) }, null).selected);
}

test "clock rollback resets pending lead and future timestamps are unavailable" {
    var election = Election.init(.{ .stable_lead_ms = 100 });
    _ = election.update(1000, &.{row(0, 8, 1000)}, null);
    _ = election.update(1010, &.{ row(0, 8, 1010), row(1, 12, 1010) }, null);
    try std.testing.expectEqual(@as(?usize, 0), election.update(10, &.{ row(0, 8, 10), row(1, 12, 10) }, null).selected);
    try std.testing.expectEqual(@as(?usize, 0), election.update(109, &.{ row(0, 8, 109), row(1, 12, 109) }, null).selected);
    try std.testing.expectEqual(@as(?usize, 1), election.update(110, &.{ row(0, 8, 110), row(1, 12, 110) }, null).selected);
    try std.testing.expectEqual(@as(?usize, null), election.update(111, &.{row(1, 12, 112)}, 1).selected);
}
