//! One coalescing event for ingress and completed upmix, plus an absolute
//! monotonic deadline checked by the render thread after every wake.
use std::{
    ffi::c_void,
    ptr::null_mut,
    sync::atomic::{AtomicU64, Ordering::Relaxed},
    time::{Duration, Instant},
};

type Handle = *mut c_void;
#[link(name = "kernel32")]
unsafe extern "system" {
    fn CreateEventW(attributes: Handle, manual: i32, initial: i32, name: *const u16) -> Handle;
    fn CreateWaitableTimerExW(attributes: Handle, name: *const u16, flags: u32, access: u32) -> Handle;
    fn SetWaitableTimer(timer: Handle, due: *const i64, period: i32, callback: Handle, context: Handle, resume: i32) -> i32;
    fn SetEvent(event: Handle) -> i32;
    fn WaitForSingleObject(handle: Handle, millis: u32) -> u32;
    fn WaitForMultipleObjects(count: u32, handles: *const Handle, all: i32, millis: u32) -> u32;
    fn CloseHandle(handle: Handle) -> i32;
}

pub(crate) struct RenderWake {
    event: usize,
    timer: usize,
    timer_mode: &'static str,
    high_resolution_create_error: u32,
    standard_create_error: u32,
    diagnostics: Diagnostics,
}

#[derive(Default)]
struct Diagnostics {
    timed_waits: AtomicU64,
    event_wakes: AtomicU64,
    timer_wakes: AtomicU64,
    fallback_waits: AtomicU64,
    fallback_timeouts: AtomicU64,
    timer_arm_failures: AtomicU64,
    wait_failures: AtomicU64,
    notify_failures: AtomicU64,
    last_timer_arm_error: AtomicU64,
    last_wait_error: AtomicU64,
    last_wait_result: AtomicU64,
    last_notify_error: AtomicU64,
    deadline_wakes: AtomicU64,
    deadline_overrun_ns_total: AtomicU64,
    deadline_overrun_ns_max: AtomicU64,
}

/// A best-effort atomic snapshot; timer_mode describes creation, while
/// fallback_waits also exposes per-wait degradation after an arm failure.
#[derive(Clone, Copy, Debug)]
pub(crate) struct RenderWakeStats {
    pub(crate) timer_mode: &'static str,
    pub(crate) high_resolution_create_error: u32,
    pub(crate) standard_create_error: u32,
    pub(crate) timed_waits: u64,
    pub(crate) event_wakes: u64,
    pub(crate) timer_wakes: u64,
    pub(crate) fallback_waits: u64,
    pub(crate) fallback_timeouts: u64,
    pub(crate) timer_arm_failures: u64,
    pub(crate) wait_failures: u64,
    pub(crate) notify_failures: u64,
    pub(crate) last_timer_arm_error: u32,
    pub(crate) last_wait_error: u32,
    pub(crate) last_wait_result: u32,
    pub(crate) last_notify_error: u32,
    /// Successful timed waits returning after the original requested deadline,
    /// including event wakes. Does not measure later ingress/render work.
    pub(crate) deadline_wakes: u64,
    pub(crate) deadline_overrun_ns_total: u64,
    pub(crate) deadline_overrun_ns_max: u64,
}

fn last_error() -> u32 {
    // WAIT_FAILED/SetEvent failure must not turn into an apparent success if
    // a provider unexpectedly leaves last-error unset.
    std::io::Error::last_os_error().raw_os_error().filter(|&code| code != 0)
        .map_or(31, |code| code as u32) // ERROR_GEN_FAILURE
}

fn timeout_millis(timeout: Duration) -> u32 {
    timeout.as_nanos().div_ceil(1_000_000).min((u32::MAX - 1) as u128) as u32
}

impl RenderWake {
    pub(crate) fn new() -> Result<Self, String> {
        let event = unsafe { CreateEventW(null_mut(), 0, 0, std::ptr::null()) };
        if event.is_null() {
            return Err(format!("Render wake event: {}", std::io::Error::last_os_error()));
        }
        const TIMER_ACCESS: u32 = 0x0010_0002; // SYNCHRONIZE | TIMER_MODIFY_STATE
        let mut timer = unsafe { CreateWaitableTimerExW(null_mut(), std::ptr::null(), 2, TIMER_ACCESS) };
        let mut timer_mode = "highResolution";
        let mut high_resolution_create_error = 0;
        let mut standard_create_error = 0;
        if timer.is_null() {
            high_resolution_create_error = last_error();
            // Older systems may reject the high-resolution flag while still
            // supporting a waitable timer. Keep that fallback observable.
            timer = unsafe { CreateWaitableTimerExW(null_mut(), std::ptr::null(), 0, TIMER_ACCESS) };
            timer_mode = "standard";
            if timer.is_null() {
                standard_create_error = last_error();
                timer_mode = "millisecondFallback";
            }
        }
        Ok(Self {
            event: event as usize,
            timer: timer as usize,
            timer_mode,
            high_resolution_create_error,
            standard_create_error,
            diagnostics: Diagnostics::default(),
        })
    }

