//! C interface. All pointed-to memory is caller-owned except the opaque session.
use crate::{
    ptp,
    rtp::{FRAMES, Flow},
    session::{self, Config, Counters, Event, Session},
    time::TimestampMode,
};
use std::{
    ffi::{CStr, c_char, c_void},
    mem,
    net::{Ipv4Addr, SocketAddrV4},
    ptr, slice,
    time::Duration,
};

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct FlowV1 {
    pub dest_ip: [u8; 4],
    pub src_interface_ip: [u8; 4],
    pub port: u16,
    pub ttl: u8,
    pub channels: u8,
    pub ssrc: u32,
    pub channel_offset: u32,
    pub payload_type: u8,
    pub source_filter: u8,
    pub reserved: [u8; 2],
}

#[repr(C)]
#[derive(Default)]
pub struct ConfigV1 {
    pub struct_size: u32,
    pub channels: u32,
    pub sample_rate: u32,
    pub buffer_ms: u32,
    pub prebuffer_ms: u32,
    pub timestamping: u32,
    pub lock_timeout_ms: u32,
    pub loss_timeout_ms: u32,
    pub ptp_domain: u32,
    pub ptp_interface_ip: [u8; 4],
    pub ptp_multicast_ip: [u8; 4],
    pub ptp_master_ip: [u8; 4],
    pub ptp_gmid: [u8; 8],
    pub ptp_event_port: u16,
    pub ptp_general_port: u16,
    pub ptp_local_event_port: u16,
    pub ptp_local_general_port: u16,
    pub sap: u8,
    pub default_ttl: u8,
    pub default_port: u16,
    pub default_dest_ip: [u8; 4],
    pub default_src_interface_ip: [u8; 4],
    pub ssrc_base: u32,
    pub flow_count: u32,
    pub flows: *const FlowV1,
    pub channel_labels: *const *const c_char,
    pub session_name: *const c_char,
    pub channel_map: *const u32,
    pub input_channels: u32,
    pub default_payload_type: u8,
    pub default_source_filter: u8,
    pub reserved: [u8; 2],
}

fn fallback<T: Default + PartialEq>(value: T, default: T) -> T {
    if value == T::default() {
        default
    } else {
        value
    }
}

fn endpoint(ip: Ipv4Addr) -> bool {
    !ip.is_unspecified() && !ip.is_broadcast()
}
fn interface(ip: Ipv4Addr) -> bool {
    !ip.is_multicast() && !ip.is_broadcast()
}

unsafe fn text(ptr: *const c_char, max_len: usize, label: bool) -> Option<String> {
    if ptr.is_null() {
        return None;
    }
    let value = unsafe { CStr::from_ptr(ptr) }.to_str().ok()?;
    if value.is_empty()
        || value.len() > max_len
        || value.chars().any(|c| c.is_control() || (label && c == ','))
    {
        return None;
    }
    Some(value.to_owned())
}

