//! Opt-in in-process Inferno backend. Control configuration crosses the ABI;
//! PCM ownership stays inside Rust from DSP through packet encoding.
use super::{Block, aoip_source::{Adapter, Message, Metrics, Publisher}};
use inferno_aoip::device_server::{DeviceServer, PacketSource, Settings};
use std::{collections::BTreeMap, net::Ipv4Addr, sync::{Arc, Mutex, Weak, atomic::{AtomicBool, Ordering}, mpsc},
    thread::{self, JoinHandle}, time::{Duration, Instant}};

#[repr(C)]
#[derive(Clone,Copy)]
pub struct NativeConfig {
    pub size:u32, pub version:u32, pub interface_ip:[u8;4], pub master_ip:[u8;4],
    pub mac:[u8;12], pub name:[u8;32], pub prebuffer_frames:u32, pub tx_latency_us:u32, pub frames_per_packet:u32,
}
#[derive(Clone,PartialEq,Eq)]
pub struct Config {
    pub interface_ip:Ipv4Addr, pub master_ip:Ipv4Addr, pub mac:String,
    pub name:String, pub prebuffer_frames:usize, pub tx_latency_us:u32, pub frames_per_packet:u32,
}
impl Config {
    pub fn from_native(n:NativeConfig)->Result<Self,String> {
        if n.size as usize!=std::mem::size_of::<NativeConfig>() || n.version!=2 {
            return Err("AoIP configuration ABI version/size".into());
        }
        let end=n.name.iter().position(|b|*b==0).ok_or("AoIP name not terminated")?;
        let c=Self{interface_ip:Ipv4Addr::from(n.interface_ip),master_ip:Ipv4Addr::from(n.master_ip),
            mac:String::from_utf8(n.mac.to_vec()).map_err(|e|e.to_string())?,
            name:String::from_utf8(n.name[..end].to_vec()).map_err(|e|e.to_string())?,
            prebuffer_frames:n.prebuffer_frames as usize,tx_latency_us:n.tx_latency_us,frames_per_packet:n.frames_per_packet};
        if c.mac.len()!=12 || !c.mac.bytes().all(|b|b.is_ascii_hexdigit()) ||
            c.name.is_empty() || c.name.len()>31 || !c.name.bytes().all(|b|(32..=126).contains(&b)) ||
            c.interface_ip.is_unspecified() || c.interface_ip.is_multicast() || c.interface_ip.is_loopback() ||
            c.master_ip.is_multicast() ||
            !(32..=4800).contains(&c.prebuffer_frames) || !(250..=100000).contains(&c.tx_latency_us) ||
            (c.frames_per_packet!=0 && !(2..=256).contains(&c.frames_per_packet)) {
            return Err("Invalid AoIP interface/master/MAC/name/prebuffer".into());
        }
        Ok(c)
    }
}
struct NativeSource(Adapter);
#[cfg(test)]
mod config_tests {
    use super::*;
    #[test]
    fn versioned_json_settings_are_validated_before_starting_network() {
        let mut n=NativeConfig{size:std::mem::size_of::<NativeConfig>() as u32,version:2,
            interface_ip:[192,168,50,1],master_ip:[192,168,50,2],mac:*b"f0d7afd04b1d",name:[0;32],
            prebuffer_frames:960,tx_latency_us:1200,frames_per_packet:0};
        n.name[..3].copy_from_slice(b"NSP");n.prebuffer_frames=64;
        assert_eq!(std::mem::size_of::<NativeConfig>(),72);
        let c=Config::from_native(n).unwrap();assert_eq!(c.prebuffer_frames,64);assert_eq!(c.tx_latency_us,1200);assert_eq!(c.frames_per_packet,0);
        n.master_ip = [0; 4];
        assert!(Config::from_native(n).unwrap().master_ip.is_unspecified());
        n.prebuffer_frames=31;assert!(Config::from_native(n).is_err());n.prebuffer_frames=64;
        n.frames_per_packet=1;assert!(Config::from_native(n).is_err());
        n.frames_per_packet=257;assert!(Config::from_native(n).is_err());
        n.frames_per_packet=32;n.tx_latency_us=100001;assert!(Config::from_native(n).is_err());
        n.tx_latency_us=4000;n.version=1;assert!(Config::from_native(n).is_err());
    }
}
impl PacketSource for NativeSource {
    fn poll(&mut self){self.0.poll();}
    fn prepare(&mut self, flow:usize, timestamp:u64, frames:usize, channels:&[Option<usize>], bytes:usize, payload:&mut[u8])->Result<bool,String> {
        self.0.prepare(flow,timestamp,frames,channels,bytes,payload)
    }
    fn commit(&mut self,flow:usize){self.0.commit(flow);}
    fn remove(&mut self,flow:usize){self.0.remove(flow);}
}
struct Runtime {
    publisher:Publisher, stop:Arc<AtomicBool>, thread:Option<JoinHandle<()>>,
    config:Config, channels:usize, active:AtomicBool,
}
impl Runtime {
    pub fn start(config:Config,channels:usize)->Result<Self,String> {
        let key=config.clone();
        let (tx,rx)=mpsc::sync_channel(32);
        let metrics=Arc::new(Metrics::default());
        let source=Adapter::new(channels,32,config.prebuffer_frames,rx,metrics.clone())?;
        let stop=Arc::new(AtomicBool::new(false));let worker_stop=stop.clone();let worker_metrics=metrics.clone();
        let (ready_tx,ready_rx)=mpsc::sync_channel(1);
        let worker=thread::Builder::new().name("NSP AoIP control".into()).spawn(move || {
            let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let runtime=tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
                runtime.block_on(async move {
                    let mut map=BTreeMap::new();
                    let master = if config.master_ip.is_unspecified() {
                        String::new()
                    } else {
                        config.master_ip.to_string()
                    };
                    map.insert("CLOCK_PATH".into(),format!("{},{master},{}",config.interface_ip,config.mac));
                    map.insert("NAME".into(),config.name);
                    map.insert("ALT_PORT".into(),"18000".into());
                    map.insert("PROCESS_ID".into(),"2".into());
                    map.insert("TX_LATENCY_NS".into(),(config.tx_latency_us*1000).to_string());
                    map.insert("TX_FRAMES_PER_PACKET".into(),config.frames_per_packet.to_string());
                    let mut settings=Settings::new("NSP Native AoIP","NSPNative",Some(config.interface_ip),&map);
                    settings.make_tx_channels(channels);settings.make_rx_channels(0);
                    let mut server=DeviceServer::start(settings).await;
                    server.transmit_packet_source(Box::new(NativeSource(source))).await;
                    let _=ready_tx.send(());
                    while !worker_stop.load(Ordering::Acquire) {
                        tokio::time::sleep(Duration::from_millis(2)).await;
                    }
                    server.shutdown().await;
                });
            }));
            if result.is_err(){worker_metrics.fail(6);}
        }).map_err(|e|e.to_string())?;
        let out=Self{publisher:Publisher{tx,metrics},stop,thread:Some(worker),config:key,channels,active:AtomicBool::new(false)};
        if ready_rx.recv_timeout(Duration::from_secs(15)).is_err(){
            // Retain/join the owning runtime on all startup failures.
            out.stop.store(true,Ordering::Release);
            return Err("AoIP control startup failed; inspect ports and PTP log".into());
        }
        Ok(out)
    }
    pub fn publish(&self, block:Box<Block>,first:u64,frames:usize,tail:bool)->Result<(),String> {
        self.publisher.publish(first,frames,tail,block)
    }
    pub fn finish(&self)->Result<(),String> {
        let deadline=Instant::now()+Duration::from_secs(3);
        loop {
            match self.publisher.tx.try_send(Message::Finish) {
                Ok(())=>break,
                Err(mpsc::TrySendError::Full(_)) if Instant::now()<deadline=>thread::sleep(Duration::from_millis(2)),
                Err(_)=>return Err("AoIP EOF handoff failed".into()),
            }
        }
        while !self.publisher.metrics.drained.load(Ordering::Acquire) && Instant::now()<deadline {
            if self.publisher.metrics.fault.load(Ordering::Acquire)!=0 {break;}
            thread::sleep(Duration::from_millis(2));
        }
        let m=&self.publisher.metrics;
        if m.fault.load(Ordering::Acquire)!=0 {return Err(format!("AoIP source fault {}",m.fault.load(Ordering::Acquire)));}
        if !m.drained.load(Ordering::Acquire){return Err("AoIP EOF drain timed out (PTP or receiver unavailable)".into());}
        Ok(())
    }
    pub fn metrics(&self)->Arc<Metrics>{self.publisher.metrics.clone()}
}
impl Drop for Runtime {
    fn drop(&mut self){self.stop.store(true,Ordering::Release);if let Some(worker)=self.thread.take(){let _=worker.join();}}
}

