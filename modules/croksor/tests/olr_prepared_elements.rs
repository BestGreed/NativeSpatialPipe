//! Regression for prepared independent OLR elements and their real-time reuse.
use aom_roar::*;
use std::alloc::{GlobalAlloc, Layout as AllocationLayout, System};
use std::cell::Cell;

struct TrackingAllocator;
thread_local! {
    static TRACKING: Cell<bool> = const { Cell::new(false) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}
unsafe impl GlobalAlloc for TrackingAllocator {
    unsafe fn alloc(&self, layout: AllocationLayout) -> *mut u8 {
        if TRACKING.try_with(Cell::get).unwrap_or(false) {
            let _ = ALLOCATIONS.try_with(|count| count.set(count.get() + 1));
        }
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: AllocationLayout) {
        unsafe { System.dealloc(ptr, layout) }
    }
}
#[global_allocator]
static ALLOCATOR: TrackingAllocator = TrackingAllocator;

fn prepared(count: u32) -> RoarRenderer {
    let samples = Samples::new(480).unwrap();
    let config = Config::new(OutputLayout::Stereo, samples, SampleRate::new(48000).unwrap()).unwrap();
    let mut renderer = RoarRenderer::create(&config).unwrap();
    renderer.set_object_position_metadata_unit_to_process(samples).unwrap();
    renderer.prepare_olr_geometry().unwrap();
    let group = renderer.add_audio_group().unwrap();
    for id in 1..=count {
        renderer.add_element(group, id, &AudioElementConfig::ObjectBased(
            ObjectBasedConfig::new(1, None).unwrap())).unwrap();
    }
    renderer
}

fn render(renderer: &mut RoarRenderer, id: u32, position: &ObjectPosition) -> [f32; 960] {
    let samples = Samples::new(480).unwrap();
    renderer.update_element_positions(id, position, samples).unwrap();
    let audio = [0.125f32; 480];
    let channels = [&audio[..]];
    let input = PlanarBufferRef::new(&channels, 1, samples).unwrap();
    let mut output = [0.; 960];
    let (left, right) = output.split_at_mut(480);
    let mut outputs = [left, right];
    renderer.render_olr_sparse(&[(id, input)],
        &mut PlanarBufferMut::new(&mut outputs, 2, samples).unwrap()).unwrap();
    output
}

#[test]
fn a_new_generation_uses_prepared_memory_and_has_fresh_position_history() {
    let mut renderer = prepared(32);
    let left = ObjectPosition::Cartesian(vec![CartesianCoordinate::new(-1., 1., 0.).unwrap()]);
    let right = ObjectPosition::Cartesian(vec![CartesianCoordinate::new(1., 1., 0.).unwrap()]);
    let _ = render(&mut renderer, 1, &left);
    let _ = render(&mut renderer, 2, &right);
    let mut fresh = prepared(1);
    let expected = render(&mut fresh, 1, &right);
    ALLOCATIONS.with(|count| count.set(0));
    TRACKING.with(|tracking| tracking.set(true));
    renderer.reset_olr_element(1).unwrap();
    let actual = render(&mut renderer, 1, &right);
    let first_use = render(&mut renderer, 32, &right);
    TRACKING.with(|tracking| tracking.set(false));
    assert_eq!(ALLOCATIONS.with(Cell::get), 0);
    assert_eq!(actual, expected);
    assert_eq!(first_use, expected);
}

#[test]
fn temporarily_missing_input_preserves_its_independent_clock_and_interpolation() {
    let mut interleaved = prepared(2);
    let mut uninterrupted = prepared(1);
    let first = ObjectPosition::Cartesian(vec![CartesianCoordinate::new(-1., 1., 0.).unwrap()]);
    let second = ObjectPosition::Cartesian(vec![CartesianCoordinate::new(1., 1., 0.).unwrap()]);
    assert_eq!(render(&mut interleaved, 1, &first), render(&mut uninterrupted, 1, &first));
    let _ = render(&mut interleaved, 2, &first);
    assert_eq!(render(&mut interleaved, 1, &second), render(&mut uninterrupted, 1, &second));
}
