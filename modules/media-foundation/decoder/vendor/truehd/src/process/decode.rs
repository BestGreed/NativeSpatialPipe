use crate::log_or_err;
use crate::process::{MAX_PRESENTATIONS, PresentationMap, PresentationType};
use crate::structs::access_unit::AccessUnit;
use crate::structs::block::Block;
use crate::structs::channel::ChannelLabel;
use crate::structs::oamd::ObjectAudioMetadataPayload;
use crate::utils::dither::fill_dither_31eb;
use crate::utils::errors::DecodeError;
use anyhow::{Result, anyhow, bail};
use log::{info, trace};
use std::collections::VecDeque;

/// Call `$function::<N>` with `N` the number of terms the matrix has, so that the
/// products of a sample are straight multiply-adds rather than a loop.
macro_rules! by_term_count {
    ($terms:expr, $function:ident $args:tt) => {
        by_term_count!(@arms $terms, $function, $args, 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15)
    };
    (@arms $terms:expr, $function:ident, $args:tt, $($n:literal)*) => {
        match $terms.count {
            $($n => $function::<$n> $args,)*
            _ => $function::<16> $args,
        }
    };
}

/// Decodes access units to PCM audio samples.
///
/// Converts parsed [`AccessUnit`] structures into 24-bit PCM audio data.
#[derive(Default)]
pub struct Decoder {
    state: DecoderState,
}

impl Decoder {
    /// Decodes an access unit to PCM audio samples.
    ///
    /// Returns a [`DecodedAccessUnit`] containing 24-bit PCM samples organized
    /// as `[sample_index][channel_index]` with up to 160 samples and 16 channels.
    ///
    /// This method now internally uses the multi-presentation decode path.
    pub fn decode_presentation(
        &mut self,
        access_unit: &AccessUnit,
        presentation: usize,
    ) -> Result<DecodedAccessUnit> {
        // Create presentation array with only the requested presentation
        let mut required_presentations = [false; MAX_PRESENTATIONS];
        required_presentations[presentation] = true;

        // Not through `decode_presentations`: only the one result is built, rather
        // than an array of four boxed and the result cloned out of it.
        self.state
            .decode_access_unit(access_unit, &required_presentations)?;

        // Read the flag and reset it, ahead of the result that reports it: built
        // last, the result is built where it is returned.
        let substream_info_changed = std::mem::take(&mut self.state.substream_info_changed);

        match self.state.effective_presentations.iter().position(|&p| p) {
            Some(i) => self.state.create_decoded_result(i, substream_info_changed),
            None => Err(anyhow!("Failed to get presentation {presentation}")),
        }
    }

    /// Decodes an access unit to PCM audio samples, into a result that is
    /// already there.
    ///
    /// As [`decode_presentation`](Self::decode_presentation), which builds a new
    /// [`DecodedAccessUnit`] for every access unit: 10 KiB of samples, most of
    /// them past the end of the access unit, and two allocations. Here the
    /// samples of the access unit are written over those `decoded` holds and
    /// its vectors are reused, so one `DecodedAccessUnit`, which
    /// [`Default`] gives, serves a whole stream.
    ///
    /// Only the rows the access unit decodes are written. Whatever `decoded`
    /// held past them stays, where `decode_presentation` returns zeros.
    pub fn decode_presentation_into(
        &mut self,
        access_unit: &AccessUnit,
        presentation: usize,
        decoded: &mut DecodedAccessUnit,
    ) -> Result<()> {
        let mut required_presentations = [false; MAX_PRESENTATIONS];
        required_presentations[presentation] = true;

        self.state
            .decode_access_unit(access_unit, &required_presentations)?;

        let substream_info_changed = std::mem::take(&mut self.state.substream_info_changed);

        match self.state.effective_presentations.iter().position(|&p| p) {
            Some(i) => {
                self.state
                    .fill_decoded_result(i, substream_info_changed, decoded);
                Ok(())
            }
            None => Err(anyhow!("Failed to get presentation {presentation}")),
        }
    }

    /// Decodes multiple presentations from an access unit.
    ///
    /// Takes a boolean array indicating which presentations are required
    /// and returns an array of optional decoded access units.
    pub fn decode_presentations(
        &mut self,
        access_unit: &AccessUnit,
        required_presentations: &[bool; MAX_PRESENTATIONS],
    ) -> Result<Box<[Option<DecodedAccessUnit>; MAX_PRESENTATIONS]>> {
        // Use the optimized shared decode method
        let result = self
            .state
            .decode_access_unit_presentations(access_unit, required_presentations)?;

        // Reset the flag after reading it
        self.state.substream_info_changed = false;

        Ok(Box::new(result))
    }

    /// Sets the failure level for validation errors.
    ///
    /// - `log::Level::Error`: Only fail on Error level messages (default)
    /// - `log::Level::Warn`: Fail on Warning level and above (strict mode)
    pub fn set_fail_level(&mut self, level: log::Level) {
        self.state.fail_level = level;
    }

    /// Resets stream state after a fatal decode failure.
    ///
    /// After a decode error, internal DSP state (filter memory, matrix
    /// coefficients, dither state) may be partially updated; continuing would
    /// produce corrupt audio. Calling this drops all stream state while
    /// preserving the fail level, so decoding can resume at the next access
    /// unit carrying a major sync.
    ///
    /// Must be called in lockstep with
    /// [`Parser::reset_for_next_major_sync`](crate::process::parse::Parser::reset_for_next_major_sync)
    /// at the same point in the frame sequence.
    pub fn reset_for_next_major_sync(&mut self) {
        self.state = DecoderState {
            fail_level: self.state.fail_level,
            ..Default::default()
        };
    }
}

/// The result of decoding an access unit to PCM audio.
///
/// Contains 24-bit signed integer samples in sample-major ordering
/// (`pcm_data[sample_index][channel_index]`) with associated metadata.
#[derive(Debug, Clone)]
pub struct DecodedAccessUnit {
    /// Sampling frequency in Hz.
    ///
    /// This is the sampling frequency used for the audio data.
    pub sampling_frequency: u32,

    /// Number of valid samples in this access unit.
    ///
    /// This indicates how many samples in the `pcm_data` array contain
    /// valid audio data. The remaining samples should be ignored.
    pub sample_length: usize,

    /// Channel count for the audio data.
    ///
    /// This is determined by the stream configuration and indicates how many
    /// channels are present in the audio data.
    pub channel_count: usize,

    /// PCM audio samples organized as `[sample_index][channel_index]`.
    ///
    /// Contains 24-bit signed integer samples with sample-major ordering.
    /// - Array dimensions: [160 samples][16 channels]
    /// - Valid data length: Determined by `sample_length`
    /// - Channel count: Determined by stream configuration
    pub pcm_data: [[i32; 16]; 160],

    /// Channel labels for the audio data.
    ///
    /// Contains labels for each channel in the audio data, providing
    /// descriptive names for each channel.
    pub channel_labels: Vec<ChannelLabel>,

    /// Optional object audio metadata payload.
    ///
    /// Contains spatial audio metadata when present in the stream.
    pub oamd: Vec<ObjectAudioMetadataPayload>,

    /// Indicates whether this access unit is a duplicate of the previous one.
    ///
    /// This is `true` when both the output timing and the decoded audio sample
    /// checksum match the previous access unit.
    /// Downstream applications may safely discard this frame.
    pub is_duplicate: bool,

    /// Indicates whether this access unit triggered a substream info change.
    ///
    /// This is `true` when substream_info or extended_substream_info changed,
    /// indicating that channel layout may have changed requiring new output files.
    pub substream_info_changed: bool,
}

impl Default for DecodedAccessUnit {
    /// No samples of no channels, as a result to decode into.
    fn default() -> Self {
        Self {
            sampling_frequency: 0,
            sample_length: 0,
            channel_count: 0,
            pcm_data: [[0; 16]; 160],
            channel_labels: vec![],
            oamd: vec![],
            is_duplicate: false,
            substream_info_changed: false,
        }
    }
}

#[derive(Debug, Clone)]
#[repr(C)]
pub struct DecoderSubstreamState {
    pub restart_sync_word: u16,
    pub output_timing: u16,
    pub max_bits: u8,
    /// Every output of the access unit folded into one word, sign removed, so the widest
    /// of them can be compared with `max_bits` once per substream.
    pub output_bits: u32,
    pub min_chan: usize,
    pub max_chan: usize,
    pub max_matrix_chan: usize,
    pub dither_shift: u32,
    pub dither_seed: u32,
    pub lossless_check_i32: i32,
    pub lossless_check_i32_prev_au: i32,
    pub lossless_check_i32_accum: i32,
    pub ch_assign: [usize; 16],

    pub block_size: usize,

    pub primitive_matrices: usize,
    pub matrix_ch: [u8; 16],
    pub frac_bits: [u8; 16],
    pub cf_shift_code: [i8; 16],
    pub dither_scale: [u8; 16],
    pub delta_precision: [u8; 16],
    pub delta_cf: [[i32; 16]; 16],
    pub m_coeff: [[i32; 16]; 16],

    pub output_shift: [i8; 16],
    pub quantiser_step_size: [u32; 16],

    pub order: [[usize; 16]; 2],
    pub coeff_q: [[i32; 16]; 2],
    pub coeff: [[[i32; 8]; 16]; 2],
    pub coeff_state: [[[i32; 8]; 16]; 2],

    pub dither_table: [i32; 256],
    pub decoded_sample_len: usize,

    pub rematrix_buffer: Box<[[i32; 16]; 160]>,
    pub output_buffer: Box<[[i32; 16]; 160]>,
    pub zero_samples: usize,
    pub channel_labels: Vec<ChannelLabel>,
}

