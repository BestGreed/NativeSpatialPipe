//! Optional pre-DSP side recording. Live submission never waits for disk or
//! retains a playback pool lease. Only the dedicated recorder owns the file.
use std::{fs::File,io::{BufWriter,Write},sync::{Arc,atomic::{AtomicBool,AtomicU64,Ordering},mpsc::{self,Receiver,SyncSender}},thread::{self,JoinHandle},time::Instant};
const CAPACITY:usize=32;
#[derive(Default)]
struct Counters{failed:AtomicBool,queued:AtomicU64,dropped:AtomicU64,written:AtomicU64,frames:AtomicU64}
pub struct Capture{
    enabled:bool,reliable:bool,channels:usize,counters:Arc<Counters>,
    free:Option<Receiver<Vec<f32>>>,ready:Option<SyncSender<Vec<f32>>>,
    worker:Option<JoinHandle<Result<u64,String>>>,error:Option<String>,flush_ns:u64,
}
impl Capture{
    pub fn disabled()->Self{Self{enabled:false,reliable:false,channels:0,counters:Arc::new(Counters::default()),free:None,ready:None,worker:None,error:None,flush_ns:0}}
    pub fn failed(error:String)->Self{let mut s=Self::disabled();s.enabled=true;s.error=Some(error);s.counters.failed.store(true,Ordering::Release);s}
    pub fn file(path:&str,samples:usize,channels:usize,reliable:bool)->Result<Self,String>{
        let file=File::create_new(path).map_err(|e|e.to_string())?;
        Self::start(samples,channels,reliable,move |rx,free,stats|{
            let mut writer=BufWriter::with_capacity(1024*1024,file);
            Self::write_loop(&mut writer,rx,free,&stats,channels)?;
            let now=Instant::now();writer.flush().map_err(|e|e.to_string())?;
            writer.get_ref().sync_all().map_err(|e|e.to_string())?;
            Ok(now.elapsed().as_nanos() as u64)
        })
    }
    fn start<F>(samples:usize,channels:usize,reliable:bool,run:F)->Result<Self,String>
    where F:FnOnce(Receiver<Vec<f32>>,SyncSender<Vec<f32>>,Arc<Counters>)->Result<u64,String>+Send+'static{
        let (return_tx,free)=mpsc::sync_channel(CAPACITY);
        let (ready,rx)=mpsc::sync_channel(CAPACITY);
        for _ in 0..CAPACITY{return_tx.send(vec![0.;samples]).map_err(|e|e.to_string())?;}
        let counters=Arc::new(Counters::default());let stats=counters.clone();
        let worker=thread::Builder::new().name("nsp-pcm-recorder".into()).spawn(move||{
            let result=run(rx,return_tx,stats.clone());
            if result.is_err(){stats.failed.store(true,Ordering::Release);}result
        }).map_err(|e|e.to_string())?;
        Ok(Self{enabled:true,reliable,channels,counters,free:Some(free),ready:Some(ready),worker:Some(worker),error:None,flush_ns:0})
    }
    fn write_loop(writer:&mut impl Write,rx:Receiver<Vec<f32>>,free:SyncSender<Vec<f32>>,stats:&Counters,channels:usize)->Result<(),String>{
        while let Ok(block)=rx.recv(){
            let bytes=unsafe{std::slice::from_raw_parts(block.as_ptr().cast::<u8>(),block.len()*4)};
            writer.write_all(bytes).map_err(|e|e.to_string())?;
            stats.written.fetch_add(1,Ordering::Relaxed);stats.frames.fetch_add((block.len()/channels) as u64,Ordering::Relaxed);
            // A returned recording block never refers to playback memory.
            if free.try_send(block).is_err(){return Err("Recorder recycle failed".into());}
        }Ok(())
    }
    pub fn submit(&mut self,pcm:&[f32]){
        if !self.enabled{return;}
        if self.counters.failed.load(Ordering::Acquire){self.counters.dropped.fetch_add(1,Ordering::Relaxed);return;}
        let free=self.free.as_ref().unwrap();
        let block=if self.reliable{free.recv().ok()}else{free.try_recv().ok()};
        let Some(mut block)=block else{self.counters.dropped.fetch_add(1,Ordering::Relaxed);return;};
        if pcm.len()>block.capacity(){self.counters.dropped.fetch_add(1,Ordering::Relaxed);return;}
        block.resize(pcm.len(),0.);block.copy_from_slice(pcm);
        if self.ready.as_ref().unwrap().try_send(block).is_ok(){self.counters.queued.fetch_add(1,Ordering::Relaxed);}
        else{self.counters.dropped.fetch_add(1,Ordering::Relaxed);}
    }
    pub fn finish(&mut self){
        self.ready.take();
        if let Some(worker)=self.worker.take(){match worker.join(){Ok(Ok(ns))=>self.flush_ns=ns,Ok(Err(e))=>self.error=Some(e),Err(_)=>self.error=Some("Recorder thread panicked".into())}}
    }
    pub fn incomplete(&self)->bool{self.error.is_some() || self.counters.dropped.load(Ordering::Relaxed)>0}
    pub fn json(&self)->String{
        let error=self.error.as_ref().map(|s|{
            let mut text=String::from("\"");for ch in s.chars(){match ch{'"'=>text.push_str("\\\""),'\\'=>text.push_str("\\\\"),c if c<' '=>text.push_str(&format!("\\u{:04x}",c as u32)),c=>text.push(c)}}text.push('"');text
        }).unwrap_or_else(||"null".into());
        format!("{{\"enabled\":{},\"mode\":\"{}\",\"capacityBlocks\":{},\"queuedBlocks\":{},\"writtenBlocks\":{},\"droppedBlocks\":{},\"writtenFrames\":{},\"channels\":{},\"error\":{},\"flushSyncMs\":{}}}",self.enabled,if self.reliable{"offline-lossless"}else{"live-best-effort"},if self.enabled{CAPACITY}else{0},self.counters.queued.load(Ordering::Relaxed),self.counters.written.load(Ordering::Relaxed),self.counters.dropped.load(Ordering::Relaxed),self.counters.frames.load(Ordering::Relaxed),self.channels,error,self.flush_ns as f64/1e6)
    }
}
impl Drop for Capture{fn drop(&mut self){self.finish();}}

