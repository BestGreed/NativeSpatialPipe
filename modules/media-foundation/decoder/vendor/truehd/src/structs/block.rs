//! Audio block structures and compression parameters.
//!
//! TrueHD audio compression operates on blocks containing 8-160 samples per channel.
//! Each block contains optional restart header, block header, and compressed audio data.
//!
//! ## Block Structure
//!
//! - **Restart header**: Decoder initialization parameters
//! - **Block header**: Parameter updates controlled by guard flags
//! - **Compressed data**: Huffman-encoded audio samples
//! - **Error protection**: Optional CRC and length validation

use anyhow::{Result, anyhow, bail};
use log::Level::Warn;
use log::{info, trace, warn};

use crate::log_or_err;
use crate::process::decode::DecoderState;
use crate::process::parse::{ParserRestartState, ParserState};
use crate::structs::channel::ChannelParams;
use crate::structs::matrix::Matrixing;
use crate::structs::restart_header::{Guards, GuardsField, RestartHeader};
use crate::utils::bitstream_io::{
    BsIoSliceReader, HUFFMAN_DEEPEST_CODE, HuffmanTable, huffman_table_of,
};
use crate::utils::errors::BlockError;
use crate::utils::perf::Timer;

/// Block header containing selective parameter updates.
///
/// Block headers provide incremental updates to decoding parameters controlled
/// by guard flags. Only parameters with enabled guards can be updated.
#[derive(Debug, Default)]
pub struct BlockHeader {
    pub guards: Option<Guards>,
    pub block_size: Option<usize>,
    pub matrixing: Option<Matrixing>,
    pub output_shift: [Option<i8>; 16],
    pub quantiser_step_size: [Option<u32>; 16],
    pub channel_params: [Option<ChannelParams>; 16],
}

/// Complete audio block with compressed data and decoding parameters.
///
/// Contains 8-160 samples per channel with optional restart header,
/// block header, and compressed audio data.
#[derive(Debug, Default)]
pub struct Block {
    pub restart_header: Option<RestartHeader>,
    pub block_header: Option<BlockHeader>,
    pub block_data_bits: Option<u16>,
    /// The bits each primitive matrix bypasses, one row per sample of the block.
    pub bypassed_lsb: Vec<[u8; 16]>,
    /// The residual of each channel, one row per sample of the block.
    pub block_data: Vec<[i32; 16]>,
    pub block_header_crc: u8,
}

/// How a channel's samples are read through a block: the constants of the
/// arithmetic, and whether the parameters allow reading a sample in one go.
#[derive(Clone, Copy, Default)]
struct SampleRead {
    /// Whether the fields below hold. Parameters out of their ranges are left to
    /// the reads that report them.
    plain: bool,
    table: Option<&'static HuffmanTable>,
    lsbs_bits: u32,
    /// The channel's offset, less what centres the code and the bits after it.
    bias: i32,
    quantiser_step_size: u32,
}

impl SampleRead {
    fn new(huff_type: usize, huff_lsbs: u32, huff_offset: i32, quantiser_step_size: u32) -> Self {
        // No more bits after the code than a window gives up at once along with
        // the longest code, which also keeps the arithmetic from overflowing.
        if quantiser_step_size > huff_lsbs || huff_lsbs - quantiser_step_size > 22 || huff_type > 3
        {
            return Self::default();
        }

        let lsbs_bits = huff_lsbs - quantiser_step_size;
        let centre = if huff_type != 0 {
            let shift = lsbs_bits as i32 + (2 - huff_type as i32);

            if shift < 0 { 0 } else { 1 << shift }
        } else if lsbs_bits > 0 {
            1 << (lsbs_bits - 1)
        } else {
            0
        };

        Self {
            plain: true,
            table: huffman_table_of(huff_type),
            lsbs_bits,
            bias: huff_offset.wrapping_sub(centre),
            quantiser_step_size,
        }
    }
}

