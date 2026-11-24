//! C interface. All pointed-to memory is caller-owned except the opaque session.
use crate::{
    ptp,
    rtp::{Encoding, Flow},
    session::{self, Config, Counters, Event, Session},
    time::{PacketTiming, SUPPORTED_PACKET_TIMES_US, SUPPORTED_SAMPLE_RATES, TimestampMode},
};
use std::{
    ffi::{CStr, c_char, c_void},
    mem,
    net::{Ipv4Addr, SocketAddrV4},
    ptr, slice,
    time::Duration,
};

/// AES67 media-profile convention: a sender offers streams of 1-8 channels
/// (AES67-2023 sec 7.3 line 1556-1558). flow_mode=standard enforces this;
/// flow_mode=extension allows a wider single flow as an explicit EXTENSION.
const STANDARD_FLOW_CHANNELS: usize = 8;
/// Hard per-flow ceiling for extension mode. 64 is the RAVENNA LKM ceiling;
/// the 1440-byte payload budget normally binds earlier.
const EXTENSION_FLOW_CHANNELS: usize = 64;
/// Total per-session channel ceiling.
const MAX_CHANNELS: usize = 256;
const MAX_FLOWS: usize = 8;
const DEFAULT_PORT: u16 = 5004;
const DEFAULT_SECONDARY_OFFSET: u16 = 2;
const DEFAULT_DEST: [u8; 4] = [239, 69, 0, 1];
const DEFAULT_TTL: u8 = 32;
const DEFAULT_SAP_INTERVAL_MS: u32 = 30_000;
/// RFC 2974 clause 3 (line 102-103): global-scope SAP announcements use
/// 224.2.127.254; administrators may reconfigure the group.
const DEFAULT_SAP_ADDRESS: [u8; 4] = [224, 2, 127, 254];

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

/// Original 128-byte configuration. The two former reserved bytes now select
/// the flow mode (0 standard, 1 extension) and ST 2022-7 redundancy
/// (0/1); old callers passed zero, which selects the standard AES67 profile.
#[repr(C)]
#[derive(Clone, Copy, Default)]
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
    pub flow_mode: u8,
    pub redundant: u8,
}

/// Extended configuration carrying packet time, encoding and the standard
/// port policy. Callers pass struct_size = sizeof(NspAes67Config) (144).
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct ConfigV2 {
    pub base: ConfigV1,
    pub packet_time_us: u32,
    pub sap_interval_ms: u32,
    /// Explicit samples per packet. Zero derives the AES67 Table 2 count from
    /// (sample rate, packet time); nonzero must agree with it (this makes the
    /// 333 us / 16-sample row and short packet times exact rather than float).
    pub frames_per_packet: u32,
    /// SAP announcement group; all zero selects RFC 2974's 224.2.127.254.
    pub sap_address: [u8; 4],
    pub bit_depth: u8,
    pub port_policy: u8,
    pub reserved: [u8; 6],
}

