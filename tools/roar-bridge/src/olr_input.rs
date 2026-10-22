//! Existing OLR-specific interpretation of the shared raw input.
use crate::capture_input::Property;

#[derive(Clone, Copy, Default)]
pub(crate) struct State {
    pub present: bool,
    pub ident: u32,
    pub typ: u32,
    pub generation: u64,
    pub position: Option<[f64; 3]>,
}
impl State {
    /// Existing slot-local policy. Does not associate identities across slots.
    pub fn observe(&mut self, prop: Property) -> (bool, bool, bool) {
        let created = !self.present || self.ident != prop.ident || self.typ != prop.typ || prop.flags & 1 != 0;
        let retired = created && self.present;
        if created {
            *self = Self { present: true, ident: prop.ident, typ: prop.typ,
                generation: self.generation + 1, position: None };
        }
        let updated = prop.typ == 1 && prop.flags & 4 != 0;
        if updated { self.position = Some(prop.xyz); }
        (created, retired, updated)
    }
}

/// Source axes -> OLR axes. Full source position stays in State.
pub(crate) fn coordinates([x, y, z]: [f64; 3], scaled: bool, reference: f64) -> [f32; 3] {
    let radius = (x * x + y * y + z * z).sqrt();
    let scale = if scaled { 1. / reference } else if radius > 0. { 1. / radius } else { 1. };
    [(x * scale) as f32, (-z * scale) as f32, (y * scale) as f32]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn updates_require_flags_and_generation_does_not_borrow_old_position() {
        let mut state = State::default();
        let mut prop = Property { ident: 9, typ: 1, flags: 5, xyz: [3.,-4.,12.], ..Property::default() };
        assert_eq!(state.observe(prop),(true,false,true));
        prop.flags=0;prop.xyz=[99.;3];
        assert_eq!(state.observe(prop),(false,false,false));
        assert_eq!(state.position,Some([3.,-4.,12.]));
        prop.ident=10;
        assert_eq!(state.observe(prop),(true,true,false));
        assert_eq!(state.position,None);
        assert_eq!(state.generation,2);
    }
    #[test]
    fn mapping_is_backend_only_and_preserves_negative_height() {
        assert_eq!(coordinates([3.,-4.,12.],true,2.),[1.5,-6.,-2.]);
        assert_eq!(coordinates([0.;3],false,1.),[0.;3]);
        let direction=coordinates([3.,-4.,12.],false,1.);
        assert_eq!(direction,[(3./13.) as f32,(-12./13.) as f32,(-4./13.) as f32]);
    }
}
