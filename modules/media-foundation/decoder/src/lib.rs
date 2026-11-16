//! Source-built Dolby decoder. PCM is borrowed until pop/reset/destroy.
use abi_stable::std_types::RSlice;
use bridge_api::{FormatBridge,RChannelLabel,RDecodedFrame,RInputTransport};
use bridge_common::family::PluginBridge;
use bridge_family_dolby::DolbyPipeline;
use std::{collections::{BTreeMap,VecDeque},panic::{catch_unwind,AssertUnwindSafe},ptr};

#[repr(C)] #[derive(Clone,Copy)]
pub struct Object {pub id:u32,pub channel:u32}
#[repr(C)] #[derive(Clone,Copy)]
pub struct Event {pub id:u32,pub flags:u32,pub sample_pos:u64,pub pos:[f64;3],pub size:[f64;3],pub gain_db:f32,pub ramp:u32}
#[repr(C)]
pub struct View {pub pcm:*const i32,pub labels:*const u8,pub objects:*const Object,pub events:*const Event,pub pts:i64,pub rate:u32,pub samples:u32,pub channels:u32,pub object_count:u32,pub event_count:u32,pub reserved:u32}
pub struct Frame {data:RDecodedFrame,objects:Vec<Object>,events:Vec<Event>,pts:i64,start:u64}
pub struct Decoder {pipe:PluginBridge<DolbyPipeline>,codec:u32,queue:VecDeque<Frame>,mapping:BTreeMap<u32,u32>,anchor:Option<i64>,cursor:u64,rate:u32,error:String}
fn pipeline(codec:u32)->PluginBridge<DolbyPipeline>{let mut p=PluginBridge::new(true);assert!(p.configure("input_codec".into(),match codec{1=>"eac3",2=>"truehd",3=>"ac3",_=>unreachable!()}.into()));assert!(p.configure("presentation".into(),"best".into()));p}
#[unsafe(no_mangle)]
pub extern "C" fn nsp_decoder_create(codec:u32)->*mut Decoder {catch_unwind(||{if !(1..=3).contains(&codec){return ptr::null_mut();}Box::into_raw(Box::new(Decoder{pipe:pipeline(codec),codec,queue:VecDeque::new(),mapping:BTreeMap::new(),anchor:None,cursor:0,rate:0,error:String::new()}))}).unwrap_or(ptr::null_mut())}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_destroy(p:*mut Decoder){if !p.is_null(){let _=catch_unwind(AssertUnwindSafe(||unsafe{drop(Box::from_raw(p))}));}}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_reset(p:*mut Decoder)->i32 {if p.is_null(){return -1;}catch_unwind(AssertUnwindSafe(||{let d=unsafe{&mut*p};d.pipe=pipeline(d.codec);d.queue.clear();d.mapping.clear();d.anchor=None;d.cursor=0;d.rate=0;d.error.clear();0})).unwrap_or(-1)}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_push(p:*mut Decoder,data:*const u8,len:usize,pts:i64)->i32 {
 if p.is_null()||data.is_null()||len==0||len>65536{return -1;}
 catch_unwind(AssertUnwindSafe(||{
  let d=unsafe{&mut*p};if !d.queue.is_empty(){return -2;}d.error.clear();
  if d.anchor.is_none(){d.anchor=Some(pts);}
  let result=d.pipe.push_packet(RSlice::from_slice(unsafe{std::slice::from_raw_parts(data,len)}),RInputTransport::Raw,0);
  if !result.error_message.is_empty()||result.did_reset{d.error=result.error_message.as_str().to_owned();if d.error.is_empty(){d.error="decoder reset".into();}return -3;}
  let mut bytes=0usize;
  for mut f in result.frames {let channels=f.channel_count as usize;let n=f.sample_count as usize;
   if channels==0||channels>160||n>65536||f.sampling_frequency==0||f.pcm.len()!=channels*n||f.channel_labels.len()>channels{d.error="invalid frame dimensions".into();d.queue.clear();return -3;}
   // Early TrueHD frames may precede the first channel declaration.
   while f.channel_labels.len()<channels{f.channel_labels.push(RChannelLabel::Unknown);}
   bytes+=f.pcm.len()*4;if bytes>16*1024*1024{d.error="decoded input exceeds 16 MiB".into();d.queue.clear();return -3;}
   if d.rate!=0&&d.rate!=f.sampling_frequency{d.error="mid-segment rate change".into();d.queue.clear();return -3;}d.rate=f.sampling_frequency;
   let mut events=Vec::new();for m in f.metadata.iter(){if !m.object_channels.is_empty(){d.mapping.clear();for o in m.object_channels.iter(){if o.channel>=f.channel_count{d.error="object channel outside PCM".into();return -3;}d.mapping.insert(o.id,o.channel);}}for e in m.events.iter(){if !e.pos.iter().chain(e.size.iter()).all(|x|x.is_finite()){d.error="nonfinite metadata".into();return -3;}events.push(Event{id:e.id,flags:u32::from(e.has_pos),sample_pos:e.sample_pos,pos:e.pos,size:e.size,gain_db:e.gain_db as f32,ramp:e.ramp_duration});}}
   let objects=d.mapping.iter().map(|(&id,&channel)|Object{id,channel}).collect();let start=d.cursor;let frame_pts=d.anchor.unwrap()+((d.cursor as u128*10_000_000)/d.rate as u128)as i64;d.cursor+=f.sample_count as u64;
   d.queue.push_back(Frame{data:f,objects,events,pts:frame_pts,start});
  }0
 })).unwrap_or(-4)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_front(p:*mut Decoder,v:*mut View)->i32 {if p.is_null()||v.is_null(){return -1;}catch_unwind(AssertUnwindSafe(||{let d=unsafe{&mut*p};let Some(f)=d.queue.front()else{return 0;};unsafe{v.write(View{pcm:f.data.pcm.as_ptr(),labels:f.data.channel_labels.as_ptr().cast(),objects:f.objects.as_ptr(),events:f.events.as_ptr(),pts:f.pts,rate:f.data.sampling_frequency,samples:f.data.sample_count,channels:f.data.channel_count,object_count:f.objects.len()as u32,event_count:f.events.len()as u32,reserved:0});}1})).unwrap_or(-1)}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_pop(p:*mut Decoder)->i32 {if p.is_null(){return -1;}catch_unwind(AssertUnwindSafe(||{i32::from(unsafe{&mut*p}.queue.pop_front().is_some())})).unwrap_or(-1)}
// Transfer only the frame container. The PCM allocation stays in place.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_take(p:*mut Decoder)->*mut Frame {if p.is_null(){return ptr::null_mut();}catch_unwind(AssertUnwindSafe(||{unsafe{&mut*p}.queue.pop_front().map(|f|Box::into_raw(Box::new(f))).unwrap_or(ptr::null_mut())})).unwrap_or(ptr::null_mut())}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_frame_release(p:*mut Frame){if !p.is_null(){let _=catch_unwind(AssertUnwindSafe(||unsafe{drop(Box::from_raw(p))}));}}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_frame_view(p:*const Frame,v:*mut View)->i32 {if p.is_null()||v.is_null(){return -1;}let f=unsafe{&*p};unsafe{v.write(View{pcm:f.data.pcm.as_ptr(),labels:f.data.channel_labels.as_ptr().cast(),objects:f.objects.as_ptr(),events:f.events.as_ptr(),pts:f.pts,rate:f.data.sampling_frequency,samples:f.data.sample_count,channels:f.data.channel_count,object_count:f.objects.len()as u32,event_count:f.events.len()as u32,reserved:0});}1}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_frame_start(p:*const Frame)->u64 {if p.is_null(){0}else{unsafe{&*p}.start}}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_decoder_error(p:*const Decoder,out:*mut u8,capacity:usize)->usize {if p.is_null(){return 0;}let text=unsafe{&*p}.error.as_bytes();if !out.is_null()&&capacity>0{let n=text.len().min(capacity-1);unsafe{ptr::copy_nonoverlapping(text.as_ptr(),out,n);out.add(n).write(0);}}text.len()}

#[cfg(test)] mod tests {use super::*;
 #[test]fn rejects_bad_codec_and_bounds(){unsafe{assert!(nsp_decoder_create(0).is_null());let p=nsp_decoder_create(1);assert!(!p.is_null());assert_eq!(nsp_decoder_push(p,ptr::null(),0,0),-1);let b=0u8;assert_eq!(nsp_decoder_push(p,&b,65537,0),-1);assert_eq!(nsp_decoder_reset(p),0);let mut v=std::mem::zeroed();assert_eq!(nsp_decoder_front(p,&mut v),0);nsp_decoder_destroy(p);}}
}
