//! Opt-in live adapter for the combined480/240 candidate. Allocation at source creation.
use std::{ffi::{c_void,c_char,CStr},sync::{Arc,Mutex,OnceLock,Weak,atomic::{AtomicBool,Ordering::Relaxed}}};
#[link(name="kernel32")]
unsafe extern "system"{fn LoadLibraryExW(path:*const u16,file:*mut c_void,flags:u32)->*mut c_void;fn GetProcAddress(module:*mut c_void,name:*const c_char)->*mut c_void;fn FreeLibrary(module:*mut c_void)->i32;}
type Process=unsafe extern "C" fn(*mut c_void,*const f32,*mut f32,u32,u32)->i32;
type Submit = unsafe extern "C" fn(*mut c_void, *const f32, u32, u32) -> u64;
type Faulted = unsafe extern "C" fn(*mut c_void) -> i32;
type Read = unsafe extern "C" fn(*mut c_void, u64, u32, *mut f32, u32) -> i32;
type Ready = unsafe extern "C" fn(*mut c_void, u64, u32) -> i32;
type SetReadyEvent = unsafe extern "C" fn(*mut c_void, *mut c_void) -> i32;
struct Session{backend:&'static str,libraries:Vec<*mut c_void>,handle:*mut c_void,process:Process,split:Option<(Submit, Read)>,readiness:Option<(Ready, SetReadyEvent)>,faulted:Option<Faulted>,stats:unsafe extern "C" fn(*mut c_void,*mut c_char,u32),close:unsafe extern "C" fn(*mut c_void)}
unsafe impl Send for Session{} unsafe impl Sync for Session{}
impl Drop for Session{fn drop(&mut self){unsafe{if !self.handle.is_null(){(self.close)(self.handle);}while let Some(h)=self.libraries.pop(){FreeLibrary(h);}}}}
struct Live {
 primary: Weak<Session>,
 fallback: Option<Weak<Session>>,
 using_fallback: Arc<AtomicBool>,
}
static LIVE: OnceLock<Mutex<Option<Live>>> = OnceLock::new();
static ACTIVE: AtomicBool = AtomicBool::new(false);
pub fn active() -> bool { ACTIVE.load(Relaxed) }
pub fn set_active(value: bool) { ACTIVE.store(value, Relaxed); }
fn wide(s: &str) -> Vec<u16> { s.encode_utf16().chain(Some(0)).collect() }
pub fn status() -> String {
 let Some((session, standby, switched)) = LIVE.get().and_then(|m| {
  let live = m.lock().ok()?;
  let live = live.as_ref()?;
  let standby = live.fallback.as_ref().and_then(Weak::upgrade);
  let switched = live.using_fallback.load(Relaxed);
  let session = if switched { standby.clone()? } else { live.primary.upgrade()? };
  Some((session, standby.is_some(), switched))
 }) else { return "null".into(); };
 let mut out = [0u8; 2048];
 unsafe { (session.stats)(session.handle, out.as_mut_ptr().cast(), out.len() as u32); }
 let n = out.iter().position(|&v| v == 0).unwrap_or(0);
 let report = String::from_utf8_lossy(&out[..n]);
 match report.strip_prefix('{') {
  Some(body) => format!("{{\"configuredBackend\":\"{}\",\"cpuStandbyReady\":{standby},\"cpuFailover\":{switched},{body}", session.backend),
  None => "null".into(),
 }
}
pub struct Joint{
 session:Arc<Session>, fallback:Option<Arc<Session>>, using_fallback:Arc<AtomicBool>,
 delay:Vec<f32>,at:usize,epoch:u32,last:Option<(u64,u32)>,input:Vec<f32>,pub output:Vec<f32>,pub available:bool
}
impl Drop for Joint{fn drop(&mut self){set_active(false);}}
impl Joint{
 pub fn configured(source:&str,n:usize)->Result<Option<Self>,String>{
  if n!=480||!source.replace('\\',"/").ends_with("/pcm-stream/renderer.f32"){return Ok(None);}
  Self::load("joint-config.txt", n, false)
 }
 pub fn configured_unified() -> Result<Option<Self>, String> {
  Self::load("joint-unified-config.txt", 480, true)
 }
 fn load(name: &str, n: usize, split: bool) -> Result<Option<Self>, String> {
  let Some(local)=std::env::var_os("LOCALAPPDATA")else{return Ok(None)};let folder=std::path::PathBuf::from(local).join("NativeSpatialPipe/dsp-ab");let config=folder.join(name);
  if !folder.join("enabled").is_file()||!config.is_file(){return Ok(None)}
  let text=std::fs::read_to_string(config).map_err(|e|e.to_string())?;let lines:Vec<_>=text.lines().collect();
  if lines.len()!=4||lines[3]!="960"||lines[..3].iter().any(|s|!std::path::Path::new(s).is_absolute()){return Err("Joint candidate configuration".into());}
  Self::open(lines[0], lines[1], lines[2], n, split, "npu").map(|joint| { joint.register(); Some(joint) })
 }
 pub fn open_unified(primary: &str, fallback: Option<&str>, model: &str, fs: &str) -> Result<Self, String> {
  let mut joint = match Self::open(primary, model, fs, 480, true, "npu") {
   Ok(joint) => joint,
   Err(error) => {
    let Some(cpu) = fallback else { return Err(error); };
    eprintln!("Joint NPU unavailable, selecting CPU: {error}");
    let joint = Self::open(cpu, model, fs, 480, true, "cpu")?;
    joint.register();
    return Ok(joint);
   }
  };
  if let Some(cpu) = fallback {
   match Self::open(cpu, model, fs, 480, true, "cpu") {
    Ok(cpu) => joint.fallback = Some(cpu.session.clone()),
    Err(error) => eprintln!("Joint CPU standby unavailable: {error}"),
   }
  }
  joint.register();
  Ok(joint)
 }
 fn register(&self) {
  *LIVE.get_or_init(|| Mutex::new(None)).lock().unwrap() = Some(Live {
   primary: Arc::downgrade(&self.session),
   fallback: self.fallback.as_ref().map(Arc::downgrade),
   using_fallback: self.using_fallback.clone(),
  });
 }
 fn open(dll: &str, model: &str, fs: &str, n: usize, split: bool, backend: &'static str) -> Result<Self, String> {
  if [dll, model, fs].iter().any(|p| !std::path::Path::new(p).is_absolute()) {
   return Err("Joint component paths must be absolute".into());
  }
  unsafe{
   // A temporary guard owns every loaded module until a session is constructed.
   struct Modules(Vec<*mut c_void>);impl Drop for Modules{fn drop(&mut self){while let Some(p)=self.0.pop(){unsafe{FreeLibrary(p);}}}}
   let mut libraries=Modules(Vec::new());let directory=std::path::Path::new(dll).parent().unwrap();
   // The DirectML build loads its private ORT runtime itself.
   if !directory.join("onnxruntime.dll").is_file() {
    for name in ["tbb12.dll","openvino.dll","openvino_c.dll"]{let path=directory.join(name);let h=LoadLibraryExW(wide(&path.to_string_lossy()).as_ptr(),std::ptr::null_mut(),0x1100);if h.is_null(){return Err(format!("Joint dependency {name}: {}",std::io::Error::last_os_error()));}libraries.0.push(h);}
   }
   let module=LoadLibraryExW(wide(dll).as_ptr(),std::ptr::null_mut(),0x1100);if module.is_null(){return Err(format!("Joint DLL: {}",std::io::Error::last_os_error()));}libraries.0.push(module);
   let uses_cpu = GetProcAddress(module, c"nsp_joint_uses_cpu".as_ptr());
   let backend = if !GetProcAddress(module, c"nsp_joint_uses_directml".as_ptr()).is_null() {
    "directml"
   } else if uses_cpu.is_null() {
    backend
   } else if std::mem::transmute::<*mut c_void, unsafe extern "C" fn() -> i32>(uses_cpu)() == 1 {
    "cpu"
   } else {
    "npu"
   };
   macro_rules! symbol{($name:literal,$ty:ty)=>{{let p=GetProcAddress(module,concat!($name,"\0").as_ptr().cast());if p.is_null(){return Err(concat!("Missing ",$name).into());}std::mem::transmute::<*mut c_void,$ty>(p)}};}
   let open=symbol!("nsp_joint_open",unsafe extern "C" fn(*const u16,*const u16,u32)->*mut c_void);let close=symbol!("nsp_joint_close",unsafe extern "C" fn(*mut c_void));let process=symbol!("nsp_joint_process",Process);let stats=symbol!("nsp_joint_stats",unsafe extern "C" fn(*mut c_void,*mut c_char,u32));let error=symbol!("nsp_joint_error",unsafe extern "C" fn()->*const c_char);
   let split = if split {
    Some((symbol!("nsp_joint_submit", Submit), symbol!("nsp_joint_read", Read)))
   } else {
    None
   };
   let handle=open(wide(model).as_ptr(),wide(fs).as_ptr(),960);if handle.is_null(){return Err(CStr::from_ptr(error()).to_string_lossy().into_owned());}
   let faulted = GetProcAddress(module, c"nsp_joint_faulted".as_ptr());
   let faulted = (!faulted.is_null()).then(|| std::mem::transmute::<*mut c_void, Faulted>(faulted));
   let ready = GetProcAddress(module, c"nsp_joint_ready".as_ptr());
   let set_event = GetProcAddress(module, c"nsp_joint_set_ready_event".as_ptr());
   let readiness = (!ready.is_null() && !set_event.is_null()).then(|| (
       std::mem::transmute::<*mut c_void, Ready>(ready),
       std::mem::transmute::<*mut c_void, SetReadyEvent>(set_event),
   ));
   let session=Arc::new(Session{backend,libraries:std::mem::take(&mut libraries.0),handle,process,split,readiness,faulted,stats,close});
   Ok(Self{session,fallback:None,using_fallback:Arc::new(AtomicBool::new(false)),delay:if split.is_some(){Vec::new()}else{vec![0.;960*12]},at:0,epoch:1,last:None,input:vec![0.;n*2],output:vec![0.;n*12],available:false})
  }
 }
 pub fn reset(&mut self){self.epoch=self.epoch%0xfffffd+1;self.delay.fill(0.);self.output.fill(0.);self.at=0;self.available=false;}
 pub fn observe(&mut self,sequence:u64,identity:u32)->bool{let changed=self.last.is_some_and(|(s,i)|sequence!=s+1||identity!=i);if changed{self.reset();}self.last=Some((sequence,identity));changed}
 pub fn process(&mut self,bus:&mut[f32],n:usize){for i in 0..n{self.input[i]=bus[12*i];self.input[n+i]=bus[12*i+1];}self.available=unsafe{(self.session.process)(self.session.handle,self.input.as_ptr(),self.output.as_mut_ptr(),n as u32,self.epoch)==1};for(i,v)in bus.iter_mut().enumerate(){std::mem::swap(v,&mut self.delay[self.at+i]);}self.at=(self.at+bus.len())%self.delay.len();}