    pub(crate) fn event(&self) -> Handle { self.event as Handle }

    pub(crate) fn notify(&self) {
        if unsafe { SetEvent(self.event()) } == 0 {
            let error = last_error();
            self.diagnostics.notify_failures.fetch_add(1, Relaxed);
            self.diagnostics.last_notify_error.store(error as u64, Relaxed);
        }
    }

    pub(crate) fn stats(&self) -> RenderWakeStats {
        let d = &self.diagnostics;
        RenderWakeStats {
            timer_mode: self.timer_mode,
            high_resolution_create_error: self.high_resolution_create_error,
            standard_create_error: self.standard_create_error,
            timed_waits: d.timed_waits.load(Relaxed),
            event_wakes: d.event_wakes.load(Relaxed),
            timer_wakes: d.timer_wakes.load(Relaxed),
            fallback_waits: d.fallback_waits.load(Relaxed),
            fallback_timeouts: d.fallback_timeouts.load(Relaxed),
            timer_arm_failures: d.timer_arm_failures.load(Relaxed),
            wait_failures: d.wait_failures.load(Relaxed),
            notify_failures: d.notify_failures.load(Relaxed),
            last_timer_arm_error: d.last_timer_arm_error.load(Relaxed) as u32,
            last_wait_error: d.last_wait_error.load(Relaxed) as u32,
            last_wait_result: d.last_wait_result.load(Relaxed) as u32,
            last_notify_error: d.last_notify_error.load(Relaxed) as u32,
            deadline_wakes: d.deadline_wakes.load(Relaxed),
            deadline_overrun_ns_total: d.deadline_overrun_ns_total.load(Relaxed),
            deadline_overrun_ns_max: d.deadline_overrun_ns_max.load(Relaxed),
        }
    }

    /// Compatibility adapter. Deadline-aware callers should use wait_until so
    /// time between choosing a target and entering this function is not added
    /// back to the requested delay.
    #[allow(dead_code)]
    pub(crate) fn wait(&self, timeout: Option<Duration>) -> Result<(), u32> {
        let deadline = timeout.map(|timeout| Instant::now().checked_add(timeout)
            .ok_or(87u32)).transpose()?; // ERROR_INVALID_PARAMETER: unrepresentable deadline
        self.wait_until(deadline)
    }

    /// Wait for ingress/upmix notification or the original monotonic deadline.
    /// None waits only for an event; an expired deadline returns immediately
    /// without consuming an event. Callers recheck their state after every wake.
    /// OS wait failures propagate instead of causing a retry/busy loop here.
    pub(crate) fn wait_until(&self, deadline: Option<Instant>) -> Result<(), u32> {
        let Some(deadline) = deadline else {
            let result = unsafe { WaitForSingleObject(self.event(), u32::MAX) };
            return self.complete_wait(result, false, None);
        };
        if deadline <= Instant::now() { return Ok(()); }
        self.diagnostics.timed_waits.fetch_add(1, Relaxed);
        let timer = self.timer as Handle;
        let armed = if !timer.is_null() {
            // SetWaitableTimer uses a relative duration, not an Instant/QPC
            // deadline. Recompute as close to the arm call as possible. A
            // preemption inside/after the syscall can still delay the wake.
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() { return Ok(()); }
            let ticks = -(remaining.as_nanos().div_ceil(100).min(i64::MAX as u128) as i64);
            (unsafe { SetWaitableTimer(timer, &ticks, 0, null_mut(), null_mut(), 0) }) != 0
        } else { false };
        if armed {
            let handles = [self.event(), timer];
            let result = unsafe { WaitForMultipleObjects(2, handles.as_ptr(), 0, u32::MAX) };
            self.complete_wait(result, true, Some(deadline))
        } else {
            if !timer.is_null() {
                let error = last_error();
                self.diagnostics.timer_arm_failures.fetch_add(1, Relaxed);
                self.diagnostics.last_timer_arm_error.store(error as u64, Relaxed);
            }
            // Preserve the same target after an unsuccessful timer arm.
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() { return Ok(()); }
            self.diagnostics.fallback_waits.fetch_add(1, Relaxed);
            let millis = timeout_millis(remaining);
            let result = unsafe { WaitForSingleObject(self.event(), millis) };
            self.complete_wait(result, false, Some(deadline))
        }
    }