impl Default for DecoderSubstreamState {
    fn default() -> Self {
        Self {
            restart_sync_word: 0,
            output_timing: 0,
            max_bits: 0,
            output_bits: 0,
            min_chan: 0,
            max_chan: 0,
            max_matrix_chan: 0,
            dither_shift: 0,
            dither_seed: 0,
            lossless_check_i32: 0,
            lossless_check_i32_prev_au: 0,
            lossless_check_i32_accum: 0,
            ch_assign: [0; 16],

            block_size: 8,

            primitive_matrices: 0,
            matrix_ch: [0; 16],
            frac_bits: [0; 16],
            cf_shift_code: [0; 16],
            dither_scale: [0; 16],
            delta_precision: [0; 16],
            delta_cf: [[0; 16]; 16],
            m_coeff: [[0; 16]; 16],

            output_shift: [0; 16],
            quantiser_step_size: [0; 16],

            order: [[0; 16]; 2],
            coeff_q: [[0; 16]; 2],
            coeff: [[[0; 8]; 16]; 2],
            coeff_state: [[[0; 8]; 16]; 2],

            dither_table: [0; 256],
            decoded_sample_len: 0,

            rematrix_buffer: Box::new([[0; 16]; 160]),
            output_buffer: Box::new([[0; 16]; 160]),
            zero_samples: 0,
            channel_labels: vec![],
        }
    }
}

/// Decode-time checks stay fail-fast; they report on samples rather than on bitstream
/// syntax, and a decode that continues past one produces wrong audio.
impl crate::utils::diagnostic::DiagnosticSink for DecoderState {
    fn fail_level(&self) -> log::Level {
        self.fail_level
    }
}

#[derive(Debug)]
#[repr(C)]
pub struct DecoderState {
    pub fail_level: log::Level,

    pub valid: bool,
    pub counter: usize,
    pub has_valid_branch: bool,
    pub has_invalid_branch: bool,
    pub has_duplicate_timing: bool,
    pub has_duplicate_sample: bool,

    pub sampling_frequency: u32,
    pub samples_per_au: usize,

    pub presentation_map: Option<PresentationMap>,
    pub presentation: usize,
    pub effective_presentations: [bool; MAX_PRESENTATIONS],

    pub channel_labels: Vec<ChannelLabel>,

    pub substreams: usize,
    pub substream_mask: u8,
    pub substream_info: u8,
    pub extended_substream_info: u8,

    pub substream_index: usize,
    pub substream_state: [DecoderSubstreamState; MAX_PRESENTATIONS],

    pub rematrix_buffer: [[i32; 16]; 160],
    pub output_buffer: [[i32; 16]; 160],
    pub zero_samples: usize,
    pub oamd: VecDeque<ObjectAudioMetadataPayload>,
    pub substream_info_changed: bool,

    /// Where the filters of a block keep their states while it is decoded. Nothing
    /// in it outlives the block.
    recorrelator_states: Box<LaneStateBuffer>,
}

impl Default for DecoderState {
    fn default() -> Self {
        Self {
            fail_level: log::Level::Error,
            valid: false,
            counter: 0,
            has_valid_branch: false,
            has_invalid_branch: false,
            has_duplicate_timing: false,
            has_duplicate_sample: false,
            sampling_frequency: 0,
            samples_per_au: 0,
            presentation_map: None,
            presentation: 0,
            effective_presentations: core::array::from_fn(|_| false),
            channel_labels: vec![],
            substreams: 0,
            substream_mask: 0,
            substream_info: 0,
            extended_substream_info: 0,
            substream_index: 0,
            substream_state: core::array::from_fn(|_| DecoderSubstreamState::default()),
            rematrix_buffer: [[0; 16]; 160],
            output_buffer: [[0; 16]; 160],
            zero_samples: 0,
            oamd: VecDeque::with_capacity(4),
            substream_info_changed: false,
            recorrelator_states: Box::new(LaneStateBuffer([[[0; MAX_LANES]; 2]; STATE_BASE + 8])),
        }
    }
}

impl DecoderState {
    pub fn substream_state_mut(&mut self) -> Result<&mut DecoderSubstreamState> {
        Ok(&mut self.substream_state[self.substream_index])
    }

    pub fn substream_state(&self) -> Result<&DecoderSubstreamState> {
        Ok(&self.substream_state[self.substream_index])
    }

    pub fn decode_access_unit_presentations(
        &mut self,
        access_unit: &AccessUnit,
        required_presentations: &[bool; MAX_PRESENTATIONS],
    ) -> Result<[Option<DecodedAccessUnit>; MAX_PRESENTATIONS]> {
        self.decode_access_unit(access_unit, required_presentations)?;

        let mut result: [Option<DecodedAccessUnit>; MAX_PRESENTATIONS] =
            core::array::from_fn(|_| None);

        for (i, decoded) in result.iter_mut().enumerate() {
            if self.effective_presentations[i] {
                *decoded = Some(self.create_decoded_result(i, self.substream_info_changed)?);
            }
        }

        Ok(result)
    }

    /// Decode an access unit into the substreams' output buffers, without building
    /// any result from them.
    fn decode_access_unit(
        &mut self,
        access_unit: &AccessUnit,
        required_presentations: &[bool; MAX_PRESENTATIONS],
    ) -> Result<()> {
        access_unit.update_decoder_state(self)?;

        if !self.valid {
            self.update_presentations(required_presentations)?;

            for (i, &required) in self.effective_presentations.iter().enumerate() {
                if required {
                    self.substream_state[i].channel_labels =
                        access_unit.get_channel_labels(i).unwrap_or_default();
                }
            }
        }

        self.has_duplicate_timing = false;
        self.has_duplicate_sample = false;
        self.oamd.clear();

        if let Some(extra_data) = &access_unit.extra_data
            && let Some(evo_frame) = &extra_data.evo_frame
        {
            for evo_payload in &evo_frame.evo_payloads {
                if evo_payload.evo_payload_id == 11 {
                    let smploffst =
                        evo_payload.evo_payload_config.smploffst.unwrap_or_default() as u64;
                    let mut oamd = ObjectAudioMetadataPayload::read(&evo_payload.evo_payload_byte)?;
                    oamd.evo_sample_offset = smploffst;
                    self.oamd.push_back(oamd);
                }
            }
        }

        for i in 0..=self.presentation {
            if (self.substream_mask >> i) & 1 == 0 {
                continue;
            }

            let substream_segment = &access_unit.substream_segment[i];

            // Handle zero samples for required presentations
            if let Some(terminator) = &substream_segment.terminator
                && terminator.zero_samples_indicated
            {
                self.substream_state[i].zero_samples = terminator.zero_samples as usize;
            }

            self.substream_index = i;
            let ss_state = &mut self.substream_state[self.substream_index];
            ss_state.decoded_sample_len = 0;

            for block in substream_segment.block.iter() {
                block.update_decoder_state(self)?;
                self.decode_multi(block)?;
            }
        }

        self.valid = true;
        self.counter += 1;

        Ok(())
    }

    fn update_presentations(
        &mut self,
        required_presentations: &[bool; MAX_PRESENTATIONS],
    ) -> Result<()> {
        let Some(presentation_map) = self.presentation_map else {
            bail!("Presentation map not initialized");
        };

        self.substream_mask =
            presentation_map.substream_mask_by_required_presentations(required_presentations);

        self.effective_presentations =
            presentation_map.effective_presentations(required_presentations)?;

        // Find the highest required presentation for fallback logic
        let highest_required = required_presentations
            .iter()
            .enumerate()
            .rev()
            .find_map(|(i, &required)| if required { Some(i) } else { None })
            .unwrap_or(3);

        // Set presentation field for compatibility (using highest required)
        match presentation_map.presentation_type_by_index(highest_required) {
            PresentationType::Invalid => {
                if !self.valid {
                    let Some(max_independent) = presentation_map.max_independent_presentation()
                    else {
                        bail!("No presentation is available");
                    };
                    info!(
                        "Presentation {highest_required} is not available, using presentation {max_independent}"
                    );
                    self.presentation = max_independent;
                }
            }
            PresentationType::CopyOf(copy_index) => {
                if !self.valid {
                    info!("Presentation {highest_required} is a copy of presentation {copy_index}")
                }
                self.presentation = copy_index;
            }
            _ => {
                self.presentation = highest_required;
            }
        };

        Ok(())
    }

    pub fn reset_decoder_substream_state(&mut self) {
        let ss_state = &mut self.substream_state[self.substream_index];
        *ss_state = DecoderSubstreamState {
            lossless_check_i32_prev_au: ss_state.lossless_check_i32_prev_au,
            // The labels are only assigned when decoding starts or the layout
            // changes; a restart header must not erase them.
            channel_labels: std::mem::take(&mut ss_state.channel_labels),
            ..Default::default()
        }
    }

