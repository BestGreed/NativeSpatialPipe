//! Session-local scratch for one DTS:X HT endpoint.
//!
//! The control plane prepares late lanes. The render worker only installs
//! prepared buffers, returns released buffers to the controller, and
//! reconciles the current block's dynamic object keys.

pub(crate) const LANES: usize = 8;
pub(crate) const INITIAL_LANES: usize = 4;
pub(crate) const BED_CHANNELS: usize = 12;
pub(crate) const OBJECTS: usize = 64;
const SOURCE_SLOTS: usize = crate::capture_input::MAX_OBJECTS;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LaneKind {
    Bed,
    Pcm,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ObjectKey {
    pub(crate) lane: usize,
    pub(crate) source_slot: usize,
    pub(crate) generation: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum StorageError {
    InvalidLane,
    ObjectBudget,
    InvalidObject,
    BedUnavailable,
    DuplicateObject,
}

pub(crate) struct PreparedLane {
    kind: LaneKind,
    lane: usize,
    samples: Box<[f32]>,
}

pub(crate) struct ReleasedLane {
    pub(crate) prepared: PreparedLane,
    pub(crate) retired_objects: u64,
}

impl PreparedLane {
    pub(crate) fn new(
        kind: LaneKind,
        lane: usize,
        frames: usize,
    ) -> Result<Self, StorageError> {
        if lane >= LANES {
            return Err(StorageError::InvalidLane);
        }
        Ok(Self {
            kind,
            lane,
            samples: vec![0.; BED_CHANNELS * frames].into_boxed_slice(),
        })
    }

    /// Clear and retarget returned storage on the control thread.
    pub(crate) fn recycle_as(
        &mut self,
        kind: LaneKind,
        lane: usize,
    ) -> Result<(), StorageError> {
        if lane >= LANES {
            return Err(StorageError::InvalidLane);
        }
        self.samples.fill(0.);
        self.kind = kind;
        self.lane = lane;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Admission {
    Ready,
    Prepare,
    Preparing,
    Stale,
}

#[derive(Clone, Copy, Default)]
struct LaneLease {
    generation: u64,
    active: bool,
}

pub(crate) struct CompactStorage {
    frames: usize,
    leases: [[LaneLease; LANES]; 2],
    beds: [Option<Box<[f32]>>; LANES],
    pcm: [Option<Box<[f32]>>; LANES],
    mixed_bed: Box<[f32]>,
    object_samples: Box<[f32]>,
    objects: [Option<ObjectKey>; OBJECTS],
    // Zero means unmapped. Allocating a slot never grows a container.
    lookup: [[u8; SOURCE_SLOTS]; LANES],
}

impl CompactStorage {
    pub(crate) fn new(frames: usize) -> Self {
        let initial_lanes = || {
            std::array::from_fn(|lane| {
                (lane < INITIAL_LANES).then(|| vec![0.; BED_CHANNELS * frames].into_boxed_slice())
            })
        };
        Self {
            frames,
            leases: [[LaneLease::default(); LANES]; 2],
            beds: initial_lanes(),
            pcm: initial_lanes(),
            mixed_bed: vec![0.; BED_CHANNELS * frames].into_boxed_slice(),
            object_samples: vec![0.; OBJECTS * frames].into_boxed_slice(),
            objects: [None; OBJECTS],
            lookup: [[0; SOURCE_SLOTS]; LANES],
        }
    }

    fn leases(&mut self, kind: LaneKind) -> &mut [LaneLease; LANES] {
        match kind {
            LaneKind::Bed => &mut self.leases[0],
            LaneKind::Pcm => &mut self.leases[1],
        }
    }

    /// Prepare signals the caller to request storage once and skip this packet.
    /// Preparing signals that subsequent packets should also be skipped.
    pub(crate) fn admit(&mut self, kind: LaneKind, lane: usize, generation: u64) -> Admission {
        if generation == 0 || lane >= LANES {
            return Admission::Stale;
        }
        let ready = match kind {
            LaneKind::Bed => self.beds[lane].is_some(),
            LaneKind::Pcm => self.pcm[lane].is_some(),
        };
        let lease = &mut self.leases(kind)[lane];
        if lease.generation > generation || (lease.generation != 0 && !lease.active && lease.generation == generation) {
            return Admission::Stale;
        }
        let changed = lease.generation != generation;
        if changed && lease.active {
            return Admission::Stale;
        }
        *lease = LaneLease { generation, active: true };
        if ready {
            Admission::Ready
        } else if changed {
            Admission::Prepare
        } else {
            Admission::Preparing
        }
    }

    /// Install only if the request still belongs to the current source.
    pub(crate) fn finish_preparation(
        &mut self,
        generation: u64,
        prepared: PreparedLane,
    ) -> Result<(), PreparedLane> {
        let lease = self.leases(prepared.kind)[prepared.lane];
        if !lease.active || lease.generation != generation {
            return Err(prepared);
        }
        self.install_lane(prepared)
    }

    pub(crate) fn revoke(
        &mut self,
        kind: LaneKind,
        lane: usize,
        generation: u64,
    ) -> Option<ReleasedLane> {
        let lease = self.leases(kind).get_mut(lane)?;
        if !lease.active || lease.generation != generation {
            return None;
        }
        lease.active = false;
        self.release_lane(kind, lane)
    }

    /// Returns a rejected buffer to the caller for control-plane recycling.
    pub(crate) fn install_lane(&mut self, prepared: PreparedLane) -> Result<(), PreparedLane> {
        let lanes = match prepared.kind {
            LaneKind::Bed => &mut self.beds,
            LaneKind::Pcm => &mut self.pcm,
        };
        let lane = &mut lanes[prepared.lane];
        if lane.is_some() || prepared.samples.len() != BED_CHANNELS * self.frames {
            return Err(prepared);
        }
        *lane = Some(prepared.samples);
        Ok(())
    }

    /// The caller revokes the source lease before calling this at a block
    /// boundary. The returned mask identifies the ROAR objects to retire.
    /// Send the buffer to the controller for recycling or deallocation.
    pub(crate) fn release_lane(&mut self, kind: LaneKind, lane: usize) -> Option<ReleasedLane> {
        let lanes = match kind {
            LaneKind::Bed => &mut self.beds,
            LaneKind::Pcm => &mut self.pcm,
        };
        let samples = lanes.get_mut(lane)?.take()?;
        let mut retired_objects = 0;
        if kind == LaneKind::Bed {
            for (slot, entry) in self.objects.iter_mut().enumerate() {
                if let Some(key) = *entry && key.lane == lane {
                    *entry = None;
                    self.lookup[key.lane][key.source_slot] = 0;
                    retired_objects |= 1 << slot;
                }
            }
        }
        Some(ReleasedLane {
            prepared: PreparedLane { kind, lane, samples },
            retired_objects,
        })
    }

    pub(crate) fn lane(&self, kind: LaneKind, lane: usize) -> Option<&[f32]> {
        match kind {
            LaneKind::Bed => self.beds[lane].as_deref(),
            LaneKind::Pcm => self.pcm[lane].as_deref(),
        }
    }

    pub(crate) fn lane_mut(&mut self, kind: LaneKind, lane: usize) -> Option<&mut [f32]> {
        self.lane_and_mix_mut(kind, lane).map(|(input, _)| input)
    }

    pub(crate) fn lane_and_mix_mut(
        &mut self,
        kind: LaneKind,
        lane: usize,
    ) -> Option<(&mut [f32], &mut [f32])> {
        let lanes = match kind {
            LaneKind::Bed => &mut self.beds,
            LaneKind::Pcm => &mut self.pcm,
        };
        Some((lanes.get_mut(lane)?.as_deref_mut()?, &mut self.mixed_bed))
    }

    pub(crate) fn bed_mix_mut(&mut self) -> &mut [f32] {
        &mut self.mixed_bed
    }

    pub(crate) fn bed_mix(&self) -> &[f32] {
        &self.mixed_bed
    }

    pub(crate) fn dynamic_pcm_mut(&mut self, slot: usize) -> &mut [f32] {
        &mut self.object_samples[slot * self.frames..(slot + 1) * self.frames]
    }

    pub(crate) fn dynamic_pcm(&self, slot: usize) -> &[f32] {
        &self.object_samples[slot * self.frames..(slot + 1) * self.frames]
    }

    pub(crate) fn object(&self, slot: usize) -> Option<ObjectKey> {
        self.objects[slot]
    }

    /// Maps the complete active set for one block, preserving surviving slots.
    /// A failed validation leaves the previous map unchanged.
    pub(crate) fn slot(&self, key: ObjectKey) -> Option<usize> {
        let slot = usize::from(self.lookup[key.lane][key.source_slot].checked_sub(1)?);
        (self.objects[slot] == Some(key)).then_some(slot)
    }

    pub(crate) fn reconcile_objects(
        &mut self,
        keys: &[ObjectKey],
        assignments: &mut [usize; OBJECTS],
    ) -> Result<(), StorageError> {
        if keys.len() > OBJECTS {
            return Err(StorageError::ObjectBudget);
        }
        let mut surviving = 0u64;
        let mut seen = [[0u64; SOURCE_SLOTS.div_ceil(64)]; LANES];
        for key in keys {
            if key.lane >= LANES || key.source_slot >= SOURCE_SLOTS {
                return Err(StorageError::InvalidObject);
            }
            if self.beds[key.lane].is_none() {
                return Err(StorageError::BedUnavailable);
            }
            let word = &mut seen[key.lane][key.source_slot / 64];
            let mask = 1 << (key.source_slot % 64);
            if *word & mask != 0 {
                return Err(StorageError::DuplicateObject);
            }
            *word |= mask;
            let mapped = self.lookup[key.lane][key.source_slot];
            if mapped != 0 && self.objects[usize::from(mapped - 1)] == Some(*key) {
                surviving |= 1 << (mapped - 1);
            }
        }
        let mut free = !surviving;
        for (slot, entry) in self.objects.iter_mut().enumerate() {
            if free & (1 << slot) != 0 && let Some(old) = entry.take() {
                self.lookup[old.lane][old.source_slot] = 0;
            }
        }
        for (key, assignment) in keys.iter().zip(assignments.iter_mut()) {
            let mapped = &mut self.lookup[key.lane][key.source_slot];
            if *mapped == 0 {
                let slot = free.trailing_zeros() as usize;
                free &= !(1 << slot);
                self.objects[slot] = Some(*key);
                *mapped = (slot + 1) as u8;
            }
            *assignment = usize::from(*mapped - 1);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn installs_only_requested_late_lanes() {
        let mut storage = CompactStorage::new(480);
        for kind in [LaneKind::Bed, LaneKind::Pcm] {
            for lane in 0..LANES {
                assert_eq!(storage.lane_mut(kind, lane).is_some(), lane < INITIAL_LANES);
            }
            assert!(storage.install_lane(PreparedLane::new(kind, 4, 480).unwrap()).is_ok());
            assert_eq!(storage.lane_mut(kind, 4).unwrap().len(), 12 * 480);
            assert!(storage.lane_mut(kind, 5).is_none());
            assert!(storage.install_lane(PreparedLane::new(kind, 5, 64).unwrap()).is_err());
            assert!(PreparedLane::new(kind, 8, 480).is_err());
        }
        let (pcm, mix) = storage.lane_and_mix_mut(LaneKind::Pcm, 4).unwrap();
        pcm[0] = 2.;
        mix[0] = pcm[0];
        assert_eq!(storage.bed_mix()[0], 2.);
        storage.bed_mix_mut()[0] = 0.;
        storage.dynamic_pcm_mut(OBJECTS - 1)[0] = 3.;
        assert_eq!(storage.dynamic_pcm(OBJECTS - 1)[0], 3.);
    }

    #[test]
    fn released_bed_and_pcm_buffers_can_be_reused_without_leaking_samples() {
        let mut storage = CompactStorage::new(480);
        for kind in [LaneKind::Bed, LaneKind::Pcm] {
            let old = storage.lane_mut(kind, 0).unwrap();
            old[0] = 1.;
            let address = old.as_ptr();
            let mut released = storage.release_lane(kind, 0).unwrap();
            assert!(storage.lane_mut(kind, 0).is_none());
            assert!(storage.release_lane(kind, 0).is_none());
            assert_eq!(released.retired_objects, 0);
            released.prepared.recycle_as(kind, LANES - 1).unwrap();
            assert!(storage.install_lane(released.prepared).is_ok());
            let new = storage.lane_mut(kind, LANES - 1).unwrap();
            assert_eq!(new.as_ptr(), address);
            assert!(new.iter().all(|sample| *sample == 0.));
        }
    }

    #[test]
    fn releasing_a_bed_retires_only_its_objects_and_frees_their_slots() {
        let mut storage = CompactStorage::new(480);
        let mut assignments = [0; OBJECTS];
        let leaving = ObjectKey { lane: 0, source_slot: 159, generation: 1 };
        let staying = ObjectKey { lane: 1, source_slot: 159, generation: 1 };
        storage.reconcile_objects(&[leaving, staying], &mut assignments).unwrap();
        let free = assignments[0];
        let stable = assignments[1];
        let pcm = storage.release_lane(LaneKind::Pcm, 0).unwrap();
        assert_eq!(pcm.retired_objects, 0);
        assert_eq!(storage.object(free), Some(leaving));
        let mut bed = storage.release_lane(LaneKind::Bed, 0).unwrap();
        assert_eq!(bed.retired_objects, 1 << free);
        assert_eq!(storage.object(free), None);
        assert_eq!(storage.object(stable), Some(staying));
        assert_eq!(
            storage.reconcile_objects(&[staying, leaving], &mut assignments),
            Err(StorageError::BedUnavailable)
        );
        assert_eq!(storage.object(stable), Some(staying));
        bed.prepared.recycle_as(LaneKind::Bed, 7).unwrap();
        assert!(storage.install_lane(bed.prepared).is_ok());
        let entering = ObjectKey { lane: 7, generation: 2, ..leaving };
        storage.reconcile_objects(&[staying, entering], &mut assignments).unwrap();
        assert_eq!(assignments[..2], [stable, free]);
        assert_eq!(storage.object(free), Some(entering));
    }

    #[test]
    fn shared_budget_preserves_surviving_ids_through_full_pool_turnover() {
        let mut storage = CompactStorage::new(480);
        for lane in INITIAL_LANES..LANES {
            assert!(storage.install_lane(PreparedLane::new(LaneKind::Bed, lane, 480).unwrap()).is_ok());
        }
        let mut assignments = [0; OBJECTS];
        let mut keys: [ObjectKey; OBJECTS] = std::array::from_fn(|i| ObjectKey {
            lane: i % LANES,
            source_slot: SOURCE_SLOTS - 1 - i,
            generation: 1,
        });
        storage.reconcile_objects(&keys, &mut assignments).unwrap();
        let original_slots = assignments;
        keys[0].generation += 1;
        keys.reverse();
        storage.reconcile_objects(&keys, &mut assignments).unwrap();
        for (i, (assignment, original)) in assignments
            .iter()
            .zip(original_slots.iter().rev())
            .enumerate()
        {
            assert_eq!(assignment, original);
            assert_eq!(storage.object(*assignment), Some(keys[i]));
        }
        let mut invalid = keys.to_vec();
        invalid.push(keys[0]);
        assert_eq!(
            storage.reconcile_objects(&invalid, &mut assignments),
            Err(StorageError::ObjectBudget)
        );
        keys[31] = keys[0];
        assert_eq!(
            storage.reconcile_objects(&keys, &mut assignments),
            Err(StorageError::DuplicateObject)
        );
        assert_ne!(storage.object(assignments[0]), storage.object(assignments[31]));
    }
}

#[test]
fn expansion_skips_packets_and_rejects_a_late_preparation() {
    let mut storage = CompactStorage::new(480);
    for kind in [LaneKind::Bed, LaneKind::Pcm] {
        assert_eq!(storage.admit(kind, 0, 1), Admission::Ready);
        assert_eq!(storage.admit(kind, 4, 1), Admission::Prepare);
        assert_eq!(storage.admit(kind, 4, 1), Admission::Preparing);
        let old = PreparedLane::new(kind, 4, 480).unwrap();
        assert!(storage.revoke(kind, 4, 1).is_none());
        assert_eq!(storage.admit(kind, 4, 1), Admission::Stale);
        assert_eq!(storage.admit(kind, 4, 2), Admission::Prepare);
        let reused = storage.finish_preparation(1, old).err().unwrap();
        assert!(storage.finish_preparation(2, reused).is_ok());
        assert_eq!(storage.admit(kind, 4, 2), Admission::Ready);
        assert_eq!(storage.admit(kind, 0, 1), Admission::Ready);
        assert_eq!(storage.admit(kind, 8, 1), Admission::Stale);
    }
}