impl BlockHeader {
    /// Read a block header into `self`, which must be freshly defaulted. In place
    /// because a header is 5 KiB of mostly absent updates, which returning it
    /// would copy twice on the way to its block.
    fn read_into(&mut self, state: &mut ParserState, reader: &mut BsIoSliceReader) -> Result<()> {
        let samples_per_au = state.samples_per_au;
        let restart = &mut state.substream_state_mut()?.restart;
        let max_shift = restart.max_shift;

        let bh = self;

        if restart.guards.need_change(GuardsField::Guards) {
            // new_guards
            if reader.get()? {
                restart.guards = Guards::read(reader)?;
            }
        }

        let guards = restart.guards;

        if guards.need_change(GuardsField::BlockSize) {
            // new_block_size
            if reader.get()? {
                let block_size = reader.get_n::<u16>(9)? as usize;
                if !(8..=160).contains(&block_size) {
                    bail!(BlockError::InvalidBlockSizeRange(block_size));
                } else if block_size > samples_per_au {
                    bail!(BlockError::BlockSizeExceedsAU {
                        max: samples_per_au,
                        actual: block_size
                    });
                } else if block_size & 7 != 0 {
                    warn!("Block size {block_size} is not a multiple of 8")
                }

                bh.block_size = Some(block_size);
                restart.block_size = block_size;
            }
        }

        if guards.need_change(GuardsField::Matrixing) {
            // new_matrixing
            if reader.get()? {
                bh.matrixing = Some(Matrixing::read(state, reader)?);
            }
        }

        let restart = &mut state.substream_state_mut()?.restart;

        if guards.need_change(GuardsField::OutputShift) {
            // new_output_shift
            if reader.get()? {
                for i in 0..=restart.max_matrix_chan {
                    let output_shift = reader.get_s(4)?;
                    if output_shift > max_shift {
                        bail!(BlockError::OutputShiftTooLarge {
                            index: i,
                            value: output_shift,
                            max: max_shift,
                            substream: state.substream_index
                        });
                    }

                    bh.output_shift[i] = Some(output_shift);
                    restart.output_shift[i] = output_shift;
                }
            }
        }

        if guards.need_change(GuardsField::QuantiserStepSize) {
            // new_quantiser_step_size
            if reader.get()? {
                for i in 0..=restart.max_chan {
                    let quantiser_step_size = reader.get_n(4)?;

                    bh.quantiser_step_size[i] = Some(quantiser_step_size);
                    restart.quantiser_step_size[i] = quantiser_step_size;
                }
            }
        }

        for chi in restart.min_chan..=restart.max_chan {
            // params_for_this_chan
            if reader.get()? {
                bh.channel_params[chi] = Some(ChannelParams::read(state, reader, chi)?);
            }
        }

        Ok(())
    }

    pub fn update_decoder_state(&self, state: &mut DecoderState) -> Result<()> {
        if let Some(block_size) = self.block_size {
            state.substream_state_mut()?.block_size = block_size;
        }

        if let Some(matrixing) = &self.matrixing {
            matrixing.update_decoder_state(state)?;
        }

        let ss_state = state.substream_state_mut()?;

        for (i, output_shift) in self.output_shift.iter().enumerate() {
            if let Some(output_shift) = output_shift {
                ss_state.output_shift[i] = *output_shift;
            }
        }

        for (i, quantiser_step_size) in self.quantiser_step_size.iter().enumerate() {
            if let Some(quantiser_step_size) = quantiser_step_size {
                ss_state.quantiser_step_size[i] = *quantiser_step_size;
            }
        }

        for (i, channel_param) in self.channel_params.iter().enumerate() {
            if let Some(channel_param) = channel_param {
                channel_param.update_decoder_state(state, i)?;
            }
        }

        Ok(())
    }
}

impl Block {
    pub fn read(state: &mut ParserState, reader: &mut BsIoSliceReader) -> Result<Self> {
        let mut b = Block::default();
        b.read_into(state, reader)?;
        Ok(b)
    }

    /// As [`Default`] gives it, but for its sample rows, which
    /// [`read_into`](Self::read_into) sizes and clears when it comes to them.
    pub(crate) fn reset(&mut self) {
        self.restart_header = None;
        self.block_header = None;
        self.block_data_bits = None;
        self.block_header_crc = 0;
    }

