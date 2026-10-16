//! Experimental, single-threaded ABI. All pointers belong to the Python caller.
use aom_roar::*;
use std::{
    panic::{AssertUnwindSafe, catch_unwind},
    slice,
};
mod packet;

struct Bridge {
    rdr: RoarRenderer,
    group: GroupId,
    frames: Samples,
}
fn guarded(f: impl FnOnce() -> Result<(), OarError>) -> i32 {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(())) => 0,
        Ok(Err(e)) => e as i32,
        Err(_) => -1000,
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn nsp_create(frames: u32, rate: u32) -> *mut std::ffi::c_void {
    catch_unwind(|| -> Result<_, OarError> {
        let frames = Samples::new(frames)?;
        let mut rdr = RoarRenderer::create(&Config::new(
            Layout::Layout714,
            frames,
            SampleRate::new(rate)?,
        )?)?;
        let group = rdr.add_audio_group()?;
        Ok(Box::into_raw(Box::new(Bridge { rdr, group, frames })) as *mut std::ffi::c_void)
    })
    .ok()
    .and_then(Result::ok)
    .unwrap_or(std::ptr::null_mut())
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_destroy(p: *mut std::ffi::c_void) {
    if !p.is_null() {
        drop(unsafe { Box::from_raw(p as *mut Bridge) });
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_add(p: *mut std::ffi::c_void, id: u32, bed: u32) -> i32 {
    guarded(|| {
        if p.is_null() {
            return Err(OarError::InvalidParameter);
        }
        let b = unsafe { &mut *(p as *mut Bridge) };
        let config = if bed == 1 {
            AudioElementConfig::ChannelBased(ChannelBasedConfig {
                layout: Layout::Layout714,
                downmix_info: None,
                rendering_config: None,
            })
        } else {
            AudioElementConfig::ObjectBased(ObjectBasedConfig {
                num_objects: 1,
                rendering_config: None,
            })
        };
        b.rdr.add_element(b.group, id, &config)
    })
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_remove(p: *mut std::ffi::c_void, id: u32) -> i32 {
    guarded(|| {
        if p.is_null() {
            return Err(OarError::InvalidParameter);
        }
        unsafe { &mut *(p as *mut Bridge) }.rdr.remove_element(id)
    })
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_position(
    p: *mut std::ffi::c_void,
    id: u32,
    x: f32,
    y: f32,
    z: f32,
) -> i32 {
    guarded(|| {
        if p.is_null() {
            return Err(OarError::InvalidParameter);
        }
        let b = unsafe { &mut *(p as *mut Bridge) };
        b.rdr.update_element_positions(
            id,
            &ObjectPosition::Cartesian(vec![CartesianCoordinate { x, y, z }]),
            b.frames,
        )
    })
}
/// `pcm` concatenates channels for each ID; output is 12-channel planar f32.
/// Caller guarantees pointer lengths, no aliasing and exclusive renderer access.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_render(
    p: *mut std::ffi::c_void,
    ids: *const u32,
    count: usize,
    pcm: *const f32,
    len: usize,
    out: *mut f32,
    out_len: usize,
) -> i32 {
    guarded(|| {
        if p.is_null() || ids.is_null() || pcm.is_null() || out.is_null() {
            return Err(OarError::InvalidParameter);
        }
        let b = unsafe { &mut *(p as *mut Bridge) };
        let n = b.frames.value() as usize;
        let ids = unsafe { slice::from_raw_parts(ids, count) };
        let channels: Result<Vec<_>, _> = ids
            .iter()
            .map(|id| {
                b.rdr
                    .active_elements()
                    .get(id)
                    .map(|c| c.channels())
                    .ok_or(OarError::InvalidParameter)
            })
            .collect();
        let channels = channels?;
        if channels.iter().sum::<usize>() * n != len || out_len != 12 * n {
            return Err(OarError::InvalidParameter);
        }
        let pcm = unsafe { slice::from_raw_parts(pcm, len) };
        let mut offset = 0;
        let slices: Vec<Vec<&[f32]>> = channels
            .iter()
            .map(|c| {
                let result = pcm[offset..offset + c * n].chunks_exact(n).collect();
                offset += c * n;
                result
            })
            .collect();
        let inputs: Result<Vec<_>, _> = ids
            .iter()
            .zip(slices.iter())
            .map(|(&id, s)| Ok((id, PlanarBufferRef::new(s, s.len(), b.frames)?)))
            .collect();
        let mut outputs: Vec<_> = unsafe { slice::from_raw_parts_mut(out, out_len) }
            .chunks_exact_mut(n)
            .collect();
        b.rdr.render(
            &inputs?,
            &mut PlanarBufferMut::new(&mut outputs, 12, b.frames)?,
        )
    })
}