    fn decode_multi(&mut self, block: &Block) -> Result<()> {
        let DecoderSubstreamState {
            restart_sync_word,
            min_chan,
            max_chan,
            max_matrix_chan,
            dither_shift,
            // TODO: max_lsbs
            ch_assign,

            block_size,

            primitive_matrices,
            matrix_ch,
            dither_scale,

            output_shift,
            quantiser_step_size,

            order,
            coeff_q,
            ..
        } = *self.substream_state()?;

        let samples_per_au = self.samples_per_au;
        let current_substream_index = self.substream_index;

        // A block holds the rows its own header announced. One read under another
        // block size than the decoder is at reads as zeros past its last row.
        let mut padded_data = None;
        let block_data = match block.block_data.get(..block_size) {
            Some(rows) => rows,
            None => &padded_data.insert(padded_rows(&block.block_data))[..block_size],
        };
        let mut padded_lsb = None;
        let bypassed_lsb = match block.bypassed_lsb.get(..block_size) {
            Some(rows) => rows,
            None => &padded_lsb.insert(padded_rows(&block.bypassed_lsb))[..block_size],
        };

        // recorrelation
        {
            let ss_state = &mut self.substream_state[current_substream_index];
            let decoded_sample_len = ss_state.decoded_sample_len;
            let coeff = &ss_state.coeff;
            let coeff_state = &mut ss_state.coeff_state;
            let rows = &mut ss_state.rematrix_buffer[decoded_sample_len..][..block_size];
            let wide = restart_sync_word == 0x31EC;

            let filter = |chi: usize| {
                // Zero past each filter's order (an order of zero leaves stale
                // coefficients behind), so both can run at their full fixed length.
                let mut fir = [0i32; 8];
                for (tap, fir) in fir.iter_mut().enumerate() {
                    if tap < order[0][chi] {
                        *fir = coeff[0][chi][tap];
                    }
                }
                let mut iir = [0i32; 4];
                for (tap, iir) in iir.iter_mut().enumerate() {
                    if tap < order[1][chi] {
                        *iir = coeff[1][chi][tap];
                    }
                }

                Recorrelator {
                    fir,
                    iir,
                    orders: (order[0][chi], order[1][chi]),
                    shift: coeff_q[0][chi],
                    mask: !((1 << quantiser_step_size[chi]) - 1),
                }
            };

            let kernel = Kernel::available();
            let lanes = kernel.lanes();
            let mut chi = min_chan;

            while chi <= max_chan {
                if lanes != 0 {
                    let channels = (max_chan + 1 - chi).min(lanes);
                    let mut filters = [Recorrelator::default(); MAX_LANES];
                    for (lane, lane_filter) in filters[..channels].iter_mut().enumerate() {
                        *lane_filter = filter(chi + lane);
                    }

                    if recorrelate_lanes(
                        kernel,
                        &filters[..channels],
                        coeff_state,
                        &mut self.recorrelator_states.0,
                        block_data,
                        rows,
                        chi,
                        wide,
                    ) {
                        chi += channels;
                        continue;
                    }
                }

                // Where the lanes are not to be had, or to find which channel of
                // them left its range, and at which sample.
                let [fir, iir] = &mut coeff_state.each_mut().map(|state| &mut state[chi]);
                recorrelate(&filter(chi), [fir, iir], block_data, rows, chi, wide)?;
                chi += 1;
            }
        }

        // lossless matrix
        if self.effective_presentations[current_substream_index] {
            let decoded_sample_len =
                self.substream_state[current_substream_index].decoded_sample_len;
            let block_rows = decoded_sample_len..decoded_sample_len + block_size;

            if let Some(presentation_map) = self.presentation_map {
                let substream_mask =
                    presentation_map.substream_mask_by_index(current_substream_index);

                for i in 0..=current_substream_index {
                    if (substream_mask >> i) & 1 == 0 {
                        continue;
                    }

                    let from = &self.substream_state[i];
                    let from_rows = &from.rematrix_buffer[block_rows.clone()];
                    let to_rows = &mut self.rematrix_buffer[block_rows.clone()];

                    // A channel at a time: a substream brings a few channels of the
                    // row, and a copy of that run is a call for a handful of bytes.
                    for chi in from.min_chan..=from.max_chan {
                        for (to, from) in to_rows.iter_mut().zip(from_rows) {
                            to[chi] = from[chi];
                        }
                    }
                }
            }

            let ss_state = &mut self.substream_state[current_substream_index];
            let rows = &mut self.rematrix_buffer[block_rows.clone()];
            let matrix_terms = if restart_sync_word == 0x31EA {
                max_matrix_chan + 3
            } else {
                max_matrix_chan + 1
            };

            // A matrix at a time over the whole block, not a sample at a time through
            // every matrix: the samples are independent of each other, so one matrix's
            // products overlap from one sample to the next, where the matrices of one
            // sample each wait on the channel the one before wrote. It also settles
            // once per block which products a matrix has at all.
            match restart_sync_word {
                0x31EA => {
                    let dither_seed = &mut ss_state.dither_seed;

                    for row in rows.iter_mut() {
                        let dither_seed_shr7 = *dither_seed >> 7;

                        row[max_matrix_chan + 1] =
                            (((*dither_seed >> 15) as i8) << dither_shift) as i32;
                        row[max_matrix_chan + 2] =
                            ((dither_seed_shr7 as i8) << dither_shift) as i32;

                        *dither_seed =
                            (dither_seed_shr7 ^ (dither_seed_shr7 << 5) ^ (*dither_seed << 16))
                                & 0x7FFFFF;
                    }

                    #[allow(clippy::needless_range_loop)]
                    for pmi in 0..primitive_matrices {
                        let dest = matrix_ch[pmi] as usize;
                        let mask = !((1 << quantiser_step_size[dest]) - 1);
                        let terms = MatrixTerms::new(&ss_state.m_coeff[pmi][..matrix_terms], &[]);

                        apply_steady_matrix(&terms, rows, bypassed_lsb, pmi, dest, mask, None);
                    }
                }
                0x31EB => {
                    let dither_index_mask = samples_per_au.next_power_of_two() - 1;

                    if decoded_sample_len == 0 {
                        fill_dither_31eb(
                            &mut ss_state.dither_table[..=dither_index_mask],
                            &mut ss_state.dither_seed,
                        );
                    }

                    let dither_table = &ss_state.dither_table;

                    for pmi in 0..primitive_matrices {
                        let dest = matrix_ch[pmi] as usize;
                        let mask = !((1 << quantiser_step_size[dest]) - 1);
                        let terms = MatrixTerms::new(&ss_state.m_coeff[pmi][..matrix_terms], &[]);
                        let dither = Dither::new(
                            dither_table,
                            dither_index_mask,
                            primitive_matrices - pmi,
                            decoded_sample_len,
                            dither_scale[pmi],
                        );

                        apply_steady_matrix(&terms, rows, bypassed_lsb, pmi, dest, mask, dither);
                    }
                }
                0x31EC => {
                    let dither_index_mask = samples_per_au.next_power_of_two() - 1;

                    if decoded_sample_len == 0 {
                        fill_dither_31eb(
                            &mut ss_state.dither_table[..=dither_index_mask],
                            &mut ss_state.dither_seed,
                        );
                    }

                    let dither_table = &ss_state.dither_table;
                    let samples_per_au_recip = (1 << 16) / samples_per_au as i64;

                    for pmi in 0..primitive_matrices {
                        let dest = matrix_ch[pmi] as usize;
                        let mask = !((1 << quantiser_step_size[dest]) - 1);
                        let terms = MatrixTerms::new(
                            &ss_state.m_coeff[pmi][..matrix_terms],
                            &ss_state.delta_cf[pmi][..matrix_terms],
                        );
                        let dither = Dither::new(
                            dither_table,
                            dither_index_mask,
                            primitive_matrices - pmi,
                            decoded_sample_len,
                            dither_scale[pmi],
                        );

                        // Most matrices hold still over the access unit: without a
                        // delta there is nothing to interpolate, and no second
                        // product to take of every sample.
                        if !terms.has_delta() {
                            apply_steady_matrix(
                                &terms,
                                rows,
                                bypassed_lsb,
                                pmi,
                                dest,
                                mask,
                                dither,
                            );
                            continue;
                        }

                        by_term_count!(
                            terms,
                            apply_matrix(
                                &terms,
                                rows,
                                bypassed_lsb,
                                pmi,
                                dest,
                                mask,
                                |blki, acc_delta| {
                                    let blki_abs = (blki + decoded_sample_len) as i64;

                                    dither.map_or(0, |dither| dither.at(blki))
                                        + (acc_delta >> 18) * blki_abs * (samples_per_au_recip << 2)
                                }
                            )
                        );
                    }

                    if decoded_sample_len + block_size == samples_per_au {
                        for pmi in 0..primitive_matrices {
                            let m_coeff = &mut ss_state.m_coeff[pmi];
                            let delta_cf = &ss_state.delta_cf[pmi];
                            for chi in 0..=max_matrix_chan {
                                m_coeff[chi] += delta_cf[chi];
                            }
                        }
                    }
                }
                _ => {}
            }

            // remap
            {
                let output_rows = &mut ss_state.output_buffer[block_rows];

                if decoded_sample_len == 0 {
                    ss_state.lossless_check_i32 = 0;
                    ss_state.output_bits = 0;
                }

                let mut lossless_check_data = 0;
                let mut output_bits = 0u32;

                // A channel at a time, so that its shift and its place are settled
                // once. The rows need no clearing: the channels written are the same
                // from one restart header to the next, which starts from zeroed rows.
                for chi in 0..max_matrix_chan + 1 {
                    let ch_assign = ch_assign[chi];
                    let output_shift = output_shift[chi];

                    for (row, output_row) in rows.iter().zip(output_rows.iter_mut()) {
                        let mut output = row[chi];

                        if output_shift < 0 {
                            output >>= -output_shift;
                        } else {
                            output <<= output_shift;
                        }

                        output_row[ch_assign] = output;

                        lossless_check_data ^= (output & 0xFFFFFF) << (chi & 7);
                        output_bits |= (output ^ (output >> 31)) as u32;
                    }
                }

                ss_state.output_bits |= output_bits;
                ss_state.lossless_check_i32 ^= lossless_check_data;
                ss_state.lossless_check_i32_accum ^= lossless_check_data;

                if decoded_sample_len + block_size == samples_per_au {
                    trace!(
                        "AU {}: lossless_check_i32: {:08X}, lossless_check_i32_prev_au {:08X}",
                        self.counter,
                        ss_state.lossless_check_i32,
                        ss_state.lossless_check_i32_prev_au
                    );

                    if self.has_duplicate_timing
                        && ss_state.lossless_check_i32 == ss_state.lossless_check_i32_prev_au
                    {
                        self.has_duplicate_sample = true;
                        info!(
                            "AU {}: duplicate samples at branch, should be discarded",
                            self.counter
                        );
                    }

                    ss_state.lossless_check_i32_prev_au = ss_state.lossless_check_i32;
                }
            }
        }

        // The restart header states how many bits the substream's outputs use, sign apart,
        // and the widest of them is only known once the access unit is complete. It is a
        // property of the decoded samples, so it cannot be checked while parsing, and a
        // per-block check would report the same violation up to four times over.
        let ss_state = self.substream_state()?;

        if self.effective_presentations[current_substream_index]
            && ss_state.decoded_sample_len + block_size == samples_per_au
            && ss_state.output_bits >> ss_state.max_bits != 0
        {
            let max_bits = ss_state.max_bits;

            log_or_err!(
                self,
                log::Level::Warn,
                anyhow!(DecodeError::OutputsExceedMaxBits {
                    substream: current_substream_index,
                    max_bits,
                })
            );
        }

        self.substream_state_mut()?.decoded_sample_len += block_size;

        Ok(())
    }