static RESIDENT:Mutex<Weak<Runtime>>=Mutex::new(Weak::new());
fn resident(config:Config,channels:usize)->Result<Arc<Runtime>,String>{
    let mut guard=RESIDENT.lock().map_err(|_|"AoIP resident lock poisoned")?;
    if let Some(runtime)=guard.upgrade(){
        if runtime.config!=config || runtime.channels!=channels{return Err("AoIP standby configuration differs; restart FSA to apply network/layout changes".into());}
        return Ok(runtime);
    }
    let runtime=Arc::new(Runtime::start(config,channels)?);*guard=Arc::downgrade(&runtime);Ok(runtime)
}
pub struct Output { runtime:Arc<Runtime> }
impl Output {
    pub fn start(config:Config,channels:usize)->Result<Self,String>{
        let runtime=resident(config,channels)?;
        if runtime.active.compare_exchange(false,true,Ordering::AcqRel,Ordering::Acquire).is_err(){return Err("AoIP PCM source already attached".into());}
        runtime.publisher.metrics.drained.store(false,Ordering::Release);
        if runtime.publisher.tx.try_send(Message::Reset).is_err(){runtime.active.store(false,Ordering::Release);return Err("AoIP source reset handoff failed".into());}
        Ok(Self{runtime})
    }
    pub fn publish(&self,block:Box<Block>,first:u64,frames:usize,tail:bool)->Result<(),String>{self.runtime.publish(block,first,frames,tail)}
    pub fn finish(&self)->Result<(),String>{self.runtime.finish()}
    pub fn metrics(&self)->Arc<Metrics>{self.runtime.metrics()}
}
impl Drop for Output {fn drop(&mut self){self.runtime.active.store(false,Ordering::Release);}}
struct Standby { _runtime:Arc<Runtime> }
struct RouteSession { runtime:Arc<Runtime>, router:super::output_route::Router }
impl Drop for RouteSession {fn drop(&mut self){self.runtime.active.store(false,Ordering::Release);}}
pub struct RoutedOutput { session:Arc<RouteSession>, source:usize }
impl RoutedOutput {
    pub unsafe fn attach(handle:*const std::ffi::c_void,source:usize,channels:usize,budget:usize)->Result<Self,String>{
        if handle.is_null() || source>=4{return Err("Invalid output route handle/source".into());}
        let session=unsafe{&*handle.cast::<Arc<RouteSession>>()}.clone();
        if session.runtime.channels!=channels{return Err("Output route channel mismatch".into());}
        if session.runtime.config.prebuffer_frames>budget{return Err("Output route prebuffer exceeds source pool".into());}
        Ok(Self{session,source})
    }
    pub fn publish(&self,block:Box<Block>,first:u64,frames:usize,tail:bool)->Result<(),String>{self.session.router.publish(self.source,first,frames,tail,block)}
    pub fn finish(&self)->Result<(),String>{if self.session.router.finish_source(self.source){self.session.runtime.finish()}else{Ok(())}}
    pub fn metrics(&self)->Arc<Metrics>{self.session.runtime.metrics()}
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aoip_route_create(config:*const NativeConfig,channels:u32)->*mut std::ffi::c_void {
    std::panic::catch_unwind(||{
        if config.is_null(){return std::ptr::null_mut();}
        let Ok(runtime)=Config::from_native(unsafe{*config}).and_then(|c|resident(c,channels as usize)) else{return std::ptr::null_mut();};
        if runtime.active.compare_exchange(false,true,Ordering::AcqRel,Ordering::Acquire).is_err(){return std::ptr::null_mut();}
        if runtime.publisher.tx.try_send(Message::Reset).is_err(){runtime.active.store(false,Ordering::Release);return std::ptr::null_mut();}
        let router=super::output_route::Router::new(Publisher{tx:runtime.publisher.tx.clone(),metrics:runtime.metrics()});
        Box::into_raw(Box::new(Arc::new(RouteSession{runtime,router}))).cast()
    }).unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aoip_route_select(handle:*const std::ffi::c_void,source:u32)->i32 {
    if handle.is_null() || (source>=4 && source!=u32::MAX){return -1;}
    std::panic::catch_unwind(||{
        let s=unsafe{&*handle.cast::<Arc<RouteSession>>()};
        s.router.select(if source==u32::MAX{None}else{Some(source as usize)}).map_or(-1,|_|0)
    }).unwrap_or(-1)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aoip_route_destroy(handle:*mut std::ffi::c_void){
    if !handle.is_null(){unsafe{drop(Box::from_raw(handle.cast::<Arc<RouteSession>>()));}}
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aoip_standby_create(config:*const NativeConfig,channels:u32)->*mut std::ffi::c_void {
    std::panic::catch_unwind(||{
        if config.is_null(){return std::ptr::null_mut();}
        let result=Config::from_native(unsafe{*config}).and_then(|c|resident(c,channels as usize));
        match result {Ok(runtime)=>Box::into_raw(Box::new(Standby{_runtime:runtime})).cast(),Err(_)=>std::ptr::null_mut()}
    }).unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aoip_standby_destroy(handle:*mut std::ffi::c_void){
    if !handle.is_null(){unsafe{drop(Box::from_raw(handle.cast::<Standby>()));}}
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aoip_standby_status(handle:*const std::ffi::c_void,out:*mut u64)->i32 {
    if handle.is_null() || out.is_null(){return -1;}
    let r=&unsafe{&*handle.cast::<Standby>()}._runtime;let m=&r.publisher.metrics;
    let values=[m.fault.load(Ordering::Acquire) as u64,m.sent.load(Ordering::Relaxed),
        m.accepted.load(Ordering::Relaxed),m.recycled.load(Ordering::Relaxed),
        m.silence_packets.load(Ordering::Relaxed),m.underflows.load(Ordering::Relaxed),
        m.clock_jumps.load(Ordering::Relaxed),u64::from(r.active.load(Ordering::Acquire))];
    unsafe{std::ptr::copy_nonoverlapping(values.as_ptr(),out,values.len());}0
}