    /// [`Block::read`] into a block that is already where it will live. `self` must be
    /// freshly defaulted or [`reset`](Self::reset).
    pub(crate) fn read_into(
        &mut self,
        state: &mut ParserState,
        reader: &mut BsIoSliceReader,
    ) -> Result<()> {
        let header_setup = Timer::start();
        let b = self;

        // block_header_exists
        if reader.get()? {
            // restart_header_exists
            if reader.get()? {
                b.restart_header = Some(RestartHeader::read(state, reader)?);
            }

            b.block_header
                .insert(BlockHeader::default())
                .read_into(state, reader)?;
        }

        b.block_data_bits = if state.substream_state()?.restart.error_protect {
            let block_data_bits = reader.get_n(16)?;
            if block_data_bits > 16000 {
                bail!(BlockError::BlockDataBitsTooLarge(block_data_bits));
            }
            Some(block_data_bits)
        } else {
            None
        };

        // TODO: for all substreams
        if !state.has_parsed_substream && state.substream_state()?.restart.block_index == 0 {
            // latency
            let au_offset = state.au_counter - state.last_major_sync_index;
            let samples_per_au = state.samples_per_au;
            let sample_offset = au_offset * samples_per_au;

            let output_timing = state.output_timing;
            let input_timing = state.input_timing;

            trace!(
                "AU {}: au_offset = {}, samples_per_au = {}",
                // , wrapped output_timing = {}, wrapped input_timing = {}",
                state.au_counter,
                au_offset,
                samples_per_au,
                // output_timing,
                // input_timing
            );

            let mut prev_latency = state.substream_state()?.latency;
            let latency = sample_offset
                .wrapping_add(output_timing)
                .wrapping_sub(input_timing)
                & 0xFFFF;

            {
                let ss_state = state.substream_state_mut()?;
                ss_state.prev_latency = prev_latency;
                ss_state.latency = latency;
            }

            state.max_latency = state.max_latency.max(latency);

            if !state.is_major_sync {
                state.advance = latency.wrapping_sub(samples_per_au);
            }

            trace!(
                "AU {}: latency = {}, prev_latency = {}, advance = {}",
                state.au_counter, latency, prev_latency, state.advance
            );

            if state.flags & 0x8000 == 0 || (!state.has_parsed_au) {
                prev_latency = latency;
            } else if prev_latency != latency {
                log_or_err!(
                    state,
                    Warn,
                    anyhow!(BlockError::LatencyInconsistent {
                        substream: state.substream_index
                    }),
                    reader
                );
            }

            if state.fifo_duration > prev_latency {
                log_or_err!(
                    state,
                    Warn,
                    anyhow!(BlockError::DurationExceedsLatency {
                        duration: state.fifo_duration,
                        latency
                    }),
                    reader
                );
            }

            let samples_per_75ms =
                crate::structs::sync::samples_per_75ms(state.audio_sampling_frequency_1);

            if prev_latency as u32 > samples_per_75ms {
                log_or_err!(
                    state,
                    Warn,
                    anyhow!(BlockError::LatencyTooHigh {
                        latency: prev_latency,
                        samples: samples_per_75ms
                    }),
                    reader
                );
            }

            if prev_latency < samples_per_au {
                log_or_err!(
                    state,
                    Warn,
                    anyhow!(BlockError::LatencyTooLow {
                        latency: prev_latency,
                        au: samples_per_au
                    }),
                    reader
                );
            }

            // update output timing
            {
                let i = state.substream_state()?.history_index;
                let output_timing = if !state.has_parsed_au || state.segment_start {
                    state.output_timing
                } else {
                    let i = i.wrapping_sub(1) & 0x7F;

                    state.substream_state()?.output_timing_history[i] + state.samples_per_au
                };

                state.substream_state_mut()?.output_timing_history[i] = output_timing;

                let substream_size = if state.substream_index == 0 {
                    state.substream_state()?.substream_end_ptr
                } else {
                    let end_ptr = state.substream_state()?.substream_end_ptr;
                    let start_ptr =
                        state.substream_state[state.substream_index - 1].substream_end_ptr;
                    end_ptr.checked_sub(start_ptr).ok_or_else(|| {
                        anyhow!(BlockError::SubstreamSizeUnderflow {
                            substream: state.substream_index,
                            end_ptr,
                            start_ptr,
                        })
                    })?
                };

                state.substream_state_mut()?.substream_size_history[i] =
                    (substream_size as usize) << 1;

                state.substream_state_mut()?.history_index = i.wrapping_add(1) & 0x7F;

                trace!(
                    "AU {}: unwrapped_output_timing = {}",
                    // , substream_size = {}, history_index = {}",
                    state.au_counter,
                    // state.substream_index,
                    output_timing,
                    // state.substream_state()?.substream_size_history[i],
                    // state.substream_state()?.history_index
                );
            }
        }

        let ParserRestartState {
            restart_sync_word,
            min_chan,
            max_chan,
            max_lsbs,
            block_size,
            error_protect,

            primitive_matrices,

            huff_offset,
            huff_lsbs,
            huff_type,
            lsb_bypass_used,
            lsb_bypass_bit_count,
            quantiser_step_size,
            ..
        } = state.substream_state()?.restart;

        let block_data_start_pos = reader.position()?;

        for (chi, &lsbs) in huff_lsbs
            .iter()
            .enumerate()
            .take(max_chan + 1)
            .skip(min_chan)
        {
            if lsbs > max_lsbs {
                bail!(BlockError::HuffLsbsTooLarge {
                    channel: chi,
                    actual: lsbs as usize,
                    max: max_lsbs as usize
                });
            }
        }

        // Over the rows of the block read here before, where there was one.
        b.bypassed_lsb.clear();
        b.bypassed_lsb.resize(block_size, [0; 16]);
        b.block_data.clear();
        b.block_data.resize(block_size, [0; 16]);

        header_setup.record(&mut state.perf.block_header_setup);

        // What a sample of each channel takes to read, settled once for the block.
        let mut sample_reads = [SampleRead::default(); 16];
        for chi in min_chan..max_chan + 1 {
            sample_reads[chi & 15] = SampleRead::new(
                huff_type[chi],
                huff_lsbs[chi],
                huff_offset[chi],
                quantiser_step_size[chi],
            );
        }

        // The matrices that bypass bits, and how many: the count each states in
        // a substream that has counts, one bit where it has a flag.
        let mut bypassing = [(0usize, 0u32); 16];
        let mut bypassing_len = 0;
        for pmi in 0..primitive_matrices.min(16) {
            let bits = if restart_sync_word == 0x31EC {
                lsb_bypass_bit_count[pmi] as u32
            } else {
                lsb_bypass_used[pmi] as u32
            };

            if bits != 0 {
                bypassing[bypassing_len] = (pmi, bits);
                bypassing_len += 1;
            }
        }
        let bypassing = &bypassing[..bypassing_len];
        let checked = restart_sync_word != 0x31EC;

        let mut window = reader.window();

        for blki in 0..block_size {
            // bypassed_lsb
            let bypassed_lsb = Timer::start();
            let bypassed_lsb_row = &mut b.bypassed_lsb[blki];
            let mut bypassed_lsb_bits = 0;

            if restart_sync_word == 0x31EC {
                for &(pmi, count) in bypassing {
                    bypassed_lsb_row[pmi] = if count <= 8
                        && let Some(bits) = window.take(count)
                    {
                        bits as u8
                    } else {
                        reader.resume(&window);
                        let bits = reader.get_n::<u8>(count)?;
                        window = reader.window();
                        bits
                    };
                    bypassed_lsb_bits += count as u64;
                }
            } else {
                // All of them out of one look at the stream.
                if let Some(bits) = window.take(bypassing.len() as u32) {
                    for (read, &(pmi, _)) in bypassing.iter().enumerate() {
                        bypassed_lsb_row[pmi] = ((bits >> (bypassing.len() - 1 - read)) & 1) as u8;
                    }
                } else {
                    reader.resume(&window);
                    for &(pmi, _) in bypassing {
                        bypassed_lsb_row[pmi] = reader.get_n::<u8>(1)?;
                    }
                    window = reader.window();
                }
                bypassed_lsb_bits += bypassing.len() as u64;
            }

            bypassed_lsb.record(&mut state.perf.block_bypassed_lsb);
            let block_data = &mut b.block_data[blki];

            let huffman = Timer::start();
            // huff decode
            for chi in min_chan..max_chan + 1 {
                let read = &sample_reads[chi & 15];

                // The code and the bits after it, out of one look at the stream.
                // A sample that anything is to be said about is left to the reads
                // below, which say it.
                if read.plain
                    && let Some(loaded) = window.load()
                {
                    let word = window.bits();
                    let (code, code_len) = match read.table {
                        Some(table) => {
                            let (code, len) = table[(word >> 55) as usize];
                            (code as i32, len as u32)
                        }
                        None => (0, 0),
                    };
                    let size = code_len + read.lsbs_bits;
                    let lsbs = ((word << code_len) >> 32 >> (32 - read.lsbs_bits)) as i32;
                    let audio_data =
                        (lsbs + (code << read.lsbs_bits) + read.bias) << read.quantiser_step_size;

                    if code != HUFFMAN_DEEPEST_CODE
                        && (!checked
                            || ((-(1 << 23)..1 << 23).contains(&audio_data)
                                && size <= 29
                                && (chi != min_chan || size as u64 + bypassed_lsb_bits <= 32)))
                    {
                        window.consume(loaded, size);
                        block_data[chi] = audio_data;
                        continue;
                    }
                }

                reader.resume(&window);

                let huff_offset = huff_offset[chi];
                let huff_type = huff_type[chi];
                let huff_lsbs = huff_lsbs[chi];
                let quantiser_step_size = quantiser_step_size[chi];
                if quantiser_step_size > huff_lsbs {
                    bail!(BlockError::QuantiserStepTooLarge);
                }

                let lsbs_bits = huff_lsbs - quantiser_step_size;
                let huff_start_pos = reader.position()?;

                let mut audio_data = if huff_type != 0 {
                    let huff_code = reader.get_huffman(huff_type)?;

                    // Every table reaches its deepest leaf through a nine-bit code whose
                    // ninth bit it ignores, and only 1 is legal there.
                    if huff_code == HUFFMAN_DEEPEST_CODE {
                        reader.seek(-1)?;

                        if !reader.get()? {
                            log_or_err!(
                                state,
                                Warn,
                                anyhow!(BlockError::HuffmanNinthBitMissing),
                                reader
                            );
                        }
                    }

                    let lsbs = if lsbs_bits > 0 {
                        reader.get_n::<u32>(lsbs_bits)? as i32
                    } else {
                        0
                    };
                    let shift = lsbs_bits as i32 + (2 - huff_type as i32);

                    lsbs + (huff_code << lsbs_bits) - if shift < 0 { 0 } else { 1 << shift }
                } else {
                    let lsbs = if lsbs_bits > 0 {
                        reader.get_n::<u32>(lsbs_bits)? as i32
                    } else {
                        0
                    };
                    lsbs - (if lsbs_bits > 0 {
                        1 << (lsbs_bits - 1)
                    } else {
                        0
                    })
                };

                audio_data += huff_offset;
                audio_data <<= quantiser_step_size;

                if checked {
                    let huff_size = reader.position()? - huff_start_pos;

                    if audio_data >= 1 << 23 {
                        bail!(BlockError::HuffmanPositiveSaturation);
                    } else if audio_data < -(1 << 23) {
                        bail!(BlockError::HuffmanNegativeSaturation);
                    }

                    if chi == min_chan && huff_size + bypassed_lsb_bits > 32 {
                        warn!(
                            "Channel {chi}: LSB + Huffman bits ({}) exceed 32-bit limit",
                            huff_size + bypassed_lsb_bits
                        )
                    }

                    if huff_size > 29 {
                        bail!(BlockError::HuffmanSampleTooLong);
                    }
                }

                block_data[chi] = audio_data;
                window = reader.window();
            }

            huffman.record(&mut state.perf.block_huffman_decode);
        }

        reader.resume(&window);

        if let Some(block_data_bits) = b.block_data_bits {
            let actual_block_data_bits = reader.position()? - block_data_start_pos;
            if actual_block_data_bits != block_data_bits as u64 {
                bail!(BlockError::BlockDataBitCountMismatch {
                    expected: block_data_bits,
                    actual: actual_block_data_bits,
                });
            }
        }

        let checks = Timer::start();
        if error_protect {
            b.block_header_crc = reader.get_n(8)?;
            info!(
                "Block header CRC found: {:#04X} (error protection enabled)",
                b.block_header_crc
            );
        }

        checks.record(&mut state.perf.block_checks);

        Ok(())
    }

    pub fn update_decoder_state(&self, state: &mut DecoderState) -> Result<()> {
        if let Some(restart_header) = &self.restart_header {
            restart_header.update_decoder_state(state)?;
        } else if state.substream_state()?.decoded_sample_len == 0 {
            let samples_per_au = state.samples_per_au as u16;
            let output_timing = &mut state.substream_state_mut()?.output_timing;

            *output_timing = output_timing.wrapping_add(samples_per_au);
        }

        if let Some(block_header) = &self.block_header {
            block_header.update_decoder_state(state)?;
        }

        // The samples stay in the block: the decoder reads them from it.
        Ok(())
    }
}