    fn create_decoded_result(
        &self,
        presentation_idx: usize,
        substream_info_changed: bool,
    ) -> Result<DecodedAccessUnit> {
        let decoded = DecodedAccessUnit {
            channel_labels: self.substream_state[presentation_idx]
                .channel_labels
                .clone(),
            sampling_frequency: self.sampling_frequency,
            sample_length: self.samples_per_au
                - self.substream_state[presentation_idx].zero_samples,
            channel_count: self.substream_state[presentation_idx].max_matrix_chan + 1,
            pcm_data: *self.substream_state[presentation_idx].output_buffer,
            oamd: if presentation_idx == 3 && self.effective_presentations[3] {
                self.oamd.iter().cloned().collect::<Vec<_>>()
            } else {
                vec![]
            },
            is_duplicate: self.has_duplicate_timing && self.has_duplicate_sample,
            substream_info_changed,
        };

        Ok(decoded)
    }

    /// [`create_decoded_result`](Self::create_decoded_result) over a result that
    /// is already there, writing only the rows the access unit decoded.
    fn fill_decoded_result(
        &self,
        presentation_idx: usize,
        substream_info_changed: bool,
        decoded: &mut DecodedAccessUnit,
    ) {
        let ss_state = &self.substream_state[presentation_idx];
        let rows = self.samples_per_au.min(decoded.pcm_data.len());

        decoded.channel_labels.clone_from(&ss_state.channel_labels);
        decoded.sampling_frequency = self.sampling_frequency;
        decoded.sample_length = self.samples_per_au - ss_state.zero_samples;
        decoded.channel_count = ss_state.max_matrix_chan + 1;
        decoded.pcm_data[..rows].copy_from_slice(&ss_state.output_buffer[..rows]);
        decoded.oamd.clear();
        if presentation_idx == 3 && self.effective_presentations[3] {
            decoded.oamd.extend(self.oamd.iter().cloned());
        }
        decoded.is_duplicate = self.has_duplicate_timing && self.has_duplicate_sample;
        decoded.substream_info_changed = substream_info_changed;
    }
}

/// One channel's prediction filters, as a block runs them.
#[derive(Clone, Copy, Default)]
struct Recorrelator {
    fir: [i32; 8],
    iir: [i32; 4],
    /// How many coefficients each filter has. Those past them are zero.
    orders: (usize, usize),
    shift: i32,
    mask: i64,
}

/// The longest block, which is how far back a filter's states are kept while one
/// is decoded: newest first, the block's own below the eight it started with.
const STATE_BASE: usize = 160;

/// Run one channel's filters over a block, from the residuals in `block_data` to
/// the samples in `rows`. `history` holds the last eight states of each filter,
/// newest first, and is brought up to date.
fn recorrelate(
    filter: &Recorrelator,
    history: [&mut [i32; 8]; 2],
    block_data: &[[i32; 16]],
    rows: &mut [[i32; 16]],
    chi: usize,
    wide: bool,
) -> Result<()> {
    // One channel alone is held to its multiplications, one after the other,
    // and most channels have fewer than the twelve of two full filters: the
    // second filter is seldom there at all. An order past a filter's length
    // runs it whole, zeros and all.
    macro_rules! at_orders {
        ($fir:literal) => {
            match filter.orders.1 {
                0 => recorrelate_at::<$fir, 0>(filter, history, block_data, rows, chi, wide),
                1 => recorrelate_at::<$fir, 1>(filter, history, block_data, rows, chi, wide),
                2 => recorrelate_at::<$fir, 2>(filter, history, block_data, rows, chi, wide),
                3 => recorrelate_at::<$fir, 3>(filter, history, block_data, rows, chi, wide),
                _ => recorrelate_at::<$fir, 4>(filter, history, block_data, rows, chi, wide),
            }
        };
    }

    match filter.orders.0 {
        0 => at_orders!(0),
        1 => at_orders!(1),
        2 => at_orders!(2),
        3 => at_orders!(3),
        4 => at_orders!(4),
        5 => at_orders!(5),
        6 => at_orders!(6),
        7 => at_orders!(7),
        _ => at_orders!(8),
    }
}

/// [`recorrelate`] with the first `FIR` and `IIR` coefficients of the filters,
/// which is all of those that are not zero.
fn recorrelate_at<const FIR: usize, const IIR: usize>(
    filter: &Recorrelator,
    history: [&mut [i32; 8]; 2],
    block_data: &[[i32; 16]],
    rows: &mut [[i32; 16]],
    chi: usize,
    wide: bool,
) -> Result<()> {
    let fir: &[i32; FIR] = filter.fir[..FIR].try_into().unwrap();
    let iir: &[i32; IIR] = filter.iir[..IIR].try_into().unwrap();
    let (max_val, min_val) = if wide {
        (1i64 << 31, -(1i64 << 31))
    } else {
        (1i64 << 23, -(1i64 << 23))
    };
    let block_size = block_data.len().min(STATE_BASE);
    let mut fir_states = [0; STATE_BASE + 8];
    let mut iir_states = [0; STATE_BASE + 8];

    fir_states[STATE_BASE..].copy_from_slice(history[0]);
    iir_states[STATE_BASE..].copy_from_slice(history[1]);

    for blki in 0..block_size {
        let audio_data = block_data[blki][chi] as i64;
        let state_base = STATE_BASE - blki;

        let acc =
            dot_fixed(fir, &fir_states[state_base..]) + dot_fixed(iir, &iir_states[state_base..]);

        let pred = acc >> filter.shift;
        let fir_state = audio_data + (pred & filter.mask);
        let iir_state = fir_state - pred;

        if fir_state >= max_val {
            bail!(DecodeError::RecorrelatorPositiveSaturation(fir_state));
        } else if fir_state < min_val {
            bail!(DecodeError::RecorrelatorNegativeSaturation(fir_state));
        }

        if !(min_val..max_val).contains(&iir_state) {
            if wide {
                bail!(DecodeError::FilterBInputTooWide32(iir_state));
            } else {
                bail!(DecodeError::FilterBInputTooWide24(iir_state));
            }
        }

        fir_states[state_base - 1] = fir_state as i32;
        iir_states[state_base - 1] = iir_state as i32;

        rows[blki][chi] = fir_state as i32;
    }

    history[0].copy_from_slice(&fir_states[STATE_BASE - block_size..][..8]);
    history[1].copy_from_slice(&iir_states[STATE_BASE - block_size..][..8]);

    Ok(())
}

/// The most channels [`recorrelate_lanes`] runs side by side, which is how many
/// the widest kernel takes.
const MAX_LANES: usize = 8;

/// The states of both filters of every lane at one sample. A kernel with fewer
/// lanes than [`MAX_LANES`] uses the first of them.
type LaneStates = [[i64; MAX_LANES]; 2];

/// The states of a block's filters, newest first, below the eight a block
/// started with. Aligned for the widest kernel's loads.
#[repr(C, align(64))]
#[derive(Debug)]
struct LaneStateBuffer([LaneStates; STATE_BASE + 8]);

/// The widest of the side-by-side kernels this processor runs.
///
/// `--cfg truehd_force_avx2` holds an AVX-512 processor to the AVX2 kernels,
/// and `--cfg truehd_force_scalar` any processor to a channel and a product at
/// a time, so that each path can be checked against the others on one machine.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
// The vector kernels are not built where the processor is not asked for them.
#[cfg_attr(
    any(not(target_arch = "x86_64"), truehd_force_scalar),
    allow(dead_code)
)]
enum Kernel {
    /// A channel at a time, and a product at a time.
    Scalar,
    /// AVX2: four channels or eight products at a time.
    Avx2,
    /// AVX-512: eight channels at a time.
    Avx512,
}

impl Kernel {
    fn available() -> Self {
        #[cfg(all(target_arch = "x86_64", not(truehd_force_scalar)))]
        {
            if !cfg!(truehd_force_avx2)
                && std::arch::is_x86_feature_detected!("avx512f")
                && std::arch::is_x86_feature_detected!("avx512vl")
                && std::arch::is_x86_feature_detected!("avx512dq")
            {
                return Self::Avx512;
            }
            if std::arch::is_x86_feature_detected!("avx2") {
                return Self::Avx2;
            }
        }

        Self::Scalar
    }

    /// How many channels [`recorrelate_lanes`] runs side by side with this kernel:
    /// none where it cannot.
    fn lanes(self) -> usize {
        match self {
            Self::Scalar => 0,
            Self::Avx2 => 4,
            Self::Avx512 => 8,
        }
    }
}

/// [`recorrelate`] for the channels `chi..chi + filters.len()`, at most as many
/// as the kernel has lanes, side by side. `false` where the processor cannot,
/// and where a state of any of them left its range: the rows are then not to be
/// used, and the caller runs the channels one at a time, which reports the first.
///
/// One channel alone is held to twelve multiplications a sample, one after the
/// other. Side by side, each multiplication is one instruction for all the lanes.
#[allow(unused_variables, clippy::too_many_arguments)]
fn recorrelate_lanes(
    kernel: Kernel,
    filters: &[Recorrelator],
    coeff_state: &mut [[[i32; 8]; 16]; 2],
    states: &mut [LaneStates; STATE_BASE + 8],
    block_data: &[[i32; 16]],
    rows: &mut [[i32; 16]],
    chi: usize,
    wide: bool,
) -> bool {
    match kernel {
        // SAFETY: the kernel is the one `Kernel::available` found the processor
        // has, which is all the function requires.
        #[cfg(target_arch = "x86_64")]
        Kernel::Avx512 => unsafe {
            avx512::recorrelate_lanes(filters, coeff_state, states, block_data, rows, chi, wide)
        },
        // SAFETY: as above.
        #[cfg(target_arch = "x86_64")]
        Kernel::Avx2 => unsafe {
            avx2::recorrelate_lanes(filters, coeff_state, states, block_data, rows, chi, wide)
        },
        #[cfg(not(target_arch = "x86_64"))]
        Kernel::Avx512 | Kernel::Avx2 => false,
        Kernel::Scalar => false,
    }
}

