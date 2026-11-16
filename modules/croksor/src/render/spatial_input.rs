//! Optional autonomous backend. Uses the same N03 raw view, no OLR policy.
use crate::capture_input::CaptureInput;
use std::{ffi::{c_void, c_char, CStr}};
#[link(name="kernel32")]
unsafe extern "system" {
    fn LoadLibraryExW(path:*const u16, file:*mut c_void, flags:u32)->*mut c_void;
    fn GetProcAddress(module:*mut c_void,name:*const c_char)->*mut c_void;
    fn FreeLibrary(module:*mut c_void)->i32;
}
struct Module(*mut c_void);
impl Drop for Module {fn drop(&mut self){unsafe{FreeLibrary(self.0);}}}
pub struct Spatial {
    pub layout_code:u32, channels:usize,
    _module:Module, handle:*mut c_void, n:usize, last:Option<u64>,
    process:unsafe extern "C" fn(*mut c_void,u64,u32,u32,*const u8,*const u8,*mut f32)->i32,
    flush:unsafe extern "C" fn(*mut c_void,*mut f32,u32)->i32,
    destroy:unsafe extern "C" fn(*mut c_void),
    error:unsafe extern "C" fn()->*const c_char,
}
// Instance is moved to the renderer worker before use; never shared concurrently.
unsafe impl Send for Spatial {}
impl Drop for Spatial {fn drop(&mut self){unsafe{if !self.handle.is_null(){(self.destroy)(self.handle);}}}}
impl Spatial {
    pub fn new(n:u32,path:&str)->Result<Self,String>{Self::new_layout(n,path,714)}
    pub fn new_layout(n:u32,path:&str,code:u32)->Result<Self,String>{
        let channels=crate::layouts::get(code)?.labels.len();
        if !(1..=4096).contains(&n)||!std::path::Path::new(path).is_absolute(){return Err("Spatial renderer needs absolute DLL path and valid frames".into());}
        let wide:Vec<u16>=path.encode_utf16().chain(Some(0)).collect();
        unsafe {
            let module=LoadLibraryExW(wide.as_ptr(),std::ptr::null_mut(),0x1100);
            if module.is_null(){return Err("Spatial renderer DLL load failed".into());}
            let owned=Module(module);
            let symbol=|name:&[u8]|->Result<*mut c_void,String>{let p=GetProcAddress(module,name.as_ptr().cast());if p.is_null(){Err("Spatial renderer export missing".into())}else{Ok(p)}};
            let loaded=(||->Result<Self,String>{
                let abi:unsafe extern "C" fn()->u32=std::mem::transmute(symbol(b"nsp_spatial_abi\0")?);
                if abi()!=1{return Err("Spatial renderer ABI mismatch".into());}
                let create:unsafe extern "C" fn()->*mut c_void=std::mem::transmute(symbol(b"nsp_spatial_create\0")?);
                let mut s=Self{layout_code:code,channels,_module:owned,handle:std::ptr::null_mut(),n:n as usize,last:None,
                    process:std::mem::transmute(symbol(b"nsp_spatial_process\0")?),flush:std::mem::transmute(symbol(b"nsp_spatial_flush\0")?),
                    destroy:std::mem::transmute(symbol(b"nsp_spatial_destroy\0")?),error:std::mem::transmute(symbol(b"nsp_spatial_error\0")?)};
                s.handle=if code==714{create()}else{let create_layout:unsafe extern "C" fn(u32)->*mut c_void=std::mem::transmute(symbol(b"nsp_spatial_create_layout\0")?);create_layout(code)};if s.handle.is_null(){return Err(s.message());}Ok(s)
            })();
            loaded
        }
    }
    fn message(&self)->String{unsafe{CStr::from_ptr((self.error)()).to_string_lossy().into_owned()}}
    /// LIRA writes interleaved PCM directly into the caller's owned output pool.
    pub fn process_into(&mut self,seq:u64,count:usize,data:&[u8],output:&mut [f32])->Result<(),String>{
        if output.len()!=self.n*self.channels{return Err("Invalid LIRA output length".into());}
        let input=CaptureInput::new(data,count,self.n).map_err(str::to_string)?;
        if self.last.is_some_and(|v|v.checked_add(1)!=Some(seq)){return Err("Sequence discontinuity: rebuild native pipeline".into());}
        for slot in 0..count {
            input.property(slot).map_err(str::to_string)?;
            for bytes in input.pcm_bytes(slot).chunks_exact(4){if !f32::from_le_bytes(bytes.try_into().unwrap()).is_finite(){return Err("Nonfinite PCM".into());}}
        }
        if unsafe{(self.process)(self.handle,seq,count as u32,self.n as u32,data.as_ptr(),data[count*32..].as_ptr(),output.as_mut_ptr())}!=0{return Err(self.message());}
        self.last=Some(seq);Ok(())
    }
    pub fn finish(&mut self)->Result<Vec<f32>,String>{
        let mut tail=vec![0.;512*self.channels];let frames=unsafe{(self.flush)(self.handle,tail.as_mut_ptr(),512)};
        if frames<0{return Err(self.message());}tail.truncate(frames as usize*self.channels);Ok(tail)
    }
}
