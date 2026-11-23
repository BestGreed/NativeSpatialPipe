use std::time::Duration;

/// Wait coarsely before the caller spins to its absolute QPC deadline.
pub(super) struct Pacer {
    #[cfg(windows)]
    windows: windows::Resources,
}

impl Pacer {
    pub(super) fn new() -> Self {
        Self {
            #[cfg(windows)]
            windows: windows::Resources::new(),
        }
    }

    pub(super) fn wait(&self, duration: Duration) {
        #[cfg(windows)]
        if self.windows.wait(duration) {
            return;
        }
        std::thread::sleep(duration);
    }

    pub(super) fn timer(&self) -> &'static str {
        #[cfg(windows)]
        {
            self.windows.timer()
        }
        #[cfg(not(windows))]
        {
            "thread_sleep"
        }
    }

    pub(super) fn priority(&self) -> &'static str {
        #[cfg(windows)]
        {
            self.windows.priority()
        }
        #[cfg(not(windows))]
        {
            "default"
        }
    }
}

#[cfg(windows)]
mod windows {
    use std::{ffi::c_void, ptr, time::Duration};

    type Handle = *mut c_void;
    const CREATE_WAITABLE_TIMER_HIGH_RESOLUTION: u32 = 2;
    const SYNCHRONIZE_AND_TIMER_MODIFY_STATE: u32 = 0x0010_0002;
    const WAIT_OBJECT_0: u32 = 0;
    const THREAD_PRIORITY_HIGHEST: i32 = 2;
    const AVRT_PRIORITY_HIGH: i32 = 1;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateWaitableTimerExW(
            attrs: *const c_void,
            name: *const u16,
            flags: u32,
            access: u32,
        ) -> Handle;
        fn SetWaitableTimer(
            timer: Handle,
            due: *const i64,
            period: i32,
            callback: *const c_void,
            argument: *const c_void,
            resume: i32,
        ) -> i32;
        fn WaitForSingleObject(handle: Handle, milliseconds: u32) -> u32;
        fn CloseHandle(handle: Handle) -> i32;
        fn GetCurrentThread() -> Handle;
        fn SetThreadPriority(thread: Handle, priority: i32) -> i32;
    }

    #[link(name = "avrt")]
    unsafe extern "system" {
        fn AvSetMmThreadCharacteristicsW(task: *const u16, index: *mut u32) -> Handle;
        fn AvSetMmThreadPriority(handle: Handle, priority: i32) -> i32;
        fn AvRevertMmThreadCharacteristics(handle: Handle) -> i32;
    }

    #[link(name = "winmm")]
    unsafe extern "system" {
        fn timeBeginPeriod(milliseconds: u32) -> u32;
        fn timeEndPeriod(milliseconds: u32) -> u32;
    }

    pub(super) struct Resources {
        timer: Handle,
        mmcss: Handle,
        timer_period: bool,
        priority: &'static str,
    }

    impl Resources {
        pub(super) fn new() -> Self {
            // The registration belongs to this worker thread for its entire lifetime.
            let task = [80_u16, 114, 111, 32, 65, 117, 100, 105, 111, 0]; // Pro Audio
            let mut index = 0;
            unsafe {
                let mmcss = AvSetMmThreadCharacteristicsW(task.as_ptr(), &mut index);
                let priority = if !mmcss.is_null() {
                    if AvSetMmThreadPriority(mmcss, AVRT_PRIORITY_HIGH) != 0 {
                        "mmcss_pro_audio_high"
                    } else {
                        "mmcss_pro_audio"
                    }
                } else if SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST) != 0 {
                    "thread_highest"
                } else {
                    "default"
                };
                let timer = CreateWaitableTimerExW(
                    ptr::null(),
                    ptr::null(),
                    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                    SYNCHRONIZE_AND_TIMER_MODIFY_STATE,
                );
                let timer_period = timer.is_null() && timeBeginPeriod(1) == 0;
                Self {
                    timer,
                    mmcss,
                    timer_period,
                    priority,
                }
            }
        }

        pub(super) fn timer(&self) -> &'static str {
            if !self.timer.is_null() {
                "high_resolution_waitable_timer"
            } else if self.timer_period {
                "time_begin_period_1ms"
            } else {
                "thread_sleep"
            }
        }

        pub(super) fn priority(&self) -> &'static str {
            self.priority
        }

        pub(super) fn wait(&self, duration: Duration) -> bool {
            if self.timer.is_null() {
                return false;
            }
            let ticks = -((duration.as_nanos().div_ceil(100)).clamp(1, i64::MAX as u128) as i64);
            let timeout =
                (duration.as_millis().saturating_add(5)).min(u128::from(u32::MAX - 1)) as u32;
            unsafe {
                SetWaitableTimer(self.timer, &ticks, 0, ptr::null(), ptr::null(), 0) != 0
                    && WaitForSingleObject(self.timer, timeout) == WAIT_OBJECT_0
            }
        }
    }

    impl Drop for Resources {
        fn drop(&mut self) {
            unsafe {
                if !self.timer.is_null() {
                    CloseHandle(self.timer);
                }
                if self.timer_period {
                    timeEndPeriod(1);
                }
                if !self.mmcss.is_null() {
                    AvRevertMmThreadCharacteristics(self.mmcss);
                }
            }
        }
    }
}
