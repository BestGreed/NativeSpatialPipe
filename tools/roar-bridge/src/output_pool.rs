//! Preallocated PCM leases. Ownership crosses the C ABI; PCM never does.
use std::{ffi::c_void, sync::mpsc::SyncSender};

pub struct Block {
    pub data: Vec<f32>,
    pub recycle: SyncSender<Box<Block>>,
}
pub type Process = unsafe extern "C" fn(*mut c_void, u32, *mut f32, u32, u32, *mut u32) -> i32;
pub type Release = unsafe extern "C" fn(*mut c_void);
pub type Publish = unsafe extern "C" fn(*mut c_void, u64, *const f32, u32, u32, u32, *mut c_void, Release) -> i32;
#[derive(Clone, Copy)]
pub struct Sink { pub process: Process, pub publish: Publish, pub context: usize }

unsafe extern "C" fn release(token: *mut c_void) {
    // Called exactly once after successful publication, never for rejected blocks.
    let block = unsafe { Box::from_raw(token.cast::<Block>()) };
    let recycle = block.recycle.clone();
    // After renderer/writer teardown the receiver is gone; dropping the returned
    // block is then correct. No Pipeline pointer is retained by a lease.
    let _ = recycle.try_send(block);
}
impl Sink {
    pub fn process(&self, block: &mut Block, finish: bool) -> Result<usize,String> {
        let capacity = (block.data.len()/12) as u32;
        let mut frames = capacity;
        let code = unsafe { (self.process)(self.context as *mut c_void, u32::from(finish), block.data.as_mut_ptr(), capacity, 12, &mut frames) };
        if code != 0 || frames > capacity || (!finish && frames != capacity) {
            return Err(format!("Pool DSP rejected PCM: {code}"));
        }
        Ok(frames as usize)
    }
    pub fn publish(&self, block: Box<Block>, first: u64, frames: usize, tail: bool) -> Result<(),String> {
        let pcm = block.data.as_ptr();
        let token = Box::into_raw(block);
        // After this call succeeds we MUST NOT dereference token: the consumer
        // may already have released/reused it on another thread.
        let code = unsafe { (self.publish)(self.context as *mut c_void, first, pcm, frames as u32, 12,
            u32::from(tail), token.cast(), release) };
        if code != 0 {
            unsafe { drop(Box::from_raw(token)); }
            return Err(format!("Pool publication rejected PCM: {code}"));
        }
        Ok(())
    }
}