#[cfg(target_arch = "x86_64")]
mod avx2 {
    use super::{LaneStates, MAX_LANES, Recorrelator, STATE_BASE};
    use std::arch::x86_64::*;

    /// How many channels this kernel runs side by side.
    const LANES: usize = 4;

    #[inline(always)]
    fn load(lanes: &[i64; MAX_LANES]) -> __m256i {
        // SAFETY: the first four of eight `i64` are 32 readable bytes.
        unsafe { _mm256_loadu_si256(lanes.as_ptr().cast()) }
    }

    #[inline(always)]
    fn store(lanes: &mut [i64; MAX_LANES], value: __m256i) {
        // SAFETY: the first four of eight `i64` are 32 writable bytes.
        unsafe { _mm256_storeu_si256(lanes.as_mut_ptr().cast(), value) }
    }

    #[inline(always)]
    fn load_half(lanes: &[i32; LANES]) -> __m128i {
        // SAFETY: an array of four `i32` is 16 readable bytes.
        unsafe { _mm_loadu_si128(lanes.as_ptr().cast()) }
    }

    #[inline(always)]
    fn store_half(lanes: &mut [i32; LANES], value: __m128i) {
        // SAFETY: an array of four `i32` is 16 writable bytes.
        unsafe { _mm_storeu_si128(lanes.as_mut_ptr().cast(), value) }
    }

    #[inline(always)]
    fn load_eight(channels: &[i32]) -> __m256i {
        let channels: &[i32; 8] = channels.try_into().expect("eight channels");

        // SAFETY: an array of eight `i32` is 32 readable bytes.
        unsafe { _mm256_loadu_si256(channels.as_ptr().cast()) }
    }

    /// The sum of the products of eight channels with their coefficients, in
    /// four parts. `odd_coeff` is `coeff` with each two neighbours swapped: a
    /// multiplication takes every other value, so the odd ones are swapped
    /// into the places of the even ones, with a shuffle rather than a 64-bit
    /// shift, which the compiler would take for the widening of a product it
    /// then has to make up from several.
    #[inline]
    #[target_feature(enable = "avx2")]
    fn products(coeff: __m256i, odd_coeff: __m256i, channels: __m256i) -> __m256i {
        _mm256_add_epi64(
            _mm256_mul_epi32(coeff, channels),
            _mm256_mul_epi32(odd_coeff, _mm256_shuffle_epi32::<0xB1>(channels)),
        )
    }

    /// See [`super::apply_matrix`], for a matrix without deltas. `coeff` has a
    /// matrix's coefficients in the places of their channels, and a row's
    /// products are taken eight channels at a time: all sixteen with `UPPER`,
    /// the lower eight without, for a matrix that reads none of the others.
    /// `extra` is given the row's index in the block.
    #[target_feature(enable = "avx2")]
    pub(super) fn apply_matrix<const UPPER: bool>(
        coeff: &[i32; 16],
        rows: &mut [[i32; 16]],
        bypassed_lsb: &[[u8; 16]],
        pmi: usize,
        dest: usize,
        mask: i32,
        extra: impl Fn(usize) -> i64,
    ) {
        let lower_coeff = load_eight(&coeff[..8]);
        let lower_odd_coeff = _mm256_shuffle_epi32::<0xB1>(lower_coeff);
        let upper_coeff = load_eight(&coeff[8..]);
        let upper_odd_coeff = _mm256_shuffle_epi32::<0xB1>(upper_coeff);

        for (blki, (row, bypassed_lsb)) in rows.iter_mut().zip(bypassed_lsb).enumerate() {
            let mut parts = products(lower_coeff, lower_odd_coeff, load_eight(&row[..8]));

            if UPPER {
                parts = _mm256_add_epi64(
                    parts,
                    products(upper_coeff, upper_odd_coeff, load_eight(&row[8..])),
                );
            }

            let halves = _mm_add_epi64(
                _mm256_castsi256_si128(parts),
                _mm256_extracti128_si256::<1>(parts),
            );
            let acc = _mm_cvtsi128_si64(_mm_add_epi64(halves, _mm_unpackhi_epi64(halves, halves)))
                + extra(blki);

            row[dest] = (((acc >> 18) as i32) & mask) + bypassed_lsb[pmi] as i32;
        }
    }

    /// See [`super::recorrelate_lanes`]. Lane `l` is channel `first + l`, where
    /// `first` is `chi` unless that would run past the sixteenth channel. A lane
    /// outside `chi..chi + filters.len()` has zero coefficients and takes zero
    /// residuals, so its states stay zero, and it leaves its channel alone.
    #[target_feature(enable = "avx2")]
    pub(super) fn recorrelate_lanes(
        filters: &[Recorrelator],
        coeff_state: &mut [[[i32; 8]; 16]; 2],
        states: &mut [LaneStates; STATE_BASE + 8],
        block_data: &[[i32; 16]],
        rows: &mut [[i32; 16]],
        chi: usize,
        wide: bool,
    ) -> bool {
        let block_size = block_data.len().min(rows.len()).min(STATE_BASE);
        let first = chi.min(16 - LANES);
        let lane_of = |channel: usize| channel - first;

        if chi + filters.len() > first + LANES {
            return false;
        }

        let mut fir = [[0i64; MAX_LANES]; 8];
        let mut iir = [[0i64; MAX_LANES]; 4];
        let mut shift = [0i64; MAX_LANES];
        let mut mask = [0i64; MAX_LANES];
        let mut live = [0i32; LANES];

        for (channel, filter) in (chi..).zip(filters) {
            let lane = lane_of(channel);

            for (tap, coeff) in filter.fir.iter().enumerate() {
                fir[tap][lane] = *coeff as i64;
            }
            for (tap, coeff) in filter.iir.iter().enumerate() {
                iir[tap][lane] = *coeff as i64;
            }
            // As a scalar shift takes its count.
            shift[lane] = (filter.shift & 63) as i64;
            mask[lane] = filter.mask;
            live[lane] = -1;
        }

        for (tap, states) in states[STATE_BASE..].iter_mut().enumerate() {
            *states = [[0; MAX_LANES]; 2];

            for channel in chi..chi + filters.len() {
                states[0][lane_of(channel)] = coeff_state[0][channel][tap] as i64;
                states[1][lane_of(channel)] = coeff_state[1][channel][tap] as i64;
            }
        }

        let mut fir_lanes = [_mm256_setzero_si256(); 8];
        for (lanes, coeff) in fir_lanes.iter_mut().zip(&fir) {
            *lanes = load(coeff);
        }
        let mut iir_lanes = [_mm256_setzero_si256(); 4];
        for (lanes, coeff) in iir_lanes.iter_mut().zip(&iir) {
            *lanes = load(coeff);
        }
        let (fir, iir) = (fir_lanes, iir_lanes);
        // The count is below 64, which the shift has no way to know.
        let shift = _mm256_and_si256(load(&shift), _mm256_set1_epi64x(63));
        let mask = load(&mask);
        let live_half = load_half(&live);
        let live = _mm256_cvtepi32_epi64(live_half);
        // An arithmetic shift is a logical one of the value moved up by 2^63,
        // moved back down by 2^63 shifted as much. The sum of the older
        // products, which is ready early, carries the move up.
        let sign = _mm256_set1_epi64x(i64::MIN);
        let shifted_sign = _mm256_srlv_epi64(sign, shift);
        // A state in range, moved up by half the range, has no bit at or above
        // the width of the range.
        let (bias, bits) = if wide {
            (1i64 << 31, 32)
        } else {
            (1i64 << 23, 24)
        };
        let bias = _mm256_set1_epi64x(bias);
        let bits = _mm_cvtsi32_si128(bits);
        // The low halves of the four lanes, side by side.
        let low_halves = _mm256_setr_epi32(0, 2, 4, 6, 0, 2, 4, 6);

        // The products of every state but the newest, oldest first: each sample
        // has them ready for the next one, so that all that waits on a sample
        // is its own two products.
        let older_products = |window: &[LaneStates]| -> __m256i {
            let window: &[LaneStates; 8] = window[..8].try_into().expect("eight states");
            let mut acc = sign;

            for tap in (1..8).rev() {
                acc = _mm256_add_epi64(acc, _mm256_mul_epi32(fir[tap], load(&window[tap][0])));
            }
            for tap in (1..4).rev() {
                acc = _mm256_add_epi64(acc, _mm256_mul_epi32(iir[tap], load(&window[tap][1])));
            }

            acc
        };

        let mut older = older_products(&states[STATE_BASE..]);
        let mut newest_fir = load(&states[STATE_BASE][0]);
        let mut newest_iir = load(&states[STATE_BASE][1]);
        let mut out_of_range = _mm256_setzero_si256();

        for blki in 0..block_size {
            let state_base = STATE_BASE - blki;
            let data: &[i32; LANES] = block_data[blki][first..first + LANES]
                .try_into()
                .expect("four channels");
            let row: &mut [i32; LANES] = (&mut rows[blki][first..first + LANES])
                .try_into()
                .expect("four channels");

            let acc = _mm256_add_epi64(
                _mm256_add_epi64(older, _mm256_mul_epi32(fir[0], newest_fir)),
                _mm256_mul_epi32(iir[0], newest_iir),
            );
            let pred = _mm256_sub_epi64(_mm256_srlv_epi64(acc, shift), shifted_sign);
            let residual = _mm256_and_si256(_mm256_cvtepi32_epi64(load_half(data)), live);
            let fir_state = _mm256_add_epi64(residual, _mm256_and_si256(pred, mask));
            let iir_state = _mm256_sub_epi64(fir_state, pred);

            out_of_range = _mm256_or_si256(
                out_of_range,
                _mm256_srl_epi64(
                    _mm256_or_si256(
                        _mm256_add_epi64(fir_state, bias),
                        _mm256_add_epi64(iir_state, bias),
                    ),
                    bits,
                ),
            );

            store(&mut states[state_base - 1][0], fir_state);
            store(&mut states[state_base - 1][1], iir_state);

            let samples =
                _mm256_castsi256_si128(_mm256_permutevar8x32_epi32(fir_state, low_halves));
            store_half(row, _mm_blendv_epi8(load_half(row), samples, live_half));

            older = older_products(&states[state_base - 1..]);
            newest_fir = fir_state;
            newest_iir = iir_state;
        }

        if _mm256_testz_si256(out_of_range, out_of_range) == 0 {
            return false;
        }

        for (tap, states) in states[STATE_BASE - block_size..][..8].iter().enumerate() {
            for channel in chi..chi + filters.len() {
                coeff_state[0][channel][tap] = states[0][lane_of(channel)] as i32;
                coeff_state[1][channel][tap] = states[1][lane_of(channel)] as i32;
            }
        }

        true
    }
}

