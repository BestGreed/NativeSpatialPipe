//! Native DSP-output blocks -> packet payload, without an ABI lease or PCM ring.
//! One owner serializes topology, publication, packet preparation and commit.
//! PTP scheduling / resampling belong above this source-frame layer.
use super::Block;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
static NEXT_SOURCE: AtomicU64 = AtomicU64::new(1);

pub const MAX_FLOWS: usize = 32;
pub const MAX_CHANNELS_PER_FLOW: usize = 8;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    InvalidConfig, Closed, Discontinuity, QueueFull, InvalidBlock,
    InvalidFlow, TooManyFlows, SourceUnavailable, InvalidPacket, NonFinite,
    StalePacket,
}

/// Publication failure returns the exact block; the caller still owns it.
pub struct Rejected { pub error: Error, pub block: Box<Block> }
struct Stored { first: u64, end: u64, block: Box<Block> }
#[derive(Clone, Copy)]
struct Flow { cursor: u64, generation: u64 }

/// A prepared payload does not advance consumption. Commit only after a full
/// successful datagram send. Failed sends can retry while retaining the blocks.
#[derive(Debug, Clone, Copy)]
pub struct Prepared {
    source: u64,
    flow: usize, generation: u64, first: u64, end: u64,
    pub payload_bytes: usize,
}

pub struct Source {
    id: u64,
    channels: usize,
    capacity: usize,
    blocks: VecDeque<Stored>,
    flows: [Option<Flow>; MAX_FLOWS],
    next_frame: u64,
    generation: u64,
    tail: bool,
    finished: bool,
    closed: bool,
    accepted: u64,
    recycled: u64,
}