#[cfg(test)]mod tests{
    use super::*;
    #[test]fn blocked_disk_drops_only_recording_and_returns_immediately(){
        let (entered_tx,entered_rx)=mpsc::channel();let (gate_tx,gate_rx)=mpsc::channel();
        let mut record=Capture::start(480*16,16,false,move|rx,free,stats|{
            entered_tx.send(()).unwrap();gate_rx.recv().unwrap();
            Capture::write_loop(&mut std::io::sink(),rx,free,&stats,16)?;Ok(0)
        }).unwrap();entered_rx.recv().unwrap();
        let pcm=vec![0.25;480*16];let start=Instant::now();
        for _ in 0..1000{record.submit(&pcm);}
        assert!(start.elapsed().as_millis()<100,"Disk wait leaked to submit");
        assert_eq!(record.counters.queued.load(Ordering::Relaxed),CAPACITY as u64);
        assert_eq!(record.counters.dropped.load(Ordering::Relaxed),1000-CAPACITY as u64);
        gate_tx.send(()).unwrap();record.finish();
        assert_eq!(record.counters.written.load(Ordering::Relaxed),CAPACITY as u64);
    }
    #[test]fn disk_failure_is_not_a_playback_error(){
        let mut record=Capture::start(480,1,false,|_,_,_|Err("Simulated disk full".into())).unwrap();
        record.finish();record.submit(&[0.;480]);
        assert!(record.json().contains("Simulated disk full"));
        assert_eq!(record.counters.dropped.load(Ordering::Relaxed),1);
    }
}