 pub(crate) fn set_ready_event(&self, wake: &crate::render_wake::RenderWake) {
  for session in std::iter::once(&self.session).chain(self.fallback.iter()) {
   if let Some((_, set)) = session.readiness {
    unsafe { set(session.handle, wake.event()) };
   }
  }
 }

 pub(crate) fn ready(&self, (ticket, epoch): (u64, u32)) -> bool {
  let session = if epoch >> 31 == 1 { self.fallback.as_ref() } else { Some(&self.session) };
  session.is_some_and(|s| s.readiness.is_some_and(|(ready, _)| unsafe {
   ready(s.handle, ticket, epoch & 0x00ff_ffff) == 1
  }))
 }

 pub fn submit_planar(&mut self, left: &[u8], right: &[u8]) -> Option<(u64, u32)> {
  let switch = !self.using_fallback.load(Relaxed) && self.fallback.is_some()
   && self.session.faulted.is_some_and(|faulted| unsafe { faulted(self.session.handle) != 0 });
  if switch {
   self.reset();
   self.using_fallback.store(true, Relaxed);
  }
  let using_fallback = self.using_fallback.load(Relaxed);
  let session = if using_fallback { self.fallback.as_ref()? } else { &self.session };
  let (submit, _) = session.split?;
  for (sample, bytes) in self.input.iter_mut().zip(left.chunks_exact(4).chain(right.chunks_exact(4))) {
   *sample = f32::from_le_bytes(bytes.try_into().unwrap());
  }
  let ticket = unsafe { submit(session.handle, self.input.as_ptr(), 480, self.epoch) };
  (ticket != 0).then_some((ticket, self.epoch | if using_fallback { 1 << 31 } else { 0 }))
 }

 pub fn read_planar(&mut self, (ticket, epoch): (u64, u32)) -> Option<&[f32]> {
  let session = if epoch >> 31 == 1 { self.fallback.as_ref()? } else { &self.session };
  let (_, read) = session.split?;
  let result = unsafe { read(session.handle, ticket, epoch & 0x00ff_ffff, self.output.as_mut_ptr(), 480) };
  (result == 1).then_some(self.output.as_slice())
 }
}
