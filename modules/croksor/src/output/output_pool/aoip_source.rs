//! Single-owner pool -> datagram adapter. The writer moves blocks through a
//! bounded channel; only the network thread touches Source or flow cursors.
use super::{Block, network::{Source, Prepared, MAX_FLOWS}};
use std::sync::{Arc, atomic::{AtomicU64, AtomicU32, AtomicBool, Ordering},
    mpsc::{Receiver, SyncSender, TrySendError}};

pub enum Message {
    Block { first:u64, frames:usize, tail:bool, block:Box<Block> }, Finish, Reset,
    SelectEpoch{epoch:u64},
    RoutedBlock{epoch:u64,first:u64,frames:usize,tail:bool,block:Box<Block>},
}
#[derive(Default)]
pub struct Metrics {
    pub fault:AtomicU32, pub accepted:AtomicU64, pub sent:AtomicU64,
    pub recycled:AtomicU64, pub drained:AtomicBool,
    pub silence_packets:AtomicU64, pub underflows:AtomicU64, pub clock_jumps:AtomicU64,
}
// Output fault codes: 1=handoff unavailable, 2=pool contract,
// 4=invalid anchor range, 5=unsupported format. Recoverable source gaps and
// ordinary clock discontinuities have separate counters and do not fail capture.
impl Metrics {
    pub fn fail(&self, code:u32) { let _=self.fault.compare_exchange(0,code,Ordering::AcqRel,Ordering::Acquire); }
}
pub struct Publisher { pub tx:SyncSender<Message>, pub metrics:Arc<Metrics> }
pub(super) fn recycle(block:Box<Block>) {let tx=block.recycle.clone();let _=tx.try_send(block);}
impl Publisher {
    pub fn publish_routed(&self,epoch:u64,first:u64,frames:usize,tail:bool,block:Box<Block>)->Result<(),String>{
        if self.metrics.fault.load(Ordering::Acquire)!=0{recycle(block);return Ok(());}
        match self.tx.try_send(Message::RoutedBlock{epoch,first,frames,tail,block}){
            Ok(())=>Ok(()),
            Err(TrySendError::Full(Message::RoutedBlock{block,..})|TrySendError::Disconnected(Message::RoutedBlock{block,..}))=>{
                recycle(block);self.metrics.fail(1);Ok(())
            },_=>unreachable!(),
        }
    }
    pub fn publish(&self, first:u64, frames:usize, tail:bool, block:Box<Block>)->Result<(),String> {
        if self.metrics.fault.load(Ordering::Acquire)!=0 {
            // Network health is reported separately. Do not fail the capture
            // producer or retain its pool while the output is unavailable.
            recycle(block);return Ok(());
        }
        match self.tx.try_send(Message::Block{first,frames,tail,block}) {
            Ok(())=>Ok(()),
            Err(TrySendError::Full(Message::Block{block,..}) | TrySendError::Disconnected(Message::Block{block,..}))=>{
                recycle(block);self.metrics.fail(1);Ok(())
            },
            _=>unreachable!(),
        }
    }
}
struct Flow { index:usize, started:bool, next_ts:Option<u64> }
pub struct Adapter {
    source:Source, rx:Receiver<Message>, metrics:Arc<Metrics>, prebuffer:usize,
    channels:usize, capacity:usize,
    flows:[Option<Flow>;MAX_FLOWS], prepared:[Option<(Option<Prepared>,u64)>;MAX_FLOWS],
    anchor:Option<(u64,u64)>,
    epoch:u64,
}
impl Adapter {
    pub fn new(channels:usize, capacity:usize, prebuffer:usize, rx:Receiver<Message>, metrics:Arc<Metrics>)->Result<Self,String> {
        if !(32..=4800).contains(&prebuffer){return Err("AoIP prebuffer out of range".into());}
        Ok(Self{source:Source::new(channels,capacity).map_err(|e|format!("{e:?}"))?,rx,metrics,prebuffer,channels,capacity,
            flows:std::array::from_fn(|_|None),prepared:[None;MAX_FLOWS],anchor:None,epoch:0})
    }
    fn update(&self) {
        let (accepted,recycled,_)=self.source.counts();
        self.metrics.accepted.store(accepted,Ordering::Relaxed);
        self.metrics.recycled.store(recycled,Ordering::Relaxed);
        self.metrics.drained.store(self.source.drained(),Ordering::Release);
    }
    fn fail<T>(&mut self,code:u32,message:&str)->Result<T,String> {
        self.metrics.fail(code);self.source.abort();self.update();Err(message.into())
    }
    pub fn poll(&mut self) {
        for _ in 0..256 {
            match self.rx.try_recv() {
                Ok(Message::Block{first,frames,tail,block})=>{
                    if self.metrics.fault.load(Ordering::Acquire)!=0 {recycle(block);continue;}
                    if let Err(rejected)=self.source.push(first,frames,tail,block) {
                        recycle(rejected.block);self.metrics.fail(2);self.source.abort();
                    }
                },
                Ok(Message::RoutedBlock{epoch,first,frames,tail,block})=>{
                    if epoch!=self.epoch || self.metrics.fault.load(Ordering::Acquire)!=0{recycle(block);continue;}
                    if let Err(rejected)=self.source.push(first,frames,tail,block){recycle(rejected.block);self.metrics.fail(2);self.source.abort();}
                },
                Ok(Message::Finish)=>self.source.finish(),
                Ok(Message::SelectEpoch{epoch})=>{self.reset();self.epoch=epoch;},
                Ok(Message::Reset)=>{self.reset();self.epoch=0;},
                Err(_)=>break,
            }
        }
        if self.metrics.fault.load(Ordering::Acquire)!=0 {self.source.abort();}
        self.update();
    }
    fn reset(&mut self){
                    self.source=Source::new(self.channels,self.capacity).unwrap();
                    self.prepared.fill(None);self.anchor=None;
                    for flow in self.flows.iter_mut().flatten(){
                        flow.index=self.source.add_flow(0).unwrap();flow.started=false;
                    }
                    self.metrics.fault.store(0,Ordering::Release);
    }
    pub fn prepare(&mut self, flow:usize, timestamp:u64, frames:usize, channels:&[Option<usize>], bytes:usize, payload:&mut[u8])->Result<bool,String> {
        self.poll();
        if self.metrics.fault.load(Ordering::Acquire)!=0 {return Err("AoIP source fault".into());}
        if flow>=MAX_FLOWS || bytes!=3 || !(2..=256).contains(&frames) || channels.is_empty() || channels.len()>8 ||
            channels.iter().flatten().any(|c|*c>=self.channels) || payload.len()<frames*channels.len()*3 {
            return self.fail(5,"Native AoIP requires valid 24-bit packet mapping");
        }
        if self.flows[flow].is_none() {
            let index=self.source.add_flow(self.source.next_frame()).map_err(|e|format!("{e:?}"))?;
            self.flows[flow]=Some(Flow{index,started:false,next_ts:None});
        }
        let f=self.flows[flow].as_ref().unwrap();let index=f.index;
        if f.next_ts.is_some_and(|ts|ts!=timestamp) {
            self.metrics.clock_jumps.fetch_add(1,Ordering::Relaxed);
            self.rebuffer();
            for f in self.flows.iter_mut().flatten(){f.next_ts=None;}
        }
        let f=self.flows[flow].as_ref().unwrap();
        if self.source.drained(){return self.silence(flow,timestamp,frames,payload);}
        if !f.started {
            let available=self.source.available(index).map_err(|e|format!("{e:?}"))?;
            if available < self.prebuffer as u64 && !self.source.is_finished(){return self.silence(flow,timestamp,frames,payload);}
            if available==0 {return self.silence(flow,timestamp,frames,payload);}
            if let Some((network,source))=self.anchor {
                let target=source as i128 + timestamp as i128-network as i128;
                if !(0..=u64::MAX as i128).contains(&target){return self.fail(4,"AoIP timestamp before anchor");}
                if self.source.seek(index,target as u64).is_err(){return self.silence(flow,timestamp,frames,payload);}
            } else {
                self.anchor=Some((timestamp,self.source.cursor(index).unwrap()));
            }
            self.flows[flow].as_mut().unwrap().started=true;
        }
        let available=self.source.available(index).unwrap();
        if available==0 && self.source.is_finished(){return self.silence(flow,timestamp,frames,payload);}
        if available < frames as u64 && !self.source.is_finished() {
            self.metrics.underflows.fetch_add(1,Ordering::Relaxed);
            self.rebuffer();
            return self.silence(flow,timestamp,frames,payload);
        }
        let packet=match self.source.prepare_s24_tail(index,frames,channels,payload) {
            Ok(p)=>p,Err(_)=>return self.fail(2,"AoIP packet mapping or PCM invalid"),
        };
        self.prepared[flow]=Some((Some(packet),timestamp+frames as u64));
        Ok(true)
    }
    fn silence(&mut self,flow:usize,timestamp:u64,frames:usize,payload:&mut[u8])->Result<bool,String>{
        payload.fill(0);self.prepared[flow]=Some((None,timestamp+frames as u64));Ok(true)
    }
    fn rebuffer(&mut self){
        let cursor=self.flows.iter().flatten().filter_map(|f|self.source.cursor(f.index).ok()).max().unwrap_or(self.source.next_frame());
        for f in self.flows.iter_mut().flatten(){let _=self.source.seek(f.index,cursor);f.started=false;}
        self.prepared.fill(None);self.anchor=None;
    }
    pub fn commit(&mut self, flow:usize) {
        if let Some((packet,next))=self.prepared.get_mut(flow).and_then(Option::take) {
            if packet.is_some_and(|packet|self.source.commit(packet).is_err()){self.metrics.fail(2);self.source.abort();}
            else {
                if packet.is_none(){self.metrics.silence_packets.fetch_add(1,Ordering::Relaxed);}
                if let Some(f)=self.flows[flow].as_mut(){f.next_ts=Some(next);}
                self.metrics.sent.fetch_add(1,Ordering::Relaxed);
            }
        }
        self.update();
    }
    pub fn remove(&mut self, flow:usize) {
        if flow>=MAX_FLOWS{return;}
        self.prepared[flow]=None;
        if let Some(f)=self.flows[flow].take(){let _=self.source.remove_flow(f.index);}
        if self.flows.iter().all(Option::is_none){self.anchor=None;}
        self.update();
    }
}
impl Drop for Adapter {
    fn drop(&mut self){self.source.abort();while let Ok(message)=self.rx.try_recv(){match message{Message::Block{block,..}|Message::RoutedBlock{block,..}=>recycle(block),_=>{}}}}
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::{sync_channel,Receiver};
    fn setup()->(Publisher,Adapter,SyncSender<Box<Block>>,Receiver<Box<Block>>) {
        let (tx,rx)=sync_channel(8);let m=Arc::new(Metrics::default());
        let (recycle,returned)=sync_channel(16);
        (Publisher{tx,metrics:m.clone()},Adapter::new(2,8,32,rx,m).unwrap(),recycle,returned)
    }
    fn block(tx:&SyncSender<Box<Block>>,frames:usize)->Box<Block> {
        Box::new(Block{channels:2,data:vec![0.5;frames*2],recycle:tx.clone()})
    }
    fn prepare(a:&mut Adapter,flow:usize,ts:u64,frames:usize)->Result<bool,String> {
        a.prepare(flow,ts,frames,&[Some(0),Some(1)],3,&mut[0;1536])
    }
    #[test]
    fn failed_send_retry_keeps_pool_until_commit_and_eof_pads() {
        let (p,mut a,tx,rx)=setup();
        assert!(prepare(&mut a,0,1000,32).unwrap());
        p.publish(0,35,false,block(&tx,35)).unwrap();
        assert!(prepare(&mut a,0,1000,32).unwrap());
        assert!(prepare(&mut a,0,1000,32).unwrap());
        assert!(rx.try_recv().is_err());a.commit(0);
        p.tx.send(Message::Finish).unwrap();
        let mut bytes=[0xff;192];
        assert!(a.prepare(0,1032,32,&[Some(0),Some(1)],3,&mut bytes).unwrap());
        assert!(bytes[18..].iter().all(|b|*b==0));
        a.commit(0);assert!(rx.try_recv().is_ok());
        assert!(p.metrics.drained.load(Ordering::Acquire));
        assert_eq!(p.metrics.sent.load(Ordering::Relaxed),2);
    }
    #[test]
    fn underflow_keeps_media_alive_and_records_rebuffer() {
        let(p,mut a,tx,rx)=setup();prepare(&mut a,0,0,32).unwrap();
        p.publish(0,32,false,block(&tx,32)).unwrap();
        assert!(prepare(&mut a,0,0,32).unwrap());a.commit(0);
        assert!(prepare(&mut a,0,32,32).unwrap());a.commit(0);
        assert_eq!(p.metrics.fault.load(Ordering::Acquire),0);
        assert_eq!(p.metrics.underflows.load(Ordering::Acquire),1);
        assert_eq!(p.metrics.silence_packets.load(Ordering::Acquire),1);
        assert!(rx.try_recv().is_ok());
    }
    #[test]
    fn same_network_time_uses_same_source_frames_across_flows() {
        let(p,mut a,tx,rx)=setup();prepare(&mut a,0,1000,32).unwrap();prepare(&mut a,1,1000,32).unwrap();
        p.publish(0,96,false,block(&tx,96)).unwrap();
        assert!(prepare(&mut a,0,1000,32).unwrap());a.commit(0);
        assert!(prepare(&mut a,1,1032,32).unwrap());
        assert_eq!(a.source.cursor(a.flows[1].as_ref().unwrap().index).unwrap(),32);
        a.commit(1);a.remove(1);
        assert!(prepare(&mut a,0,1032,32).unwrap());a.commit(0);
        assert!(rx.try_recv().is_err());a.remove(0);assert!(rx.try_recv().is_ok());
    }
    #[test]
    fn clock_jump_rebuffers_without_terminating_capture() {
        let(p,mut a,tx,rx)=setup();prepare(&mut a,0,10,32).unwrap();
        p.publish(0,96,false,block(&tx,96)).unwrap();prepare(&mut a,0,10,32).unwrap();a.commit(0);
        assert!(prepare(&mut a,0,100,32).unwrap());a.commit(0);
        assert_eq!(p.metrics.fault.load(Ordering::Acquire),0);
        assert_eq!(p.metrics.clock_jumps.load(Ordering::Acquire),1);a.remove(0);assert!(rx.try_recv().is_ok());
    }
    #[test]
    fn no_subscriptions_and_shutdown_return_owned_blocks() {
        let(p,mut a,tx,rx)=setup();p.publish(0,480,false,block(&tx,480)).unwrap();a.poll();
        assert!(rx.try_recv().is_ok());
        p.publish(480,480,false,block(&tx,480)).unwrap();drop(a);
        assert!(rx.try_recv().is_ok());
        assert!(p.publish(960,480,false,block(&tx,480)).is_ok());assert!(rx.try_recv().is_ok());
        assert_eq!(p.metrics.fault.load(Ordering::Acquire),1);
    }
    #[test]
    fn detach_and_new_capture_preserve_continuous_network_time() {
        let(p,mut a,tx,rx)=setup();
        assert!(prepare(&mut a,0,0,32).unwrap());a.commit(0);
        p.publish(0,32,false,block(&tx,32)).unwrap();
        assert!(prepare(&mut a,0,32,32).unwrap());a.commit(0);assert!(rx.try_recv().is_ok());
        p.tx.send(Message::Finish).unwrap();a.poll();assert!(p.metrics.drained.load(Ordering::Acquire));
        assert!(prepare(&mut a,0,64,32).unwrap());a.commit(0);
        p.tx.send(Message::Reset).unwrap();a.poll();
        p.publish(0,32,false,block(&tx,32)).unwrap();
        assert!(prepare(&mut a,0,96,32).unwrap());a.commit(0);assert!(rx.try_recv().is_ok());
        assert_eq!(p.metrics.fault.load(Ordering::Acquire),0);
        assert_eq!(p.metrics.silence_packets.load(Ordering::Acquire),2);
    }
}