#[cfg(target_arch = "x86_64")]
mod avx512 {
    use super::{LaneStates, MAX_LANES, Recorrelator, STATE_BASE};
    use std::arch::x86_64::*;

    /// How many channels this kernel runs side by side.
    const LANES: usize = MAX_LANES;

    #[inline(always)]
    fn load(lanes: &[i64; LANES]) -> __m512i {
        // SAFETY: an array of eight `i64` is 64 readable bytes.
        unsafe { _mm512_loadu_si512(lanes.as_ptr().cast()) }
    }

    #[inline(always)]
    fn store(lanes: &mut [i64; LANES], value: __m512i) {
        // SAFETY: an array of eight `i64` is 64 writable bytes.
        unsafe { _mm512_storeu_si512(lanes.as_mut_ptr().cast(), value) }
    }

    #[inline(always)]
    fn load_eight(values: &[i32; 8]) -> __m256i {
        // SAFETY: an array of eight `i32` is 32 readable bytes.
        unsafe { _mm256_loadu_si256(values.as_ptr().cast()) }
    }

    #[inline(always)]
    fn store_eight(values: &mut [i32; 8], value: __m256i) {
        // SAFETY: an array of eight `i32` is 32 writable bytes.
        unsafe { _mm256_storeu_si256(values.as_mut_ptr().cast(), value) }
    }

    /// Eight rows of eight 32-bit values as their eight columns: the taps of
    /// eight channels as the channels of eight taps, and back again. The
    /// states and coefficients of a block come a channel at a time, and the
    /// lanes take them a tap at a time; through registers, where a vector
    /// loaded over values just stored one at a time waits for the stores.
    #[inline]
    #[target_feature(enable = "avx2,avx512f,avx512vl,avx512dq")]
    fn transpose(rows: [__m256i; 8]) -> [__m256i; 8] {
        let pair = |a, b| _mm512_inserti64x4::<1>(_mm512_castsi256_si512(a), b);
        let [r0, r1, r2, r3, r4, r5, r6, r7] = rows;
        let (p01, p23) = (pair(r0, r1), pair(r2, r3));
        let (p45, p67) = (pair(r4, r5), pair(r6, r7));

        // Four rows' first four values, a column at a time, then their last four.
        let lo = _mm512_setr_epi32(0, 8, 16, 24, 1, 9, 17, 25, 2, 10, 18, 26, 3, 11, 19, 27);
        let hi = _mm512_add_epi32(lo, _mm512_set1_epi32(4));
        let (a_lo, a_hi) = (
            _mm512_permutex2var_epi32(p01, lo, p23),
            _mm512_permutex2var_epi32(p01, hi, p23),
        );
        let (b_lo, b_hi) = (
            _mm512_permutex2var_epi32(p45, lo, p67),
            _mm512_permutex2var_epi32(p45, hi, p67),
        );

        // Two whole columns a register.
        let lo = _mm512_setr_epi32(0, 1, 2, 3, 16, 17, 18, 19, 4, 5, 6, 7, 20, 21, 22, 23);
        let hi = _mm512_add_epi32(lo, _mm512_set1_epi32(8));
        let halves = |v| [_mm512_castsi512_si256(v), _mm512_extracti64x4_epi64::<1>(v)];
        let [c0, c1] = halves(_mm512_permutex2var_epi32(a_lo, lo, b_lo));
        let [c2, c3] = halves(_mm512_permutex2var_epi32(a_lo, hi, b_lo));
        let [c4, c5] = halves(_mm512_permutex2var_epi32(a_hi, lo, b_hi));
        let [c6, c7] = halves(_mm512_permutex2var_epi32(a_hi, hi, b_hi));

        [c0, c1, c2, c3, c4, c5, c6, c7]
    }

    /// See [`super::avx2::recorrelate_lanes`], for eight channels. The lanes
    /// that are live are a mask here, the shifts are arithmetic ones, and a
    /// sample waits less on the one before: of the two products a prediction
    /// takes of the newest states, the second filter's is taken of the bits
    /// the mask took off the prediction before, which are known two steps
    /// before the state is, and the sum is formed in the order its terms are
    /// ready.
    #[target_feature(enable = "avx2,avx512f,avx512vl,avx512dq")]
    pub(super) fn recorrelate_lanes(
        filters: &[Recorrelator],
        coeff_state: &mut [[[i32; 8]; 16]; 2],
        states: &mut [LaneStates; STATE_BASE + 8],
        block_data: &[[i32; 16]],
        rows: &mut [[i32; 16]],
        chi: usize,
        wide: bool,
    ) -> bool {
        let block_size = block_data.len().min(rows.len()).min(STATE_BASE);
        let first = chi.min(16 - LANES);
        let lane_of = |channel: usize| channel - first;

        if chi + filters.len() > first + LANES {
            return false;
        }

        // The coefficients and the states the block starts with, a tap at a
        // time, without the lanes that are not live: their coefficients are
        // zero, so their states stay zero.
        let zero = _mm256_setzero_si256();
        let mut fir_rows = [zero; LANES];
        let mut iir_rows = [zero; LANES];
        let mut shift = zero;
        let mut mask = zero;
        let mut live: __mmask8 = 0;

        for (channel, filter) in (chi..).zip(filters) {
            let lane = lane_of(channel);

            fir_rows[lane] = load_eight(&filter.fir);
            // SAFETY: an array of four `i32` is 16 readable bytes.
            iir_rows[lane] =
                _mm256_zextsi128_si256(unsafe { _mm_loadu_si128(filter.iir.as_ptr().cast()) });
            // As a scalar shift takes its count.
            shift = _mm256_mask_set1_epi32(shift, 1 << lane, filter.shift & 63);
            // The step size is a four-bit field, so the mask is the same in 32 bits.
            mask = _mm256_mask_set1_epi32(mask, 1 << lane, filter.mask as i32);
            live |= 1 << lane;
        }

        let widen = |column| _mm512_cvtepi32_epi64(column);
        let fir = transpose(fir_rows).map(widen);
        let iir_columns = transpose(iir_rows);
        let iir: [__m512i; 4] = core::array::from_fn(|tap| widen(iir_columns[tap]));
        let shift = _mm512_cvtepi32_epi64(shift);
        let mask = _mm512_cvtepi32_epi64(mask);

        for (filter, coeff_state) in coeff_state.iter().enumerate() {
            let rows: [__m256i; LANES] =
                core::array::from_fn(|lane| load_eight(&coeff_state[first + lane]));

            for (tap, column) in transpose(rows).into_iter().enumerate() {
                store(
                    &mut states[STATE_BASE + tap][filter],
                    _mm512_maskz_cvtepi32_epi64(live, column),
                );
            }
        }

        // A state in range, moved up by half the range, has no bit at or above
        // the width of the range.
        let (bias, bits) = if wide {
            (1i64 << 31, 32)
        } else {
            (1i64 << 23, 24)
        };
        let bias = _mm512_set1_epi64(bias);
        let above_range = _mm512_set1_epi64(-1i64 << bits);
        // The products of the older states, over a window of states newest
        // first: taps 2 to 7 of the first filter, 2 and 3 of the second. The
        // two newest states are kept in registers, and their products taken
        // apart.
        let older_products = |window: &[LaneStates]| -> __m512i {
            let window: &[LaneStates; 8] = window[..8].try_into().expect("eight states");
            let mut acc = _mm512_setzero_si512();

            for tap in (2..8).rev() {
                acc = _mm512_add_epi64(acc, _mm512_mul_epi32(fir[tap], load(&window[tap][0])));
            }
            for tap in (2..4).rev() {
                acc = _mm512_add_epi64(acc, _mm512_mul_epi32(iir[tap], load(&window[tap][1])));
            }

            acc
        };

        // The prediction of the first sample, from the states the block starts
        // with: every tap of both filters.
        let history = &states[STATE_BASE];
        let mut newest_fir = load(&history[0]);
        let mut newest_iir = load(&history[1]);
        let mut pred = _mm512_srav_epi64(
            _mm512_add_epi64(
                _mm512_add_epi64(
                    older_products(&states[STATE_BASE..]),
                    _mm512_add_epi64(
                        _mm512_mul_epi32(fir[1], load(&states[STATE_BASE + 1][0])),
                        _mm512_mul_epi32(iir[1], load(&states[STATE_BASE + 1][1])),
                    ),
                ),
                _mm512_add_epi64(
                    _mm512_mul_epi32(fir[0], newest_fir),
                    _mm512_mul_epi32(iir[0], newest_iir),
                ),
            ),
            shift,
        );
        let mut out_of_range = _mm512_setzero_si512();

        for blki in 0..block_size {
            let state_base = STATE_BASE - blki;
            let data: &[i32; LANES] = block_data[blki][first..first + LANES]
                .try_into()
                .expect("eight channels");
            let row: &mut [i32; LANES] = (&mut rows[blki][first..first + LANES])
                .try_into()
                .expect("eight channels");

            // SAFETY: an array of eight `i32` is 32 readable bytes.
            let residual = _mm512_maskz_cvtepi32_epi64(live, unsafe {
                _mm256_loadu_si256(data.as_ptr().cast())
            });
            // All that the next prediction takes from before this sample: the
            // products of the states but the two newest, and the second
            // filter's first product's share of the residual, that filter's
            // newest state being the residual less a part of the prediction.
            // None of it waits on the prediction.
            let older = _mm512_add_epi64(
                older_products(&states[state_base - 1..]),
                _mm512_mul_epi32(iir[0], residual),
            );
            let newest = _mm512_add_epi64(
                _mm512_mul_epi32(fir[1], newest_fir),
                _mm512_mul_epi32(iir[1], newest_iir),
            );

            // The bits the mask keeps go to the first filter's state, those it
            // takes off come off the second's: the latter are below 2^15, the
            // step size being a four-bit field, so their product is exact in
            // 32 bits, and ready before the first state's product is.
            let kept = _mm512_and_si512(pred, mask);
            let dropped = _mm512_andnot_si512(mask, pred);
            let fir_state = _mm512_add_epi64(residual, kept);
            let iir_state = _mm512_sub_epi64(residual, dropped);
            let fir_product = _mm512_mul_epi32(fir[0], fir_state);
            let iir_product = _mm512_mul_epi32(iir[0], dropped);

            // The three or-ed together: 0xFE is the truth table of `a | b | c`.
            out_of_range = _mm512_ternarylogic_epi64::<0xFE>(
                out_of_range,
                _mm512_add_epi64(fir_state, bias),
                _mm512_add_epi64(iir_state, bias),
            );

            store(&mut states[state_base - 1][0], fir_state);
            store(&mut states[state_base - 1][1], iir_state);

            // SAFETY: an array of eight `i32` is 32 writable bytes, of which the
            // mask selects.
            unsafe {
                _mm256_mask_storeu_epi32(row.as_mut_ptr(), live, _mm512_cvtepi64_epi32(fir_state));
            }

            // The sum, in the order the terms are ready: the compiler is free
            // to reassociate a sum, and puts the term that waits on the
            // prediction first, where every other term then waits on it.
            let acc: __m512i;
            // SAFETY: three register-to-register additions, with no memory or
            // flags touched.
            unsafe {
                std::arch::asm!(
                    "vpaddq {acc}, {newest}, {older}",
                    "vpsubq {acc}, {acc}, {iir_product}",
                    "vpaddq {acc}, {acc}, {fir_product}",
                    newest = in(zmm_reg) newest,
                    older = in(zmm_reg) older,
                    iir_product = in(zmm_reg) iir_product,
                    fir_product = in(zmm_reg) fir_product,
                    acc = out(zmm_reg) acc,
                    options(pure, nomem, nostack, preserves_flags),
                );
            }
            pred = _mm512_srav_epi64(acc, shift);
            newest_fir = fir_state;
            newest_iir = iir_state;
        }

        if _mm512_test_epi64_mask(out_of_range, above_range) != 0 {
            return false;
        }

        for (filter, coeff_state) in coeff_state.iter_mut().enumerate() {
            let columns: [__m256i; 8] = core::array::from_fn(|tap| {
                _mm512_cvtepi64_epi32(load(&states[STATE_BASE - block_size + tap][filter]))
            });
            let rows = transpose(columns);

            for channel in chi..chi + filters.len() {
                store_eight(&mut coeff_state[channel], rows[lane_of(channel)]);
            }
        }

        true
    }
}