impl ConfigV2 {
    /// Test/support helper: view the extended config through its ABI prefix.
    #[allow(dead_code)]
    fn cast(&self) -> *const ConfigV1 {
        (self as *const ConfigV2).cast()
    }
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

/// RFC 3550 clause 5.1 line 857-862: SSRC SHOULD be random. Derive a random
/// base per session while keeping flow k = base + k (unique for <= 8 flows).
fn random_ssrc_base() -> u32 {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;
    let pid = u64::from(std::process::id());
    let mixed = (nanos ^ (pid << 32) ^ 0x9E37_79B9_7F4A_7C15) as u32;
    if mixed == 0 { 1 } else { mixed }
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

/// Balanced flow plan: one flow when the whole layout fits the per-flow
/// channel cap and the 1440-byte payload budget, otherwise balanced flows.
/// standard passes cap = min(8, fit); extension passes cap = min(64, fit),
/// so extension packs as much as the 1440-byte payload allows and only splits
/// when the payload does not fit.
fn plan_flows(channels: usize, fit: usize, cap_limit: usize) -> Option<Vec<usize>> {
    if channels == 0 {
        return None;
    }
    let cap = cap_limit.min(fit);
    if cap == 0 {
        return None;
    }
    if channels <= cap {
        return Some(vec![channels]);
    }
    let flow_count = channels.div_ceil(cap);
    if flow_count > MAX_FLOWS {
        return None;
    }
    let quotient = channels / flow_count;
    let remainder = channels % flow_count;
    Some(
        (0..flow_count)
            .map(|index| quotient + usize::from(index < remainder))
            .collect(),
    )
}

fn add_group(base: Ipv4Addr, index: usize) -> Option<Ipv4Addr> {
    let value = u32::from(base);
    let next = value.checked_add(index as u32)?;
    let address = Ipv4Addr::from(next);
    address.is_multicast().then_some(address)
}

fn even_port(port: u16) -> bool {
    port >= 1024 && port != u16::MAX && port % 2 == 0
}
unsafe fn config(raw: *const ConfigV1) -> Option<Config> {
    let size = unsafe { raw.cast::<u32>().read() } as usize;
    let extended = size == mem::size_of::<ConfigV2>();
    if !extended && size != mem::size_of::<ConfigV1>() {
        return None;
    }
    let (base, packet_time_us, sap_interval_ms, frames_per_packet, sap_address, bit_depth, port_policy) =
        if extended {
            let v2 = unsafe { &*raw.cast::<ConfigV2>() };
            if v2.reserved != [0; 6] {
                return None;
            }
            (
                &v2.base,
                v2.packet_time_us,
                v2.sap_interval_ms,
                v2.frames_per_packet,
                v2.sap_address,
                v2.bit_depth,
                v2.port_policy,
            )
        } else {
            (unsafe { &*raw }, 0, 0, 0, DEFAULT_SAP_ADDRESS, 0, 0)
        };

    let channels = base.channels as usize;
    let inputs = fallback(base.input_channels, base.channels) as usize;
    let sample_rate = fallback(base.sample_rate, 48_000);
    if !SUPPORTED_SAMPLE_RATES.contains(&sample_rate)
        || !(1..=MAX_CHANNELS).contains(&channels)
        || !(1..=MAX_CHANNELS).contains(&inputs)
        || base.sap > 1
        || base.ptp_domain > 127
        || base.default_source_filter > 1
        || base.flow_mode > 1
        || base.redundant > 1
        || port_policy > 1
    {
        return None;
    }
    let encoding = Encoding::from_abi(bit_depth)?;
    let packet_time_us = fallback(packet_time_us, 1_000);
    if !SUPPORTED_PACKET_TIMES_US.contains(&packet_time_us) {
        return None;
    }
    let timing = PacketTiming::new(sample_rate, packet_time_us)?;
    if frames_per_packet != 0 && frames_per_packet != timing.samples_per_packet() {
        return None;
    }
    let sap_interval_ms = fallback(sap_interval_ms, DEFAULT_SAP_INTERVAL_MS);
    let sap_address = Ipv4Addr::from(fallback(sap_address, DEFAULT_SAP_ADDRESS));
    if !(1_000..=300_000).contains(&sap_interval_ms) || !sap_address.is_multicast() {
        return None;
    }
    let flow_mode = base.flow_mode;
    let redundant = base.redundant == 1;
    let mode = match base.timestamping {
        0 => TimestampMode::Auto,
        1 => TimestampMode::Software,
        2 => TimestampMode::Hardware,
        _ => return None,
    };
    let sample_rate_usize = sample_rate as usize;
    let buffer_ms = fallback(base.buffer_ms, 100);
    let prebuffer_ms = fallback(base.prebuffer_ms, 10.min(buffer_ms));
    let lock_ms = fallback(base.lock_timeout_ms, 8_000);
    let loss_ms = fallback(base.loss_timeout_ms, 3_000);
    if !(8..=1024).contains(&buffer_ms)
        || prebuffer_ms > buffer_ms.min(1000)
        || !(100..=120_000).contains(&lock_ms)
        || !(100..=120_000).contains(&loss_ms)
    {
        return None;
    }

    let map = if base.channel_map.is_null() {
        (0..channels).collect::<Vec<_>>()
    } else {
        unsafe { slice::from_raw_parts(base.channel_map, channels) }
            .iter()
            .map(|&x| x as usize)
            .collect()
    };
    if map.iter().any(|&x| x >= inputs) {
        return None;
    }
    let labels = if base.channel_labels.is_null() {
        (1..=channels).map(|i| format!("CH{i}")).collect()
    } else {
        unsafe { slice::from_raw_parts(base.channel_labels, channels) }
            .iter()
            .map(|&label| unsafe { text(label, 48, true) })
            .collect::<Option<Vec<_>>>()?
    };
    let name = if base.session_name.is_null() {
        "NativeSpatial Pipe AES67".into()
    } else {
        unsafe { text(base.session_name, 128, false) }?
    };

    let ptp_interface = Ipv4Addr::from(base.ptp_interface_ip);
    let multicast = Ipv4Addr::from(fallback(base.ptp_multicast_ip, [224, 0, 1, 129]));
    let master = Ipv4Addr::from(base.ptp_master_ip);
    let event_port = fallback(base.ptp_event_port, 319);
    let general_port = fallback(base.ptp_general_port, 320);
    if !interface(ptp_interface)
        || !multicast.is_multicast()
        || !interface(master)
        || event_port == general_port
    {
        return None;
    }
    let ptp = ptp::Config {
        domain: base.ptp_domain as u8,
        pinned_gm: (base.ptp_gmid != [0; 8]).then_some(base.ptp_gmid),
        interface: ptp_interface,
        multicast,
        unicast_master: (!master.is_unspecified()).then_some(master),
        event_port,
        general_port,
        local_event_port: base.ptp_local_event_port,
        local_general_port: base.ptp_local_general_port,
    };

    let default_dest = Ipv4Addr::from(fallback(base.default_dest_ip, DEFAULT_DEST));
    let base_port = fallback(base.default_port, DEFAULT_PORT);
    let ttl = fallback(base.default_ttl, DEFAULT_TTL);
    let payload_type = fallback(base.default_payload_type, 96);
    let source_interface = Ipv4Addr::from(base.default_src_interface_ip);
    // AES67 sec 6.3 line 1332-1333: RTP default port 5004 (RTCP 5005). This
    // sender allocates RTP media ports only; redundant legs use port + 2
    // (5006), the standard secondary-port convention used by the daemon.
    if !even_port(base_port)
        || redundant && base_port.checked_add(DEFAULT_SECONDARY_OFFSET).is_none()
    {
        return None;
    }
    if ttl == 0 || !(96..=127).contains(&payload_type) {
        return None;
    }
    let bytes = encoding.bytes();
    let fit = timing.max_channels_per_flow(bytes);
    if fit == 0 {
        return None;
    }
    // flow_mode: 0 = standard (AES67 1-8 channels per flow), 1 = extension
    // (fill each flow to the 1440-byte payload limit, then split).
    let standard_cap = STANDARD_FLOW_CHANNELS.min(fit);
    let extension_cap = EXTENSION_FLOW_CHANNELS.min(fit);
    let flow_cap = if flow_mode == 1 {
        extension_cap
    } else {
        standard_cap
    };
    let base_ssrc = if base.ssrc_base == 0 {
        random_ssrc_base()
    } else {
        base.ssrc_base
    };
    let flow_count = base.flow_count as usize;

    let mut flows: Vec<Flow> = Vec::with_capacity(MAX_FLOWS);
    if flow_count == 0 {
        // Module-derived plan: standard keeps 1-8 channels per flow; extension
        // packs to the 1440-byte payload ceiling and only splits when needed.
        let plan = match flow_mode {
            0 => plan_flows(channels, fit, standard_cap)?,
             // Explicit single-flow extension mode: up to 16 channels in one
            // flow, still gated by the 1440-byte payload budget (fit).
            1 => plan_flows(channels, fit, extension_cap)?,
            _ => return None,
        };
        if plan.len() > MAX_FLOWS {
            return None;
        }
        if plan.len() > 1 && !default_dest.is_multicast() {
            // Strict port policy cannot derive unique unicast destinations.
            return None;
        }
        let mut offset = 0;
        for (index, count) in plan.iter().copied().enumerate() {
            if count == 0 || count > flow_cap {
                return None;
            }
            let destination = if default_dest.is_multicast() {
                add_group(default_dest, index)?
            } else {
                default_dest
            };
            flows.push(Flow {
                destination: SocketAddrV4::new(destination, base_port),
                interface: source_interface,
                ttl,
                ssrc: base_ssrc.wrapping_add(index as u32),
                offset,
                channels: count,
                payload_type,
                source_filter: base.default_source_filter == 1,
            });
            offset += count;
        }
        if offset != channels {
            return None;
        }
    } else {
        if flow_count > MAX_FLOWS || base.flows.is_null() {
            return None;
        }
        let raw_flows = unsafe { slice::from_raw_parts(base.flows, flow_count) };
        let mut covered = vec![false; channels];
        let mut offset = 0usize;
        for (index, row) in raw_flows.iter().enumerate() {
            let count = usize::from(row.channels);
            let cap = flow_cap;
            let dest = Ipv4Addr::from(row.dest_ip);
            let source = Ipv4Addr::from(row.src_interface_ip);
            let pt = fallback(row.payload_type, payload_type);
            let port = fallback(row.port, base_port);
            if count == 0
                || count > cap
                || offset.checked_add(count).is_none_or(|end| end > channels)
                || usize::try_from(row.channel_offset).ok() != Some(offset)
                || !endpoint(dest)
                || !interface(source)
                || row.source_filter > 1
                || row.reserved != [0; 2]
                || !(96..=127).contains(&pt)
                || !even_port(port)
                || port_policy == 0 && port != base_port
            {
                return None;
            }
            let ssrc = fallback(row.ssrc, base_ssrc.wrapping_add(index as u32));
            let socket = SocketAddrV4::new(dest, port);
            if flows
                .iter()
                .any(|existing| existing.ssrc == ssrc || existing.destination == socket)
            {
                return None;
            }
            if dest.is_multicast()
                && flows
                    .iter()
                    .any(|existing| existing.destination.ip() == &dest)
            {
                return None;
            }
            if covered[offset..offset + count].iter().any(|&claimed| claimed) {
                return None;
            }
            covered[offset..offset + count].fill(true);
            flows.push(Flow {
                destination: socket,
                interface: source,
                ttl: fallback(row.ttl, ttl),
                ssrc,
                offset,
                channels: count,
                payload_type: pt,
                source_filter: row.source_filter == 1,
            });
            offset += count;
        }
        if offset != channels || !covered.into_iter().all(|claimed| claimed) {
            return None;
        }
    }

    Some(Config {
        channels,
        inputs,
        map,
        labels,
        name,
        capacity: buffer_ms as usize * sample_rate_usize / 1000,
        prebuffer: prebuffer_ms as usize * sample_rate_usize / 1000,
        ptp,
        mode,
        lock_timeout: Duration::from_millis(u64::from(lock_ms)),
        flows,
        sap: base.sap == 1,
        sap_interval: Duration::from_millis(u64::from(sap_interval_ms)),
        sap_destination: sap_address,
        timing,
        encoding,
        redundant,
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
    if raw.is_null() {
        return session::INVALID;
    }
    let size = unsafe { raw.cast::<u32>().read() } as usize;
    if size != mem::size_of::<ConfigV1>() && size != mem::size_of::<ConfigV2>() {
        return session::INVALID;
    }
    let Some(config) = (unsafe { config(raw) }) else {
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