    fn complete_wait(&self, result: u32, timer_wait: bool, deadline: Option<Instant>) -> Result<(), u32> {
        // Read last-error immediately, before another operation could replace it.
        let error = (result == u32::MAX).then(last_error);
        let overrun = deadline.map(|deadline| Instant::now().saturating_duration_since(deadline));
        let d = &self.diagnostics;
        d.last_wait_result.store(result as u64, Relaxed);
        match result {
            0 => { d.event_wakes.fetch_add(1, Relaxed); }
            1 if timer_wait => { d.timer_wakes.fetch_add(1, Relaxed); }
            258 if !timer_wait && deadline.is_some() => { d.fallback_timeouts.fetch_add(1, Relaxed); }
            _ => {
                let error = error.unwrap_or(13); // ERROR_INVALID_DATA: unexpected wait result
                d.wait_failures.fetch_add(1, Relaxed);
                d.last_wait_error.store(error as u64, Relaxed);
                return Err(error);
            }
        }
        if let Some(overrun) = overrun {
            let overrun = overrun.as_nanos().min(u64::MAX as u128) as u64;
            if overrun > 0 {
                d.deadline_wakes.fetch_add(1, Relaxed);
                d.deadline_overrun_ns_total.fetch_add(overrun, Relaxed);
                d.deadline_overrun_ns_max.fetch_max(overrun, Relaxed);
            }
        }
        Ok(())
    }
}

impl Drop for RenderWake {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.event());
            if self.timer != 0 { CloseHandle(self.timer as Handle); }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn millisecond_fallback_rounds_up_without_becoming_infinite() {
        assert_eq!(timeout_millis(Duration::ZERO), 0);
        assert_eq!(timeout_millis(Duration::from_micros(200)), 1);
        assert_eq!(timeout_millis(Duration::from_micros(500)), 1);
        assert_eq!(timeout_millis(Duration::MAX), u32::MAX - 1);
    }

    #[test]
    fn event_notification_and_timer_expiry_are_reported_separately() {
        let wake = RenderWake::new().unwrap();
        wake.notify();
        wake.wait(None).unwrap();
        wake.wait(Some(Duration::from_millis(1))).unwrap();
        let stats = wake.stats();
        assert_eq!(stats.event_wakes, 1);
        assert_eq!(stats.timed_waits, 1);
        assert_eq!(stats.timer_wakes + stats.fallback_timeouts, 1);
        assert_eq!(stats.wait_failures, 0);
        assert_eq!(stats.notify_failures, 0);
    }

    #[test]
    fn unexpected_wait_result_is_an_error_not_a_successful_wake() {
        let wake = RenderWake::new().unwrap();
        assert_eq!(wake.complete_wait(128, true, None), Err(13));
        let stats = wake.stats();
        assert_eq!(stats.wait_failures, 1);
        assert_eq!(stats.last_wait_result, 128);
        assert_eq!(stats.last_wait_error, 13);
        assert_eq!(stats.event_wakes + stats.timer_wakes, 0);
    }

    #[test]
    fn expired_deadline_does_not_arm_timer_or_consume_pending_event() {
        let wake = RenderWake::new().unwrap();
        let deadline = Instant::now();
        wake.notify();
        wake.wait_until(Some(deadline)).unwrap();
        assert_eq!(wake.stats().timed_waits, 0);
        assert_eq!(wake.stats().event_wakes, 0);
        wake.wait_until(None).unwrap();
        assert_eq!(wake.stats().event_wakes, 1);
    }

    #[test]
    fn overrun_is_measured_from_original_absolute_deadline() {
        let wake = RenderWake::new().unwrap();
        let deadline = Instant::now() - Duration::from_millis(2);
        wake.complete_wait(1, true, Some(deadline)).unwrap();
        let stats = wake.stats();
        assert_eq!(stats.deadline_wakes, 1);
        assert!(stats.deadline_overrun_ns_total >= 2_000_000);
        assert_eq!(stats.deadline_overrun_ns_total, stats.deadline_overrun_ns_max);
    }
}
