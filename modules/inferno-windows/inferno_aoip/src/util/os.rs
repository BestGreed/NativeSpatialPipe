use thread_priority::{thread_native_id, Error};

#[cfg(windows)]
#[link(name = "avrt")]
extern "system" {
  fn AvSetMmThreadCharacteristicsW(task: *const u16, index: *mut u32) -> isize;
  fn AvSetMmThreadPriority(handle: isize, priority: i32) -> i32;
  fn AvRevertMmThreadCharacteristics(handle: isize) -> i32;
}

/// Managed multimedia scheduling for the lifetime of an owned audio worker.
/// Drop must run on that same thread; the Rc marker prevents moving the guard.
#[cfg(windows)]
pub struct AudioSchedulingGuard { handle: isize, _thread: std::marker::PhantomData<std::rc::Rc<()>> }
#[cfg(windows)]
impl AudioSchedulingGuard {
  pub fn enter() -> std::io::Result<Self> {
    let name: Vec<u16> = "Pro Audio".encode_utf16().chain(Some(0)).collect();
    let mut index = 0;
    let handle = unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut index) };
    if handle == 0 { return Err(std::io::Error::last_os_error()); }
    if unsafe { AvSetMmThreadPriority(handle, 1) } == 0 {
      let error = std::io::Error::last_os_error();
      unsafe { AvRevertMmThreadCharacteristics(handle); }
      return Err(error);
    }
    Ok(Self { handle, _thread: std::marker::PhantomData })
  }
}
#[cfg(windows)]
impl Drop for AudioSchedulingGuard {
  fn drop(&mut self) { unsafe { AvRevertMmThreadCharacteristics(self.handle); } }
}

#[cfg(target_family = "unix")]
pub fn set_current_thread_realtime(priority_hint: u8) -> Result<(), Error> {
  use thread_priority::unix::set_thread_priority_and_policy;
  use thread_priority::{
    RealtimeThreadSchedulePolicy, ThreadPriority, ThreadPriorityValue, ThreadSchedulePolicy,
  };

  set_thread_priority_and_policy(
    thread_native_id(),
    ThreadPriority::Crossplatform(ThreadPriorityValue::try_from(priority_hint).unwrap()),
    ThreadSchedulePolicy::Realtime(RealtimeThreadSchedulePolicy::Fifo),
  )
}

#[cfg(not(target_family = "unix"))]
pub fn set_current_thread_realtime(_priority_hint: u8) -> Result<(), Error> {
  // Leave Windows critical system threads above this experimental audio worker.
  thread_priority::set_current_thread_priority(thread_priority::ThreadPriority::Crossplatform(
    thread_priority::ThreadPriorityValue::try_from(75u8).unwrap(),
  ))
}
