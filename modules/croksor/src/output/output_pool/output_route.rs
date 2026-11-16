//! Control-plane source selection. No PCM copies and no processing under lock.
//! One short critical section orders each block against a source-epoch change.
use super::{Block,aoip_source::{Message,Publisher,recycle}};
use std::sync::Mutex;
pub const SOURCES:usize=4;
struct State { selected:Option<usize>, epoch:u64, base:[Option<u64>;SOURCES], finished:u8, ended:bool }
pub struct Router { publisher:Publisher, state:Mutex<State> }
impl Router {
    pub fn new(publisher:Publisher)->Self {
        Self{publisher,state:Mutex::new(State{selected:None,epoch:0,base:[None;SOURCES],finished:0,ended:false})}
    }
    pub fn select(&self,source:Option<usize>)->Result<(),String>{
        if source.is_some_and(|i|i>=SOURCES){return Err("Invalid AoIP source".into());}
        let mut s=self.state.lock().map_err(|_|"Output router poisoned")?;
        if s.ended{return Err("Capture output already ended".into());}
        if s.selected==source{return Ok(());}
        let epoch=s.epoch.checked_add(1).ok_or("Source epoch exhausted")?;
        // Commit control state only when the ordered reset has been accepted.
        self.publisher.tx.try_send(Message::SelectEpoch{epoch}).map_err(|_|"Output route handoff busy")?;
        s.epoch=epoch;s.selected=source;s.base.fill(None);
        Ok(())
    }
    pub fn selected(&self)->Option<usize>{self.state.lock().ok().and_then(|s|s.selected)}
    pub fn publish(&self,source:usize,first:u64,frames:usize,tail:bool,block:Box<Block>)->Result<(),String>{
        let mut s=match self.state.lock(){Ok(s)=>s,Err(_)=>{recycle(block);return Err("Output router poisoned".into());}};
        if s.ended || s.selected!=Some(source){recycle(block);return Ok(());}
        let base=*s.base[source].get_or_insert(first);
        let Some(relative)=first.checked_sub(base) else {recycle(block);return Err("Output source frame regressed".into());};
        self.publisher.publish_routed(s.epoch,relative,frames,tail,block)
    }
    pub fn finish_source(&self,source:usize)->bool{
        let mut s=self.state.lock().unwrap();s.finished|=1<<source;
        if !s.ended && (s.selected==Some(source) || s.finished==15){s.ended=true;true}else{false}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::aoip_source::{Adapter,Metrics};
    use std::sync::{Arc,mpsc::sync_channel};
    #[test]
    fn hot_switch_rebases_pcm_while_network_clock_and_flow_stay_alive(){
        let(tx,rx)=sync_channel(16);let m=Arc::new(Metrics::default());
        let r=Router::new(Publisher{tx,metrics:m.clone()});
        let mut a=Adapter::new(2,8,32,rx,m.clone()).unwrap();
        let(free,returned)=sync_channel(8);
        let block=|v|Box::new(Block{channels:2,data:vec![v;64],recycle:free.clone()});
        let mut pcm=[0u8;192];a.prepare(0,0,32,&[Some(0),Some(1)],3,&mut pcm).unwrap();a.commit(0);
        r.select(Some(0)).unwrap();r.publish(0,48000,32,false,block(0.25)).unwrap();
        a.prepare(0,32,32,&[Some(0),Some(1)],3,&mut pcm).unwrap();a.commit(0);
        assert_eq!(&pcm[..3],&[0x20,0,0]);assert!(returned.try_recv().is_ok());
        r.select(Some(1)).unwrap();r.publish(0,48032,32,false,block(-1.)).unwrap();
        assert!(returned.try_recv().is_ok());
        r.publish(1,912000,32,false,block(0.5)).unwrap();
        a.prepare(0,64,32,&[Some(0),Some(1)],3,&mut pcm).unwrap();a.commit(0);
        assert_eq!(&pcm[..3],&[0x40,0,0]);assert!(returned.try_recv().is_ok());
        assert_eq!(m.fault.load(std::sync::atomic::Ordering::Acquire),0);
        assert_eq!(m.clock_jumps.load(std::sync::atomic::Ordering::Acquire),0);
        assert!(!r.finish_source(0));assert!(r.finish_source(1));
    }
    #[test]
    fn repeated_selection_keeps_pending_audio_and_invalid_source_has_no_effect(){
        let(tx,rx)=sync_channel(8);let m=Arc::new(Metrics::default());
        let r=Router::new(Publisher{tx,metrics:m});
        r.select(Some(2)).unwrap();assert!(matches!(rx.try_recv().unwrap(),Message::SelectEpoch{epoch:1}));
        r.select(Some(2)).unwrap();assert!(rx.try_recv().is_err());
        assert!(r.select(Some(4)).is_err());assert_eq!(r.selected(),Some(2));
        r.select(None).unwrap();assert!(matches!(rx.try_recv().unwrap(),Message::SelectEpoch{epoch:2}));
    }
    #[test]
    fn a_late_old_epoch_block_cannot_enter_the_new_source(){
        let(tx,rx)=sync_channel(16);let m=Arc::new(Metrics::default());
        let r=Router::new(Publisher{tx:tx.clone(),metrics:m.clone()});
        let mut a=Adapter::new(2,8,32,rx,m.clone()).unwrap();let(free,returned)=sync_channel(8);
        let block=|v|Box::new(Block{channels:2,data:vec![v;64],recycle:free.clone()});
        let mut pcm=[0u8;192];a.prepare(0,0,32,&[Some(0),Some(1)],3,&mut pcm).unwrap();a.commit(0);
        r.select(Some(0)).unwrap();r.select(Some(1)).unwrap();
        tx.send(Message::RoutedBlock{epoch:1,first:0,frames:32,tail:false,block:block(-1.)}).ok().unwrap();
        r.publish(1,123456,32,false,block(0.5)).unwrap();
        a.prepare(0,32,32,&[Some(0),Some(1)],3,&mut pcm).unwrap();a.commit(0);
        assert_eq!(&pcm[..3],&[0x40,0,0]);assert!(returned.try_recv().is_ok());assert!(returned.try_recv().is_ok());
        assert_eq!(m.fault.load(std::sync::atomic::Ordering::Acquire),0);
    }
}
