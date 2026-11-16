//! Renderer-independent view of an existing owned capture packet.
//! No allocation, coordinate mapping, persistent identity, or gain application.
pub(crate) const MAX_OBJECTS: usize = 160;
pub(crate) const PROPERTY_BYTES: usize = 32;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct Property {
    pub ident: u32,
    pub frames: u32,
    pub typ: u32,
    pub flags: u32,
    /// Raw source axes and scale, including radius and negative height.
    /// Values are an update only when the source flags say so.
    pub xyz: [f64; 3],
    pub gain: f32,
}

pub(crate) struct CaptureInput<'a> {
    data: &'a [u8],
    count: usize,
    frames: usize,
}
impl<'a> CaptureInput<'a> {
    pub fn new(data: &'a [u8], count: usize, frames: usize) -> Result<Self, &'static str> {
        if !(1..=MAX_OBJECTS).contains(&count) || !(1..=4096).contains(&frames)
            || data.len() != count * (PROPERTY_BYTES + frames * 4)
        {
            return Err("Invalid packet length/count");
        }
        Ok(Self { data, count, frames })
    }
    pub fn property(&self, slot: usize) -> Result<Property, &'static str> {
        assert!(slot < self.count);
        let p = &self.data[slot * PROPERTY_BYTES..(slot + 1) * PROPERTY_BYTES];
        let u = |i| u32::from_le_bytes(p[i..i + 4].try_into().unwrap());
        let f = |i| f32::from_le_bytes(p[i..i + 4].try_into().unwrap());
        let prop = Property { ident: u(0), frames: u(4), typ: u(8), flags: u(12),
            xyz: [f(16) as f64, f(20) as f64, f(24) as f64], gain: f(28) };
        if prop.frames as usize != self.frames || !prop.xyz.iter().all(|v| v.is_finite())
            || !prop.gain.is_finite()
        {
            return Err("Invalid native property");
        }
        // Type support and validity of an update belong to each backend adapter.
        Ok(prop)
    }
    pub fn pcm_bytes(&self, slot: usize) -> &'a [u8] {
        assert!(slot < self.count);
        let at = self.count * PROPERTY_BYTES + slot * self.frames * 4;
        &self.data[at..at + self.frames * 4]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn packet() -> Vec<u8> {
        let mut data = Vec::new();
        for n in [9u32, 2, 0x80000000, 0] { data.extend(n.to_le_bytes()); }
        for v in [3f32, -4., 12., 0.25, -0.5, 0.125] { data.extend(v.to_le_bytes()); }
        data
    }
    #[test]
    fn raw_axes_radius_flags_gain_and_unknown_type_survive() {
        let bytes = packet();
        let input = CaptureInput::new(&bytes, 1, 2).unwrap();
        let p = input.property(0).unwrap();
        assert_eq!(p.xyz, [3., -4., 12.]);
        assert_eq!(p.xyz.iter().map(|v| v*v).sum::<f64>().sqrt(), 13.);
        assert_eq!((p.ident, p.typ, p.flags, p.gain), (9, 0x80000000, 0, 0.25));
        assert_eq!(input.pcm_bytes(0), &bytes[32..]);
        assert_eq!(input.pcm_bytes(0).as_ptr(), bytes[32..].as_ptr());
    }
    #[test]
    fn structural_errors_do_not_reach_backend() {
        let bytes = packet();
        for (count, frames) in [(0,2),(161,2),(1,0),(1,4097),(2,2)] {
            assert!(CaptureInput::new(&bytes,count,frames).is_err());
        }
        assert!(CaptureInput::new(&bytes[..39],1,2).is_err());
        let mut bad = bytes.clone();bad[4..8].copy_from_slice(&3u32.to_le_bytes());
        assert!(CaptureInput::new(&bad,1,2).unwrap().property(0).is_err());
        for at in [16,20,24,28] {
            let mut bad=bytes.clone();bad[at..at+4].copy_from_slice(&f32::NAN.to_le_bytes());
            assert!(CaptureInput::new(&bad,1,2).unwrap().property(0).is_err());
        }
    }
}
