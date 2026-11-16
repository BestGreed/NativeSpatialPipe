//! Optional local residual audition. All allocation/loading occurs at source creation.
use std::{ffi::{c_void,c_char},sync::atomic::{AtomicBool,Ordering::Relaxed}};
#[link(name="kernel32")]
unsafe extern "system"{fn LoadLibraryExW(path:*const u16,file:*mut c_void,flags:u32)->*mut c_void;fn GetProcAddress(module:*mut c_void,name:*const c_char)->*mut c_void;fn FreeLibrary(module:*mut c_void)->i32;}
static ENABLED:AtomicBool=AtomicBool::new(false);
pub fn enabled()->bool{ENABLED.load(Relaxed)}
type Process=unsafe extern "C" fn(*mut c_void,*const f32,*mut f32,u32)->i32;
pub struct FreeSurround{module:*mut c_void,handle:*mut c_void,process:Process,reset:unsafe extern "C" fn(*mut c_void),close:unsafe extern "C" fn(*mut c_void),delay:Vec<f32>,at:usize,input:Vec<f32>,pub output:Vec<f32>,pub vocal:Vec<f32>}
unsafe impl Send for FreeSurround{}
impl Drop for FreeSurround{fn drop(&mut self){unsafe{(self.close)(self.handle);FreeLibrary(self.module);}ENABLED.store(false,Relaxed);}}
impl FreeSurround{
 pub fn configured(active:bool,n:usize)->Result<Option<Self>,String>{
  if !active{return Ok(None)}let Some(local)=std::env::var_os("LOCALAPPDATA")else{return Ok(None)};
  let file=std::path::PathBuf::from(local).join("NativeSpatialPipe/dsp-ab/freesurround-config.txt");if !file.is_file(){return Ok(None)}
  let path=std::fs::read_to_string(file).map_err(|e|e.to_string())?;let path=path.trim();if n!=480||!std::path::Path::new(path).is_absolute(){return Err("FreeSurround configuration".into())}
  unsafe{
   let wide:Vec<u16>=path.encode_utf16().chain(Some(0)).collect();let module=LoadLibraryExW(wide.as_ptr(),std::ptr::null_mut(),0x1100);if module.is_null(){return Err("FreeSurround DLL load failed".into())}
   macro_rules! symbol{($name:literal,$typ:ty)=>{{let p=GetProcAddress(module,concat!($name,"\0").as_ptr().cast());if p.is_null(){FreeLibrary(module);return Err(concat!("Missing ",$name).into())}std::mem::transmute::<*mut c_void,$typ>(p)}};}
   let open=symbol!("nsp_fs_open",unsafe extern "C" fn()->*mut c_void);let close=symbol!("nsp_fs_close",unsafe extern "C" fn(*mut c_void));let reset=symbol!("nsp_fs_reset",unsafe extern "C" fn(*mut c_void));let process=symbol!("nsp_fs_process",Process);let delay=symbol!("nsp_fs_delay",unsafe extern "C" fn()->u32)() as usize;
   if delay!=1536{FreeLibrary(module);return Err("FreeSurround unexpected delay".into())}let handle=open();if handle.is_null(){FreeLibrary(module);return Err("FreeSurround initialization failed".into())}
   ENABLED.store(true,Relaxed);Ok(Some(Self{module,handle,process,reset,close,delay:vec![0.;delay*14],at:0,input:vec![0.;2*n],output:vec![0.;12*n],vocal:vec![0.;2*n]}))
  }
 }
 pub fn delay_frames(&self)->usize{self.delay.len()/14}
 pub fn reset(&mut self){unsafe{(self.reset)(self.handle);}self.delay.fill(0.);self.output.fill(0.);self.vocal.fill(0.);self.at=0;}
 pub fn process(&mut self,bus:&mut[f32],vocal:&[f32],n:usize){
  for i in 0..n{self.input[i]=bus[12*i]-vocal[i];self.input[n+i]=bus[12*i+1]-vocal[n+i];}
  let ok=unsafe{(self.process)(self.handle,self.input.as_ptr(),self.output.as_mut_ptr(),n as u32)};debug_assert_eq!(ok,1);
  for i in 0..n{for c in 0..12{std::mem::swap(&mut bus[12*i+c],&mut self.delay[self.at+c]);}for c in 0..2{self.vocal[c*n+i]=self.delay[self.at+12+c];self.delay[self.at+12+c]=vocal[c*n+i];}self.at=(self.at+14)%self.delay.len();}
 }
}