/// Sum of the products of `a` with the first `N` values of `b`, in 64 bits. A
/// length known at compile time unrolls into straight multiply-adds, where a
/// run-time one pays a loop, or a vector loop's set-up, for a handful of terms.
#[inline(always)]
fn dot_fixed<const N: usize>(a: &[i32; N], b: &[i32]) -> i64 {
    let b: &[i32; N] = b[..N].try_into().unwrap();
    let mut acc = 0i64;
    for i in 0..N {
        acc += a[i] as i64 * b[i] as i64;
    }
    acc
}

/// The products of one primitive matrix that are not zero: which channels of the
/// sample row it reads, and with which coefficients. A matrix of a 7.1 substream
/// often reads half of the eight channels, and one that only rescales a channel
/// reads one.
struct MatrixTerms {
    count: usize,
    channel: [u8; 16],
    coeff: [i32; 16],
    /// The coefficient deltas of the same channels, for the substream that
    /// interpolates its matrices.
    delta: [i32; 16],
}

impl MatrixTerms {
    /// From a matrix's coefficients and, where it has them, its deltas; a channel
    /// is kept when either is not zero.
    fn new(coeff: &[i32], delta: &[i32]) -> Self {
        let mut terms = Self {
            count: 0,
            channel: [0; 16],
            coeff: [0; 16],
            delta: [0; 16],
        };

        for (chi, &coeff) in coeff.iter().enumerate() {
            let delta = delta.get(chi).copied().unwrap_or(0);

            if coeff != 0 || delta != 0 {
                terms.channel[terms.count] = chi as u8;
                terms.coeff[terms.count] = coeff;
                terms.delta[terms.count] = delta;
                terms.count += 1;
            }
        }

        terms
    }

    /// The coefficients in the places of their channels, zero for the channels
    /// the matrix does not read, and whether it reads any of the upper eight.
    #[cfg(target_arch = "x86_64")]
    fn by_channel(&self) -> ([i32; 16], bool) {
        let mut coeff = [0; 16];
        let mut upper = false;

        for (&channel, &term) in self.channel[..self.count].iter().zip(&self.coeff) {
            coeff[(channel & 15) as usize] = term;
            upper |= channel >= 8;
        }

        (coeff, upper)
    }

    /// Whether the matrix changes over the access unit.
    fn has_delta(&self) -> bool {
        self.delta[..self.count].iter().any(|&delta| delta != 0)
    }
}

/// The dither a matrix adds to its products, out of the table the substream
/// fills for each access unit.
#[derive(Clone, Copy)]
struct Dither<'a> {
    table: &'a [i32],
    index_mask: usize,
    /// The matrix takes its dither `2 * step + 1` entries apart, from entry
    /// `step` on: the later the matrix, the shorter its step.
    step: usize,
    /// The samples of the access unit decoded before the block.
    decoded: usize,
    shift: u32,
}

impl<'a> Dither<'a> {
    /// `None` for a matrix that has no scale for it, and adds none.
    fn new(
        table: &'a [i32],
        index_mask: usize,
        step: usize,
        decoded: usize,
        scale: u8,
    ) -> Option<Self> {
        (scale != 0).then_some(Self {
            table,
            index_mask,
            step,
            decoded,
            shift: 11 + scale as u32,
        })
    }

    /// What the matrix adds to the products of row `blki` of the block.
    #[inline(always)]
    fn at(&self, blki: usize) -> i64 {
        let blki_abs = blki + self.decoded;
        let index = self.step * (2 * blki_abs + 1) + blki_abs;

        (self.table[index & self.index_mask] as i64) << self.shift
    }
}

/// The fewest products for which a matrix is taken a whole row at a time: the
/// row's eight or sixteen channels cost the two or four multiplications that
/// as many products cost one at a time, and their sum a few steps more.
const WHOLE_ROW_TERMS: usize = 5;

/// [`apply_matrix`] for a matrix that holds still over the access unit: its
/// products, and its dither where it has one.
fn apply_steady_matrix(
    terms: &MatrixTerms,
    rows: &mut [[i32; 16]],
    bypassed_lsb: &[[u8; 16]],
    pmi: usize,
    dest: usize,
    mask: i32,
    dither: Option<Dither<'_>>,
) {
    #[cfg(target_arch = "x86_64")]
    if terms.count >= WHOLE_ROW_TERMS && Kernel::available() != Kernel::Scalar {
        let (coeff, upper) = terms.by_channel();

        // SAFETY: the processor has AVX2, which is all the function requires,
        // the wider kernel being an extension of it.
        unsafe {
            match (dither, upper) {
                (Some(dither), false) => avx2::apply_matrix::<false>(
                    &coeff,
                    rows,
                    bypassed_lsb,
                    pmi,
                    dest,
                    mask,
                    |blki| dither.at(blki),
                ),
                (Some(dither), true) => avx2::apply_matrix::<true>(
                    &coeff,
                    rows,
                    bypassed_lsb,
                    pmi,
                    dest,
                    mask,
                    |blki| dither.at(blki),
                ),
                (None, false) => {
                    avx2::apply_matrix::<false>(&coeff, rows, bypassed_lsb, pmi, dest, mask, |_| 0)
                }
                (None, true) => {
                    avx2::apply_matrix::<true>(&coeff, rows, bypassed_lsb, pmi, dest, mask, |_| 0)
                }
            }
        }

        return;
    }

    apply_steady_matrix_by_term(terms, rows, bypassed_lsb, pmi, dest, mask, dither);
}

/// [`apply_steady_matrix`] one product at a time.
fn apply_steady_matrix_by_term(
    terms: &MatrixTerms,
    rows: &mut [[i32; 16]],
    bypassed_lsb: &[[u8; 16]],
    pmi: usize,
    dest: usize,
    mask: i32,
    dither: Option<Dither<'_>>,
) {
    match dither {
        Some(dither) => by_term_count!(
            terms,
            apply_matrix(terms, rows, bypassed_lsb, pmi, dest, mask, |blki, _| dither
                .at(blki))
        ),
        None => by_term_count!(
            terms,
            apply_matrix(terms, rows, bypassed_lsb, pmi, dest, mask, |_, _| 0)
        ),
    }
}