impl Source {
    pub fn new(channels: usize, capacity: usize) -> Result<Self, Error> {
        if !(1..=16).contains(&channels) || !(1..=256).contains(&capacity) {
            return Err(Error::InvalidConfig);
        }
        let id = NEXT_SOURCE.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| Error::InvalidConfig)?;
        Ok(Self { id, channels, capacity, blocks: VecDeque::with_capacity(capacity),
            flows: [None; MAX_FLOWS], next_frame: 0, generation: 0,
            tail: false, finished: false, closed: false, accepted: 0, recycled: 0 })
    }

    pub fn counts(&self) -> (u64, u64, usize) {
        (self.accepted, self.recycled, self.blocks.len())
    }

    pub fn next_frame(&self) -> u64 { self.next_frame }
    pub fn cursor(&self, index: usize) -> Result<u64, Error> {
        self.flows.get(index).copied().flatten().map(|f| f.cursor).ok_or(Error::InvalidFlow)
    }
    pub fn available(&self, index: usize) -> Result<u64, Error> {
        Ok(self.next_frame - self.cursor(index)?)
    }
    pub fn is_finished(&self) -> bool { self.finished }
    pub fn seek(&mut self, index: usize, first: u64) -> Result<(), Error> {
        let oldest = self.blocks.front().map_or(self.next_frame, |b| b.first);
        if first < oldest || first > self.next_frame { return Err(Error::SourceUnavailable); }
        self.flows.get_mut(index).and_then(Option::as_mut).ok_or(Error::InvalidFlow)?.cursor=first;
        self.reclaim();
        Ok(())
    }

    pub fn add_flow(&mut self, first: u64) -> Result<usize, Error> {
        if self.closed { return Err(Error::Closed); }
        let oldest = self.blocks.front().map_or(self.next_frame, |b| b.first);
        if first < oldest || first > self.next_frame { return Err(Error::SourceUnavailable); }
        let index = self.flows.iter().position(Option::is_none).ok_or(Error::TooManyFlows)?;
        self.generation = self.generation.checked_add(1).ok_or(Error::InvalidFlow)?;
        self.flows[index] = Some(Flow { cursor: first, generation: self.generation });
        Ok(index)
    }

    pub fn remove_flow(&mut self, index: usize) -> Result<(), Error> {
        if self.flows.get_mut(index).and_then(Option::take).is_none() {
            return Err(Error::InvalidFlow);
        }
        self.reclaim();
        Ok(())
    }

    /// Called with the existing block after exclusive in-place DSP finishes.
    /// No allocation, sample conversion or pointer/token ABI crossing occurs.
    pub fn push(&mut self, first: u64, frames: usize, tail: bool,
                block: Box<Block>) -> Result<(), Rejected> {
        let end = first.checked_add(frames as u64);
        let error = if self.closed || self.finished { Some(Error::Closed) }
        else if frames == 0 || block.channels != self.channels ||
            frames.checked_mul(self.channels).is_none_or(|n| n > block.data.len()) {
            Some(Error::InvalidBlock)
        } else if first != self.next_frame || end.is_none() || (self.tail && !tail) {
            Some(Error::Discontinuity)
        } else if self.blocks.len() == self.capacity { Some(Error::QueueFull) }
        else { None };
        if let Some(error) = error { return Err(Rejected { error, block }); }
        self.next_frame = end.unwrap();
        self.tail |= tail;
        self.accepted += 1;
        self.blocks.push_back(Stored { first, end: self.next_frame, block });
        // No subscriber: discard that time interval and return storage now.
        self.reclaim();
        Ok(())
    }

    /// Encodes audio samples only; the caller writes the protocol timestamp/header
    /// into its own preallocated datagram. None denotes an intentionally silent slot.
    pub fn prepare_s24(&self, index: usize, frames: usize, mapping: &[Option<usize>],
                       payload: &mut [u8]) -> Result<Prepared, Error> {
        self.prepare(index, frames, mapping, payload, false)
    }

    /// At EOF only, pad the final datagram with silence, consuming valid frames only.
    pub fn prepare_s24_tail(&self, index: usize, frames: usize, mapping: &[Option<usize>],
                            payload: &mut [u8]) -> Result<Prepared, Error> {
        self.prepare(index, frames, mapping, payload, true)
    }

    fn prepare(&self, index: usize, frames: usize, mapping: &[Option<usize>],
               payload: &mut [u8], pad_tail: bool) -> Result<Prepared, Error> {
        let flow = self.flows.get(index).copied().flatten().ok_or(Error::InvalidFlow)?;
        if self.closed { return Err(Error::Closed); }
        if !(2..=256).contains(&frames) || mapping.is_empty() ||
            mapping.len() > MAX_CHANNELS_PER_FLOW ||
            mapping.iter().flatten().any(|&c| c >= self.channels) {
            return Err(Error::InvalidPacket);
        }
        let bytes = frames * mapping.len() * 3;
        if payload.len() < bytes { return Err(Error::InvalidPacket); }
        let requested_end = flow.cursor.checked_add(frames as u64).ok_or(Error::InvalidPacket)?;
        let end = if pad_tail && self.finished { requested_end.min(self.next_frame) } else { requested_end };
        if end > self.next_frame || end == flow.cursor { return Err(Error::SourceUnavailable); }
        // Validate the complete source range before touching the destination.
        for frame in flow.cursor..end {
            let (b, offset) = self.find(frame)?;
            for &channel in mapping.iter().flatten() {
                if !b.data[offset * self.channels + channel].is_finite() {
                    return Err(Error::NonFinite);
                }
            }
        }
        let mut output = 0;
        for frame in flow.cursor..end {
            let (block, offset) = self.find(frame)?;
            for &channel in mapping {
                let sample = channel.map_or(0.0, |c| block.data[offset * self.channels + c]);
                let value = ((sample as f64).clamp(-1.0, 1.0) * 8_388_608.0)
                    .round().clamp(-8_388_608.0, 8_388_607.0) as i32;
                payload[output..output+3].copy_from_slice(&value.to_be_bytes()[1..]);
                output += 3;
            }
        }
        payload[output..bytes].fill(0);
        Ok(Prepared { source: self.id, flow: index, generation: flow.generation, first: flow.cursor,
            end, payload_bytes: bytes })
    }

    pub fn commit(&mut self, packet: Prepared) -> Result<(), Error> {
        if packet.source != self.id { return Err(Error::StalePacket); }
        let flow = self.flows.get_mut(packet.flow).and_then(Option::as_mut)
            .ok_or(Error::StalePacket)?;
        if flow.generation != packet.generation || flow.cursor != packet.first {
            return Err(Error::StalePacket);
        }
        flow.cursor = packet.end;
        self.reclaim();
        Ok(())
    }

    /// End of publication; queued audio remains readable until flows drain.
    pub fn finish(&mut self) { self.finished = true; }
    pub fn drained(&self) -> bool { self.finished && self.blocks.is_empty() }

    pub fn abort(&mut self) {
        self.closed = true;
        self.flows.fill(None);
        self.reclaim();
    }

    fn find(&self, frame: u64) -> Result<(&Block, usize), Error> {
        self.blocks.iter().find(|b| b.first <= frame && frame < b.end)
            .map(|b| (&*b.block, (frame - b.first) as usize)).ok_or(Error::SourceUnavailable)
    }

    fn reclaim(&mut self) {
        let until = self.flows.iter().flatten().map(|f| f.cursor).min()
            .unwrap_or(self.next_frame);
        while self.blocks.front().is_some_and(|b| b.end <= until) {
            let block = self.blocks.pop_front().unwrap().block;
            let recycle = block.recycle.clone();
            // A stopped pipeline has no free-pool receiver; dropping is correct.
            let _ = recycle.try_send(block);
            self.recycled += 1;
        }
    }
}

