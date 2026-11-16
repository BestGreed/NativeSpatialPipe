use std::io;

/// Register the clock worker with MMCSS Pro Audio at its critical relative
/// priority, falling back to highest Win32 thread priority. The process class
/// is unchanged; the guard restores scheduling on the owning thread.
#[cfg(windows)]
pub struct ClockPriorityGuard {
    previous: i32,
    multimedia: Option<isize>,
    _thread: std::marker::PhantomData<std::rc::Rc<()>>,
}
#[cfg(windows)]
#[link(name = "avrt")]
extern "system" {
    fn AvSetMmThreadCharacteristicsW(task: *const u16, index: *mut u32) -> isize;
    fn AvSetMmThreadPriority(handle: isize, priority: i32) -> i32;
    fn AvRevertMmThreadCharacteristics(handle: isize) -> i32;
}
#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetCurrentThread() -> isize;
    fn GetThreadPriority(thread: isize) -> i32;
    fn SetThreadPriority(thread: isize, priority: i32) -> i32;
}
#[cfg(windows)]
impl ClockPriorityGuard {
    pub fn enter() -> io::Result<Self> {
        let thread = unsafe { GetCurrentThread() };
        let previous = unsafe { GetThreadPriority(thread) };
        if previous == i32::MAX || unsafe { SetThreadPriority(thread, 2) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let name: Vec<u16> = "Pro Audio".encode_utf16().chain(Some(0)).collect();
        let mut index = 0;
        let handle = unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut index) };
        let multimedia = if handle != 0 {
            if unsafe { AvSetMmThreadPriority(handle, 2) } != 0 { Some(handle) }
            else { unsafe { AvRevertMmThreadCharacteristics(handle); } None }
        } else { None };
        eprintln!("clock_multimedia_scheduling={}", multimedia.is_some());
        Ok(Self { previous, multimedia, _thread: std::marker::PhantomData })
    }
}
#[cfg(windows)]
impl Drop for ClockPriorityGuard {
    fn drop(&mut self) {
        unsafe {
            if let Some(handle) = self.multimedia { AvRevertMmThreadCharacteristics(handle); }
            SetThreadPriority(GetCurrentThread(), self.previous);
        }
    }
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn QueryPerformanceCounter(value: *mut i64) -> i32;
    fn QueryPerformanceFrequency(value: *mut i64) -> i32;
}

pub struct LocalClock {
    #[cfg(windows)]
    frequency: i64,
    #[cfg(not(windows))]
    start: std::time::Instant,
}

impl LocalClock {
    pub fn new() -> io::Result<Self> {
        #[cfg(windows)]
        {
            let mut frequency = 0;
            if unsafe { QueryPerformanceFrequency(&mut frequency) } == 0 || frequency <= 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(Self { frequency })
        }
        #[cfg(not(windows))]
        Ok(Self {
            start: std::time::Instant::now(),
        })
    }

    pub fn now_ns(&self) -> i64 {
        #[cfg(windows)]
        {
            let mut ticks = 0;
            assert_ne!(unsafe { QueryPerformanceCounter(&mut ticks) }, 0);
            ((ticks as i128 * 1_000_000_000) / self.frequency as i128) as i64
        }
        #[cfg(not(windows))]
        {
            self.start
                .elapsed()
                .as_nanos()
                .try_into()
                .expect("local clock overflow")
        }
    }
}