/// Apply one primitive matrix to every sample row of a block: channel `dest`
/// becomes the sum of the matrix's `N` products and of `extra`, which is given
/// the row's index in the block and the sum of its products with the deltas.
#[inline(always)]
fn apply_matrix<const N: usize>(
    terms: &MatrixTerms,
    rows: &mut [[i32; 16]],
    bypassed_lsb: &[[u8; 16]],
    pmi: usize,
    dest: usize,
    mask: i32,
    extra: impl Fn(usize, i64) -> i64,
) {
    let channel: &[u8; N] = terms.channel[..N].try_into().unwrap();
    let coeff: &[i32; N] = terms.coeff[..N].try_into().unwrap();
    let delta: &[i32; N] = terms.delta[..N].try_into().unwrap();

    for (blki, (row, bypassed_lsb)) in rows.iter_mut().zip(bypassed_lsb).enumerate() {
        let mut acc = 0i64;
        let mut acc_delta = 0i64;

        for i in 0..N {
            let sample = row[(channel[i] & 15) as usize] as i64;

            acc += coeff[i] as i64 * sample;
            acc_delta += delta[i] as i64 * sample;
        }

        acc += extra(blki, acc_delta);

        row[dest] = (((acc >> 18) as i32) & mask) + bypassed_lsb[pmi] as i32;
    }
}

/// The rows of a block followed by zeros, up to the longest block there is.
#[cold]
fn padded_rows<T: Copy + Default>(rows: &[[T; 16]]) -> Box<[[T; 16]; 160]> {
    let mut padded = Box::new([[T::default(); 16]; 160]);
    let len = rows.len().min(160);

    padded[..len].copy_from_slice(&rows[..len]);
    padded
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A matrix taken a whole row at a time leaves the rows that one product at
    /// a time leaves, whichever channels it reads, with and without dither.
    #[test]
    fn whole_rows_match_one_product_at_a_time() {
        let mut seed = 0x9e37_79b9_7f4a_7c15u64;
        let mut random = |bits: u32| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed as i64 >> (64 - bits)) as i32
        };
        let table: Vec<i32> = (0..64).map(|_| random(8)).collect();
        let (mut whole, mut upper) = (0, 0);

        for round in 0..2000usize {
            // Of all sixteen channels, or of the lower eight alone.
            let channels = if round % 3 == 0 { 8 } else { 16 };
            let mut coeff = [0; 16];
            for coeff in &mut coeff[..channels] {
                if random(2) != 0 {
                    *coeff = random(16);
                }
            }
            let terms = MatrixTerms::new(&coeff, &[]);
            whole += (terms.count >= WHOLE_ROW_TERMS) as usize;
            upper += (terms.count >= WHOLE_ROW_TERMS && channels == 16) as usize;

            let block_size = 8 + round % 153;
            let rows: Vec<[i32; 16]> = (0..block_size)
                .map(|_| core::array::from_fn(|_| random(24)))
                .collect();
            let bypassed_lsb: Vec<[u8; 16]> = (0..block_size)
                .map(|_| core::array::from_fn(|_| (random(2) & 1) as u8))
                .collect();
            let (pmi, dest) = (round % 16, random(5) as usize & 15);
            let mask = !((1 << (round % 3)) - 1);
            let dither = Dither::new(&table, 63, 1 + round % 5, round % 40, (round % 4) as u8);

            let mut expected = rows.clone();
            apply_steady_matrix_by_term(
                &terms,
                &mut expected,
                &bypassed_lsb,
                pmi,
                dest,
                mask,
                dither,
            );
            let mut actual = rows;
            apply_steady_matrix(&terms, &mut actual, &bypassed_lsb, pmi, dest, mask, dither);

            assert_eq!(actual, expected, "round {round}");
        }

        // Both kinds of rows were taken whole, where the processor can.
        assert!(whole > 500 && upper > 500 && whole - upper > 100);
    }

    /// Filters run at their orders leave the rows and the states that they leave
    /// at full length, and report the same state out of its range.
    #[test]
    fn filters_at_their_orders_match_full_length() {
        let mut seed = 0x1234_5678_9abc_def1u64;
        let mut random = |bits: u32| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed as i64 >> (64 - bits)) as i32
        };
        let (mut in_range, mut out_of_range) = (0, 0);

        for round in 0..45 * 8usize {
            let orders = (round % 9, round / 9 % 5);
            let wide = round % 7 == 0;
            let chi = round % 16;
            let block_size = 8 + (random(16) as usize & 0xFFFF) % 153;
            let residual_bits = if round % 11 == 0 { 24 } else { 16 };

            let filter = Recorrelator {
                fir: core::array::from_fn(|tap| if tap < orders.0 { random(11) } else { 0 }),
                iir: core::array::from_fn(|tap| if tap < orders.1 { random(10) } else { 0 }),
                orders,
                shift: 8 + (random(4) & 7),
                mask: !((1 << (random(3) & 3)) - 1),
            };
            let block_data: Vec<[i32; 16]> = (0..block_size)
                .map(|_| core::array::from_fn(|_| random(residual_bits)))
                .collect();
            let history: [[i32; 8]; 2] =
                core::array::from_fn(|_| core::array::from_fn(|_| random(20)));

            let mut full_history = history;
            let mut full_rows = vec![[0x5a5a5a5a; 16]; block_size];
            let [fir, iir] = &mut full_history.each_mut();
            let full =
                recorrelate_at::<8, 4>(&filter, [fir, iir], &block_data, &mut full_rows, chi, wide);

            let mut history = history;
            let mut rows = vec![[0x5a5a5a5a; 16]; block_size];
            let [fir, iir] = &mut history.each_mut();
            let at_orders = recorrelate(&filter, [fir, iir], &block_data, &mut rows, chi, wide);

            match (&at_orders, &full) {
                (Ok(()), Ok(())) => {
                    assert_eq!(rows, full_rows, "round {round}");
                    assert_eq!(history, full_history, "round {round}");
                    in_range += 1;
                }
                (Err(at_orders), Err(full)) => {
                    assert_eq!(at_orders.to_string(), full.to_string(), "round {round}");
                    out_of_range += 1;
                }
                _ => panic!("round {round}: {at_orders:?}, {full:?} at full length"),
            }
        }

        assert!(
            in_range > 100 && out_of_range > 10,
            "{in_range} {out_of_range}"
        );
    }

    /// The lanes leave the rows and the filter states that one channel at a time
    /// leaves, and refuse the blocks in which a state leaves its range: every
    /// kernel the processor has, not only the widest.
    #[test]
    fn lanes_match_one_channel_at_a_time() {
        let widest = Kernel::available();
        let kernels = [Kernel::Avx2, Kernel::Avx512];

        for kernel in kernels.into_iter().filter(|&kernel| kernel <= widest) {
            lanes_match_one_channel_at_a_time_with(kernel);
        }
    }

    fn lanes_match_one_channel_at_a_time_with(kernel: Kernel) {
        let lanes = kernel.lanes();
        let mut seed = 0x2545_f491_4f6c_dd1du64;
        let mut random = |bits: u32| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed as i64 >> (64 - bits)) as i32
        };
        let mut states = Box::new(LaneStateBuffer([[[0; MAX_LANES]; 2]; STATE_BASE + 8]));
        let (mut in_range, mut out_of_range) = (0, 0);

        for round in 0..400usize {
            let wide = round % 5 == 0;
            let channels = 1 + round % lanes;
            let chi = [0, 3, 16 - lanes, 16 - channels][round / lanes % 4];
            let block_size = 8 + (random(16) as usize & 0xFFFF) % 153;
            // Mostly residuals a stable filter keeps in range, sometimes not.
            let residual_bits = if round % 7 == 0 { 24 } else { 16 };

            // Filters of every length, one at a time at their orders and side by
            // side at full length.
            let filters: Vec<Recorrelator> = (0..channels)
                .map(|_| {
                    let orders = ((random(5) & 15) as usize % 9, (random(4) & 7) as usize % 5);

                    Recorrelator {
                        fir: core::array::from_fn(
                            |tap| {
                                if tap < orders.0 { random(11) } else { 0 }
                            },
                        ),
                        iir: core::array::from_fn(
                            |tap| {
                                if tap < orders.1 { random(10) } else { 0 }
                            },
                        ),
                        orders,
                        shift: 8 + (random(4) & 7),
                        mask: !((1 << (random(3) & 3)) - 1),
                    }
                })
                .collect();
            let block_data: Vec<[i32; 16]> = (0..block_size)
                .map(|_| core::array::from_fn(|_| random(residual_bits)))
                .collect();
            let coeff_state: [[[i32; 8]; 16]; 2] = core::array::from_fn(|_| {
                core::array::from_fn(|_| core::array::from_fn(|_| random(20)))
            });

            let mut one_state = coeff_state;
            let mut one_rows = vec![[0x5a5a5a5a; 16]; block_size];
            let one_at_a_time = filters.iter().zip(chi..).all(|(filter, chi)| {
                let [fir, iir] = &mut one_state.each_mut().map(|state| &mut state[chi]);

                recorrelate(filter, [fir, iir], &block_data, &mut one_rows, chi, wide).is_ok()
            });

            let mut lane_state = coeff_state;
            let mut lane_rows = vec![[0x5a5a5a5a; 16]; block_size];
            let side_by_side = recorrelate_lanes(
                kernel,
                &filters,
                &mut lane_state,
                &mut states.0,
                &block_data,
                &mut lane_rows,
                chi,
                wide,
            );

            assert_eq!(side_by_side, one_at_a_time, "{kernel:?} round {round}");
            if side_by_side {
                assert_eq!(lane_rows, one_rows, "{kernel:?} round {round}");
                assert_eq!(lane_state, one_state, "{kernel:?} round {round}");
                in_range += 1;
            } else {
                out_of_range += 1;
            }
        }

        assert!(
            in_range > 100 && out_of_range > 10,
            "{kernel:?}: {in_range} {out_of_range}"
        );
    }
}