unsafe fn config(c: &ConfigV1) -> Option<Config> {
    let channels = c.channels as usize;
    let inputs = fallback(c.input_channels, c.channels) as usize;
    if c.sample_rate != 48_000
        || !(1..=256).contains(&channels)
        || !(1..=256).contains(&inputs)
        || c.sap > 1
        || c.ptp_domain > 127
        || c.default_source_filter > 1
        || c.reserved != [0; 2]
    {
        return None;
    }
    let mode = match c.timestamping {
        0 => TimestampMode::Auto,
        1 => TimestampMode::Software,
        2 => TimestampMode::Hardware,
        _ => return None,
    };
    let buffer_ms = fallback(c.buffer_ms, 100);
    let prebuffer_ms = fallback(c.prebuffer_ms, 10.min(buffer_ms));
    let lock_ms = fallback(c.lock_timeout_ms, 8_000);
    let loss_ms = fallback(c.loss_timeout_ms, 3_000);
    if !(8..=1024).contains(&buffer_ms)
        || prebuffer_ms > buffer_ms.min(1000)
        || !(100..=120_000).contains(&lock_ms)
        || !(100..=120_000).contains(&loss_ms)
    {
        return None;
    }

    let map = if c.channel_map.is_null() {
        (0..channels).collect::<Vec<_>>()
    } else {
        unsafe { slice::from_raw_parts(c.channel_map, channels) }
            .iter()
            .map(|&x| x as usize)
            .collect()
    };
    if map.iter().any(|&x| x >= inputs) {
        return None;
    }
    let labels = if c.channel_labels.is_null() {
        (1..=channels).map(|i| format!("CH{i}")).collect()
    } else {
        unsafe { slice::from_raw_parts(c.channel_labels, channels) }
            .iter()
            .map(|&label| unsafe { text(label, 48, true) })
            .collect::<Option<Vec<_>>>()?
    };
    let name = if c.session_name.is_null() {
        "NativeSpatial Pipe AES67".into()
    } else {
        unsafe { text(c.session_name, 128, false) }?
    };

    let ptp_interface = Ipv4Addr::from(c.ptp_interface_ip);
    let multicast = Ipv4Addr::from(fallback(c.ptp_multicast_ip, [224, 0, 1, 129]));
    let master = Ipv4Addr::from(c.ptp_master_ip);
    let event_port = fallback(c.ptp_event_port, 319);
    let general_port = fallback(c.ptp_general_port, 320);
    if !interface(ptp_interface)
        || !multicast.is_multicast()
        || !interface(master)
        || event_port == general_port
    {
        return None;
    }
    let ptp = ptp::Config {
        domain: c.ptp_domain as u8,
        pinned_gm: (c.ptp_gmid != [0; 8]).then_some(c.ptp_gmid),
        interface: ptp_interface,
        multicast,
        unicast_master: (!master.is_unspecified()).then_some(master),
        event_port,
        general_port,
        local_event_port: c.ptp_local_event_port,
        local_general_port: c.ptp_local_general_port,
        loss_ns: i128::from(loss_ms) * 1_000_000,
    };
    let default_dest = fallback(c.default_dest_ip, [239, 69, 0, 1]);
    let base_port = fallback(c.default_port, 5004);
    let ttl = fallback(c.default_ttl, 1);
    let payload_type = fallback(c.default_payload_type, 96);
    if !(96..=127).contains(&payload_type) {
        return None;
    }
    let defaults;
    let raw = if c.flow_count == 0 {
        defaults = (0..channels.div_ceil(8))
            .map(|index| {
                Some(FlowV1 {
                    dest_ip: default_dest,
                    src_interface_ip: c.default_src_interface_ip,
                    port: base_port.checked_add(2 * index as u16)?,
                    ttl,
                    channels: (channels - index * 8).min(8) as u8,
                    channel_offset: (index * 8) as u32,
                    payload_type,
                    source_filter: c.default_source_filter,
                    ..FlowV1::default()
                })
            })
            .collect::<Option<Vec<_>>>()?;
        defaults.as_slice()
    } else {
        if c.flows.is_null() || c.flow_count as usize > channels {
            return None;
        }
        unsafe { slice::from_raw_parts(c.flows, c.flow_count as usize) }
    };
    let base_ssrc = fallback(c.ssrc_base, 0x4e53_5000);
    let mut covered = vec![false; channels];
    let mut flows: Vec<Flow> = Vec::with_capacity(raw.len());
    for (index, flow) in raw.iter().enumerate() {
        let offset = flow.channel_offset as usize;
        let count = usize::from(flow.channels);
        let dest = Ipv4Addr::from(flow.dest_ip);
        let source = Ipv4Addr::from(flow.src_interface_ip);
        let pt = fallback(flow.payload_type, payload_type);
        let ssrc = fallback(flow.ssrc, base_ssrc.wrapping_add(index as u32));
        let socket = SocketAddrV4::new(dest, flow.port);
        if count == 0
            || count > 8
            || offset >= channels
            || count > channels - offset
            || flow.port == 0
            || !endpoint(dest)
            || !interface(source)
            || flow.source_filter > 1
            || flow.reserved != [0; 2]
            || !(96..=127).contains(&pt)
            || flows
                .iter()
                .any(|existing| existing.ssrc == ssrc || existing.destination == socket)
            || covered[offset..offset + count]
                .iter()
                .any(|&claimed| claimed)
        {
            return None;
        }
        covered[offset..offset + count].fill(true);
        flows.push(Flow {
            destination: socket,
            interface: source,
            ttl: fallback(flow.ttl, ttl),
            ssrc,
            offset,
            channels: count,
            payload_type: pt,
            source_filter: flow.source_filter == 1,
        });
    }
    if !covered.into_iter().all(|claimed| claimed) {
        return None;
    }
    Some(Config {
        channels,
        inputs,
        map,
        labels,
        name,
        capacity: buffer_ms as usize * FRAMES,
        prebuffer: prebuffer_ms as usize * FRAMES,
        ptp,
        mode,
        lock_timeout: Duration::from_millis(u64::from(lock_ms)),
        flows,
        sap: c.sap == 1,
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_create(out: *mut *mut Session) -> i32 {
    if out.is_null() {
        return session::INVALID;
    }
    unsafe { out.write(Box::into_raw(Box::new(Session::default()))) };
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_configure(handle: *mut Session, raw: *const ConfigV1) -> i32 {
    let Some(handle) = (unsafe { handle.as_ref() }) else {
        return session::INVALID;
    };
    if raw.is_null() || unsafe { raw.cast::<u32>().read() } as usize != mem::size_of::<ConfigV1>() {
        return session::INVALID;
    }
    let Some(config) = (unsafe { config(&*raw) }) else {
        return session::INVALID;
    };
    handle.configure(config)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_start(handle: *mut Session) -> i32 {
    unsafe { handle.as_ref() }.map_or(session::INVALID, Session::start)
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_stop(handle: *mut Session, timeout_ms: u32) -> i32 {
    unsafe { handle.as_ref() }.map_or(session::INVALID, |h| h.stop(timeout_ms))
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_poll_lifecycle_event(
    handle: *mut Session,
    out: *mut Event,
) -> i32 {
    let Some(handle) = (unsafe { handle.as_ref() }) else {
        return session::INVALID;
    };
    if out.is_null() {
        return session::INVALID;
    }
    if let Some(event) = handle.poll() {
        unsafe { out.write(event) };
        1
    } else {
        0
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_counters(handle: *mut Session, out: *mut Counters) -> i32 {
    let Some(handle) = (unsafe { handle.as_ref() }) else {
        return session::INVALID;
    };
    if out.is_null() {
        return session::INVALID;
    }
    unsafe { out.write(handle.counters()) };
    0
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_sdp(
    handle: *mut Session,
    flow_index: u32,
    out: *mut c_char,
    capacity: usize,
) -> i32 {
    let Some(handle) = (unsafe { handle.as_ref() }) else {
        return session::INVALID;
    };
    if out.is_null() && capacity != 0 {
        return session::INVALID;
    }
    handle.with_sdp(flow_index as usize, |sdp| {
        let size = sdp.len() + 1;
        if !out.is_null() {
            if capacity < size {
                return session::BUFFER_TOO_SMALL;
            }
            unsafe {
                ptr::copy_nonoverlapping(sdp.as_ptr(), out.cast(), size - 1);
                out.add(size - 1).write(0);
            }
        }
        size as i32
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_close(handle: *mut Session, timeout_ms: u32) -> i32 {
    let status = unsafe { nsp_aes67_stop(handle, timeout_ms) };
    if status == 0 {
        drop(unsafe { Box::from_raw(handle) });
    }
    status
}

pub type Release = unsafe extern "C" fn(*mut c_void);

#[unsafe(no_mangle)]
pub unsafe extern "C" fn nsp_aes67_submit_leased(
    context: *mut c_void,
    first_frame: u64,
    pcm: *const f32,
    frames: u32,
    channels: u32,
    tail: u32,
    token: *mut c_void,
    release_fn: Option<Release>,
) -> i32 {
    let Some(handle) = (unsafe { context.cast::<Session>().as_ref() }) else {
        return session::INVALID;
    };
    let Some(release) = release_fn else {
        return session::INVALID;
    };
    if pcm.is_null()
        || token.is_null()
        || frames == 0
        || tail > 1
        || first_frame.checked_add(u64::from(frames)).is_none()
    {
        return session::INVALID;
    }
    let status = unsafe { handle.submit(first_frame, pcm, frames as usize, channels as usize) };
    if status == 0 {
        unsafe { release(token) };
    }
    status
}

#[cfg(test)]
mod tests;
