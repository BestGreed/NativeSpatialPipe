//! Preallocated PCM blocks: native ownership transfer or device ABI leases.
use std::{ffi::c_void, sync::mpsc::SyncSender};
pub static RELEASED: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static RECYCLE_FAILED: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

#[cfg(any(test, feature = "experimental-aoip-pool"))]
#[path = "output_pool/network.rs"]
pub mod network;
#[cfg(any(test, feature = "aoip-output"))]
#[path = "output_pool/aoip_source.rs"]
pub mod aoip_source;
#[cfg(any(test, feature = "aoip-output"))]
#[path = "output_pool/output_route.rs"]
pub mod output_route;
#[cfg(feature = "aoip-output")]
#[path = "output_pool/aoip.rs"]
pub mod aoip;

pub struct Block {
    pub channels: usize,
    pub data: Vec<f32>,
    pub recycle: SyncSender<Box<Block>>,
}
pub type Process = unsafe extern "C" fn(*mut c_void, u32, *mut f32, u32, u32, *mut u32) -> i32;
pub type Release = unsafe extern "C" fn(*mut c_void);
pub type Publish = unsafe extern "C" fn(*mut c_void, u64, *const f32, u32, u32, u32, *mut c_void, Release) -> i32;
pub enum Destination {
    Device { publish:Publish, context:usize },
    #[cfg(feature="aoip-output")]
    Aoip(aoip::Output),
    #[cfg(feature="aoip-output")]
    RoutedAoip(aoip::RoutedOutput),
}
pub struct Sink { pub lsac_eq: Option<nsp_lsac_adapter::loudness::Loudness>, pub process: Option<Process>, pub native: Option<crate::output_processing::Processor>, pub destination:Destination, pub context: usize }

unsafe extern "C" fn release(token: *mut c_void) {
    RELEASED.fetch_add(1,std::sync::atomic::Ordering::Relaxed);
    // Called exactly once after successful publication, never for rejected blocks.
    let block = unsafe { Box::from_raw(token.cast::<Block>()) };
    let recycle = block.recycle.clone();
    // After renderer/writer teardown the receiver is gone; dropping the returned
    // block is then correct. No Pipeline pointer is retained by a lease.
    if let Err(std::sync::mpsc::TrySendError::Full(_)) = recycle.try_send(block) {
        RECYCLE_FAILED.fetch_add(1,std::sync::atomic::Ordering::Relaxed);
        eprintln!("Output pool lease recycle unexpectedly full");
    }
}
impl Sink {
    pub fn process(&mut self, block: &mut Block, finish: bool) -> Result<usize,String> {
        let capacity = (block.data.len()/block.channels) as u32;
        if let Some(processor)=self.native.as_mut() {
            if block.channels!=processor.channels(){return Err("Pool DSP channel mismatch".into());}
            return if finish {processor.finish();processor.drain(&mut block.data)}else{
                if let Some(eq) = &mut self.lsac_eq {
                    eq.process(&mut block.data);
                    processor.report_adapter_gain(eq.gains_db());
                }
                processor.process(&mut block.data)?;Ok(capacity as usize)
            };
        }
        let mut frames = capacity;
        let code = unsafe { (self.process.ok_or("Missing pool processor")?)(self.context as *mut c_void, u32::from(finish), block.data.as_mut_ptr(), capacity, block.channels as u32, &mut frames) };
        if code != 0 || frames > capacity || (!finish && frames != capacity) {
            return Err(format!("Pool DSP rejected PCM: {code}"));
        }
        Ok(frames as usize)
    }
    pub fn publish(&self, block: Box<Block>, first: u64, frames: usize, tail: bool) -> Result<(),String> {
        let (publish,context)=match &self.destination {
            Destination::Device{publish,context}=>(*publish,*context),
            #[cfg(feature="aoip-output")]
            Destination::Aoip(output)=>return output.publish(block,first,frames,tail),
            #[cfg(feature="aoip-output")]
            Destination::RoutedAoip(output)=>return output.publish(block,first,frames,tail),
        };
        let pcm = block.data.as_ptr();
        let channels = block.channels;
        let token = Box::into_raw(block);
        // After this call succeeds we MUST NOT dereference token: the consumer
        // may already have released/reused it on another thread.
        let code = unsafe { publish(context as *mut c_void, first, pcm, frames as u32, channels as u32,
            u32::from(tail), token.cast(), release) };
        if code != 0 {
            unsafe { drop(Box::from_raw(token)); }
            return Err(format!("Pool publication rejected PCM: {code}"));
        }
        Ok(())
    }
    pub fn finish(&self)->Result<(),String> {
        match &self.destination {
            #[cfg(feature="aoip-output")]
            Destination::Aoip(output)=>output.finish(),
            #[cfg(feature="aoip-output")]
            Destination::RoutedAoip(output)=>output.finish(),
            _=>Ok(()),
        }
    }
}
