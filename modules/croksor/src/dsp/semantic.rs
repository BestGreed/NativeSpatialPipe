//! Temporary single-PCM semantic audition; inference and SRC live off-thread.
use std::{ffi::{c_void,c_char,CStr},sync::{Arc,Mutex,OnceLock,Weak}};
#[link(name="kernel32")]
unsafe extern "system"{fn LoadLibraryExW(path:*const u16,file:*mut c_void,flags:u32)->*mut c_void;fn GetProcAddress(module:*mut c_void,name:*const c_char)->*mut c_void;fn FreeLibrary(module:*mut c_void)->i32;}
type Process=unsafe extern "C" fn(*mut c_void,*const f32,*mut f32,u32,u32)->i32;
#[derive(Default)]struct Libraries{handles:Vec<*mut c_void>}
impl Drop for Libraries{fn drop(&mut self){while let Some(p)=self.handles.pop(){unsafe{FreeLibrary(p);}}}}
struct Session{_libraries:Libraries,handle:*mut c_void,process:Process,stats:unsafe extern "C" fn(*mut c_void,*mut c_char,u32),close:unsafe extern "C" fn(*mut c_void)}
// Single producer on the renderer; the native stats entry reads atomics only.
unsafe impl Send for Session{} unsafe impl Sync for Session{}
impl Drop for Session{fn drop(&mut self){unsafe{(self.close)(self.handle);}}}
static LIVE:OnceLock<Mutex<Weak<Session>>>=OnceLock::new();
fn wide(text:&str)->Vec<u16>{text.encode_utf16().chain(Some(0)).collect()}
impl Session{
 fn open(dll:&str,model:&str,latency:u32)->Result<Arc<Self>,String>{unsafe{
  let mut libraries=Libraries::default();
  // OpenVINO's C shim delay-loads the core. Preload its colocated dependencies
  // by absolute path, without changing the host process DLL search policy.
  if let Some(directory)=std::path::Path::new(dll).parent(){if directory.join("openvino_c.dll").is_file(){for name in ["tbb12.dll","openvino.dll","openvino_c.dll"]{let path=directory.join(name);let loaded=LoadLibraryExW(wide(&path.to_string_lossy()).as_ptr(),std::ptr::null_mut(),0x1100);if loaded.is_null(){return Err(format!("Semantic dependency {name}: {}",std::io::Error::last_os_error()));}libraries.handles.push(loaded);}}}
  let module=LoadLibraryExW(wide(dll).as_ptr(),std::ptr::null_mut(),0x1100);if module.is_null(){return Err(format!("Semantic DLL load failed: {}",std::io::Error::last_os_error()));}libraries.handles.push(module);
  macro_rules! symbol{($name:literal,$typ:ty)=>{{let p=GetProcAddress(module,concat!($name,"\0").as_ptr().cast());if p.is_null(){return Err(concat!("Semantic missing ",$name).into());}std::mem::transmute::<*mut c_void,$typ>(p)}};}
  let open=symbol!("nsp_vocal_open",unsafe extern "C" fn(*const u16,u32)->*mut c_void);
  let process=symbol!("nsp_vocal_process",Process);let stats=symbol!("nsp_vocal_stats",unsafe extern "C" fn(*mut c_void,*mut c_char,u32));let close=symbol!("nsp_vocal_close",unsafe extern "C" fn(*mut c_void));let error=symbol!("nsp_vocal_error",unsafe extern "C" fn()->*const c_char);
  let handle=open(wide(model).as_ptr(),latency);if handle.is_null(){let message=CStr::from_ptr(error()).to_string_lossy().into_owned();return Err(message);}
  let session=Arc::new(Self{_libraries:libraries,handle,process,stats,close});*LIVE.get_or_init(||Mutex::new(Weak::new())).lock().unwrap()=Arc::downgrade(&session);Ok(session)
 }}
}
pub fn status()->String{let session=LIVE.get().and_then(|m|m.lock().ok()?.upgrade());if let Some(session)=session{let mut text=[0u8;2048];unsafe{(session.stats)(session.handle,text.as_mut_ptr().cast(),text.len() as u32);}let len=text.iter().position(|x|*x==0).unwrap_or(0);String::from_utf8_lossy(&text[..len]).into_owned()}else{"null".into()}}
pub struct Semantic{session:Arc<Session>,delay:Vec<f32>,at:usize,block:usize,epoch:u32,last:Option<(u64,u32)>,input:Vec<f32>,pub vocal:Vec<f32>,pub available:bool}
impl Semantic{
 pub fn reset(&mut self){self.epoch=self.epoch%0xfffffd+1;self.delay.fill(0.);self.vocal.fill(0.);self.at=0;self.available=false;}
 pub fn delay_frames(&self)->usize{self.delay.len()/12}
 pub fn configured(source_path:&str,n:usize)->Result<Option<Self>,String>{
  // Trial scope only. Production will pass an explicit per-source processor.
  if !source_path.replace('\\',"/").ends_with("/pcm-stream/renderer.f32"){return Ok(None);}
  let Some(local)=std::env::var_os("LOCALAPPDATA") else{return Ok(None)};let folder=std::path::PathBuf::from(local).join("NativeSpatialPipe/dsp-ab");
  if !folder.join("enabled").is_file(){return Ok(None);}let file=folder.join("semantic-config.txt");if !file.is_file(){return Ok(None);}
  let config=std::fs::read_to_string(file).map_err(|e|e.to_string())?;let lines:Vec<_>=config.lines().collect();if !(3..=4).contains(&lines.len())||n!=480{return Err("Semantic audition configuration".into());}
  let latency=lines[2].parse::<usize>().map_err(|e|e.to_string())?;if latency<960||latency>9600||latency%n!=0||!std::path::Path::new(lines[0]).is_absolute()||!std::path::Path::new(lines[1]).is_absolute(){return Err("Semantic delay/paths".into());}
  if lines.len()==4&&!std::path::Path::new(lines[3]).is_absolute(){return Err("Semantic fallback path".into());}
  let session=Session::open(lines[0],lines[1],latency as u32).or_else(|error|{if lines.len()==4&&lines[3]!=lines[0]{eprintln!("Semantic primary initialization failed: {error}; loading configured CPU fallback");Session::open(lines[3],lines[1],latency as u32)}else{Err(error)}})?;Ok(Some(Self{session,delay:vec![0.;latency*12],at:0,block:n*12,epoch:1,last:None,input:vec![0.;n*2],vocal:vec![0.;n*2],available:false}))
 }
 pub fn observe(&mut self,sequence:u64,identity:u32)->bool{let changed=self.last.is_some_and(|(s,i)|sequence!=s+1||identity!=i);if changed{self.epoch=self.epoch%0xfffffd+1;self.delay.fill(0.);self.vocal.fill(0.);self.at=0;self.available=false;}self.last=Some((sequence,identity));changed}
 pub fn process(&mut self,bus:&mut[f32],n:usize){
  for i in 0..n{self.input[i]=bus[i*12];self.input[n+i]=bus[i*12+1];}
  self.available=unsafe{(self.session.process)(self.session.handle,self.input.as_ptr(),self.vocal.as_mut_ptr(),n as u32,self.epoch)==1};
  for (i,value) in bus.iter_mut().enumerate(){std::mem::swap(value,&mut self.delay[self.at+i]);}self.at=(self.at+self.block)%self.delay.len();
 }
}