impl Drop for Source { fn drop(&mut self) { self.abort(); } }

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::{sync_channel, Receiver, SyncSender};
    fn block(channels: usize, data: Vec<f32>, tx: &SyncSender<Box<Block>>) -> Box<Block> {
        Box::new(Block { channels, data, recycle: tx.clone() })
    }
    fn pool() -> (SyncSender<Box<Block>>, Receiver<Box<Block>>) { sync_channel(16) }
    fn push(s: &mut Source, first: u64, frames: usize, b: Box<Block>) {
        assert!(s.push(first, frames, false, b).is_ok());
    }

    #[test]
    fn cross_block_quantization_and_success_only_recycle() {
        let (tx,rx)=pool(); let mut s=Source::new(2,4).unwrap();let f=s.add_flow(0).unwrap();
        push(&mut s,0,1,block(2,vec![-1.,0.5],&tx));
        push(&mut s,1,1,block(2,vec![1.,-0.5],&tx));
        let mut out=[0;18]; let p=s.prepare_s24(f,2,&[Some(1),None,Some(0)],&mut out).unwrap();
        assert_eq!(out,[0x40,0,0,0,0,0,0x80,0,0,0xc0,0,0,0,0,0,0x7f,0xff,0xff]);
        assert_eq!(p.payload_bytes,18); assert!(rx.try_recv().is_err());
        let mut retry=[0;18];s.prepare_s24(f,2,&[Some(1),None,Some(0)],&mut retry).unwrap();
        assert_eq!(retry,out);s.commit(p).unwrap();assert_eq!(s.counts(),(2,2,0));
        assert!(rx.try_recv().is_ok());assert!(rx.try_recv().is_ok());
        assert_eq!(s.commit(p),Err(Error::StalePacket));
    }
    #[test]
    fn slow_flow_retains_block_until_removed() {
        let (tx,rx)=pool();let mut s=Source::new(1,2).unwrap();
        let fast=s.add_flow(0).unwrap();let slow=s.add_flow(0).unwrap();
        push(&mut s,0,4,block(1,vec![0.;4],&tx));
        let p=s.prepare_s24(fast,4,&[Some(0)],&mut[0;12]).unwrap();s.commit(p).unwrap();
        assert!(rx.try_recv().is_err());s.remove_flow(slow).unwrap();
        assert!(rx.try_recv().is_ok());assert_eq!(s.counts(),(1,1,0));
    }
    #[test]
    fn rejected_publication_keeps_exact_block_and_cursor() {
        let (tx,_)=pool();let mut s=Source::new(1,1).unwrap();s.add_flow(0).unwrap();
        push(&mut s,0,2,block(1,vec![0.;2],&tx));
        let b=block(1,vec![1.;2],&tx);let ptr=b.data.as_ptr();
        let rejected=s.push(2,2,false,b).err().unwrap();
        assert_eq!(rejected.error,Error::QueueFull);assert_eq!(rejected.block.data.as_ptr(),ptr);
        assert_eq!(s.next_frame,2);assert_eq!(s.counts(),(1,0,1));
    }
    #[test]
    fn unsubscribed_blocks_recycle_without_audio_copy() {
        let (tx,rx)=pool();let mut s=Source::new(1,1).unwrap();
        let b=block(1,vec![0.;4],&tx);let ptr=b.data.as_ptr();push(&mut s,0,4,b);
        assert_eq!(rx.try_recv().unwrap().data.as_ptr(),ptr);
        assert_eq!(s.add_flow(0),Err(Error::SourceUnavailable));assert!(s.add_flow(4).is_ok());
    }
    #[test]
    fn invalid_samples_and_missing_frames_do_not_touch_payload() {
        let (tx,_)=pool();let mut s=Source::new(1,2).unwrap();let f=s.add_flow(0).unwrap();
        push(&mut s,0,2,block(1,vec![0.,f32::NAN],&tx));let mut out=[0xab;12];
        assert!(matches!(s.prepare_s24(f,2,&[Some(0)],&mut out),Err(Error::NonFinite)));
        assert!(matches!(s.prepare_s24(f,4,&[Some(0)],&mut out),Err(Error::SourceUnavailable)));
        assert_eq!(out,[0xab;12]);assert_eq!(s.flows[f].unwrap().cursor,0);
    }
    #[test]
    fn replacement_flow_rejects_old_packet_and_abort_recycles() {
        let (tx,rx)=pool();let mut s=Source::new(1,2).unwrap();let a=s.add_flow(0).unwrap();
        let b=s.add_flow(0).unwrap();push(&mut s,0,4,block(1,vec![0.;4],&tx));
        let p=s.prepare_s24(a,2,&[Some(0)],&mut[0;6]).unwrap();s.remove_flow(a).unwrap();
        assert_eq!(s.add_flow(0).unwrap(),a);assert_eq!(s.commit(p),Err(Error::StalePacket));
        s.abort();assert!(rx.try_recv().is_ok());assert_eq!(s.remove_flow(b),Err(Error::InvalidFlow));
        assert_eq!(s.counts(),(1,1,0));
    }
    #[test]
    fn short_tail_stays_ordered_and_is_returned_on_drop() {
        let (tx,rx)=pool();{
            let mut s=Source::new(1,3).unwrap();s.add_flow(0).unwrap();
            push(&mut s,0,2,block(1,vec![0.;2],&tx));
            assert!(s.push(2,1,true,block(1,vec![0.;4],&tx)).is_ok());
            assert_eq!(s.push(3,1,false,block(1,vec![0.;1],&tx)).err().unwrap().error,Error::Discontinuity);
        } assert!(rx.try_recv().is_ok());assert!(rx.try_recv().is_ok());assert!(rx.try_recv().is_err());
    }
    #[test]
    fn twelve_channels_two_flows_split_one_480_frame_pool_block() {
        let (tx,rx)=pool();let mut s=Source::new(12,2).unwrap();
        let a=s.add_flow(0).unwrap();let b=s.add_flow(0).unwrap();
        let data:Vec<f32>=(0..480).flat_map(|_| (0..12).map(|c|c as f32/16.)).collect();
        let original=data.as_ptr();push(&mut s,0,480,block(12,data,&tx));
        s.finish();assert!(!s.drained());
        let map_a:Vec<_>=(0..8).map(Some).collect();let map_b:Vec<_>=(8..12).map(Some).collect();
        for _ in 0..15 {
            let p=s.prepare_s24(a,32,&map_a,&mut[0;768]).unwrap();s.commit(p).unwrap();
        }
        assert!(rx.try_recv().is_err());
        for _ in 0..15 {
            let mut payload=[0;384];let p=s.prepare_s24(b,32,&map_b,&mut payload).unwrap();
            assert_eq!(&payload[..3],&[0x40,0,0]); // channel8 = 0.5
            s.commit(p).unwrap();
        }
        assert!(s.drained());assert_eq!(rx.try_recv().unwrap().data.as_ptr(),original);
        assert!(s.push(480,2,true,block(12,vec![0.;24],&tx)).is_err());
    }
    #[test]
    fn packet_cannot_be_committed_on_a_different_source() {
        let (tx,_)=pool();let mut a=Source::new(1,2).unwrap();let mut b=Source::new(1,2).unwrap();
        let fa=a.add_flow(0).unwrap();b.add_flow(0).unwrap();
        push(&mut a,0,2,block(1,vec![0.;2],&tx));push(&mut b,0,2,block(1,vec![0.;2],&tx));
        let p=a.prepare_s24(fa,2,&[Some(0)],&mut[0;6]).unwrap();
        assert_eq!(b.commit(p),Err(Error::StalePacket));a.commit(p).unwrap();
    }
}
