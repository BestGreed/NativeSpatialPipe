//! Bitstream I/O utilities for audio parsing.
//!
//! Provides bitstream reading, Huffman decoding, CRC validation,
//! and specialized bit manipulation functions for format parsing.

use std::io;
use std::io::SeekFrom;

use bitstream_io::{BigEndian, BitRead, BitReader, SignedInteger, UnsignedInteger};

use crate::utils::crc::{Crc8, Crc16, crc8, crc16};

const STACK_BUF_SIZE: usize = 256;

/// `bitstream_io::define_huffman_tree!` without its writing half.
///
/// Since bitstream-io 4.10, `ToBits::to_bits` expands both children of every
/// node in both orders, so its code doubles with each level of the tree. These
/// trees are nine levels deep and never written, but `to_bits` is generic, so
/// its MIR is still optimized for the crate metadata: about 150,000 locals and
/// 140,000 blocks per tree, which took a release build of this crate past 13 GB
/// of memory.
macro_rules! define_huffman_tree {
    ($name:ident : $type:ty = $nodes:tt) => {
        #[derive(Copy, Clone, Debug)]
        struct $name;

        impl bitstream_io::huffman::FromBits for $name {
            type Symbol = $type;

            fn from_bits<F, E>(mut next: F) -> Result<Self::Symbol, E>
            where
                F: FnMut() -> Result<bool, E>,
            {
                bitstream_io::compile_read_tree_nodes!(next, $nodes)
            }
        }
    };
}

define_huffman_tree!(HuffTree1 : i32 = [
        [
            [[[[[[[(-7), (-7)], (-6)], (-5)], (-4)], (-3)], (-2)], (-1)],
            [[[[[[[ 10 ,  10 ],   9 ],   8 ],   7 ],   6 ],   5 ],   4 ]
        ],
        [
            [0, 1], [2, 3]
        ]
    ]
);

define_huffman_tree!(HuffTree2 : i32 = [
        [
            [[[[[[[(-7), (-7)], (-6)], (-5)], (-4)], (-3)], (-2)], (-1)],
            [[[[[[[  8 ,   8 ],   7 ],   6 ],   5 ],   4 ],   3 ],   2 ]
        ],
        [0, 1]
    ]
);

define_huffman_tree!(HuffTree3 : i32 = [
        [
            [[[[[[[(-7), (-7)], (-6)], (-5)], (-4)], (-3)], (-2)], (-1)],
            [[[[[[[  7 ,   7 ],   6 ],   5 ],   4 ],   3 ],   2 ],   1 ]
        ],
        0
    ]
);

/// Value every huffman table maps its deepest, nine-bit code to.
pub const HUFFMAN_DEEPEST_CODE: i32 = -7;

#[derive(Debug)]
pub struct BitstreamIoReader<R: io::Read + io::Seek> {
    bs: BitReader<R, BigEndian>,
    len: u64,
}

/// The reader every parser in this crate reads with.
pub type BsIoSliceReader<'a> = SliceBitReader<'a>;

impl<R> BitstreamIoReader<R>
where
    R: io::Read + io::Seek,
{
    pub fn new(read: R, len_bytes: u64) -> Self {
        Self {
            bs: BitReader::new(read),
            len: len_bytes << 3,
        }
    }

    #[inline(always)]
    pub fn get(&mut self) -> io::Result<bool> {
        self.bs.read_bit()
    }

    #[inline(always)]
    pub fn get_n<I: UnsignedInteger>(&mut self, n: u32) -> io::Result<I> {
        // Skip bounds check for small reads - bitstream_io handles EOF internally
        if n <= 32 {
            match self.bs.read_unsigned_var(n) {
                Ok(val) => Ok(val),
                Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => {
                    // Only call position() on error path to avoid overhead
                    Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        format!(
                            "get_n({}): out of bounds bits at {}",
                            n,
                            self.bs.position_in_bits().unwrap_or(0)
                        ),
                    ))
                }
                Err(e) => Err(e),
            }
        } else {
            // For larger reads, keep bounds check
            self.available().and_then(|avail| {
                if n as u64 > avail {
                    Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        format!(
                            "get_n({}): out of bounds bits at {}",
                            n,
                            self.bs.position_in_bits().unwrap_or(0)
                        ),
                    ))
                } else {
                    self.bs.read_unsigned_var(n)
                }
            })
        }
    }

    #[inline(always)]
    pub fn get_s<S: SignedInteger>(&mut self, n: u32) -> io::Result<S> {
        // Skip bounds check for small reads - bitstream_io handles EOF internally
        if n <= 32 {
            match self.bs.read_signed_var(n) {
                Ok(val) => Ok(val),
                Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => {
                    // Only call position() on error path to avoid overhead
                    Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        format!(
                            "get_s({}): out of bounds bits at {}",
                            n,
                            self.bs.position_in_bits().unwrap_or(0)
                        ),
                    ))
                }
                Err(e) => Err(e),
            }
        } else {
            // For larger reads, keep bounds check
            self.available().and_then(|avail| {
                if n as u64 > avail {
                    Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        format!(
                            "get_s({}): out of bounds bits at {}",
                            n,
                            self.bs.position_in_bits().unwrap_or(0)
                        ),
                    ))
                } else {
                    self.bs.read_signed_var(n)
                }
            })
        }
    }

    #[inline(always)]
    pub fn get_huffman(&mut self, huff_type: usize) -> io::Result<i32> {
        match huff_type {
            1 => self.bs.read_huffman::<HuffTree1>(),
            2 => self.bs.read_huffman::<HuffTree2>(),
            3 => self.bs.read_huffman::<HuffTree3>(),
            _ => Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "get_huffman: unsupported huff_type",
            )),
        }
    }

    #[inline(always)]
    pub fn get_variable_bits_max(&mut self, n: u32, max_num_groups: u32) -> io::Result<u32> {
        let mut value = 0;
        let mut num_group = 0;
        let mut b_read_more = true;

        while b_read_more && num_group < max_num_groups {
            let read = self.get_n::<u32>(n)?;
            value += read;
            b_read_more = self.get()?;
            if b_read_more {
                value = (value + 1) << n;
                num_group += 1;
            }
        }

        Ok(value)
    }

    #[inline(always)]
    pub fn seek(&mut self, offset: i64) -> io::Result<u64> {
        if (offset < 0 && self.position()? as i64 + offset >= 0)
            || (offset >= 0 && self.available()? as i64 >= offset)
        {
            return self.bs.seek_bits(SeekFrom::Current(offset));
        }

        Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            format!(
                "seek({}): out of bounds bits at {}",
                offset,
                self.position()?
            ),
        ))
    }

    #[inline(always)]
    // TODO: byte boundary
    pub fn parity_check_for_last_n_bits(&mut self, len: u64) -> io::Result<u8> {
        let position = self.position()?;

        self.seek(-(len as i64))?;

        let bytes_len = (len >> 3) as usize;

        let parity = if bytes_len <= STACK_BUF_SIZE {
            let mut stack_buf = [0u8; STACK_BUF_SIZE];
            let buf = &mut stack_buf[..bytes_len];
            self.bs.read_bytes(buf)?;
            buf.iter().fold(0, |acc, x| acc ^ x)
        } else {
            let mut heap_buf = vec![0; bytes_len];
            self.bs.read_bytes(&mut heap_buf)?;
            heap_buf.iter().fold(0, |acc, x| acc ^ x)
        };

        self.bs.seek_bits(SeekFrom::Start(position))?;

        Ok(parity)
    }

    #[inline(always)]
    pub fn parity_check_nibble_for_last_n_bits(&mut self, len: u64) -> io::Result<u8> {
        let mut parity = self.parity_check_for_last_n_bits(len)?;

        parity ^= parity >> 4;
        parity &= 0xF;

        Ok(parity)
    }

    #[inline(always)]
    pub fn crc8_check(&mut self, crc: &Crc8, start: u64, len: u64) -> io::Result<u8> {
        let position = self.position()?;

        if start + len > self.len {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "crc8_check: out of bounds bits",
            ));
        }

        self.bs.seek_bits(SeekFrom::Start(start))?;

        let mut checksum = crc.init;

        let prefix_len = start & 7;
        let suffix_len = (len - prefix_len) & 7;
        let middle_len = (len - prefix_len - suffix_len) as usize;

        if prefix_len != 0 {
            let prefix: u8 = self.bs.read_var(prefix_len as u32)?;
            checksum = crc8(crc.poly, checksum, prefix_len as usize) ^ prefix;
        }

        let bytes_len = middle_len >> 3;
        if bytes_len <= STACK_BUF_SIZE {
            let mut stack_buf = [0u8; STACK_BUF_SIZE];
            let buf = &mut stack_buf[..bytes_len];
            self.bs.read_bytes(buf)?;
            checksum = crc.update(checksum, buf);
        } else {
            let mut heap_buf = vec![0; bytes_len];
            self.bs.read_bytes(&mut heap_buf)?;
            checksum = crc.update(checksum, &heap_buf);
        };

        if suffix_len != 0 {
            let suffix: u8 = self.bs.read_var(suffix_len as u32)?;
            checksum = crc8(crc.poly, checksum, suffix_len as usize) ^ suffix;
        }

        self.bs.seek_bits(SeekFrom::Start(position))?;

        Ok(checksum)
    }

    #[inline(always)]
    pub fn crc16_check(&mut self, crc: &Crc16, start: u64, len: u64) -> io::Result<u16> {
        let position = self.position()?;

        if start + len > self.len {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "crc8_check: out of bounds bits",
            ));
        }

        self.bs.seek_bits(SeekFrom::Start(start))?;

        let mut checksum = crc.init;

        let prefix_len = start & 7;
        let suffix_len = (len - prefix_len) & 7;
        let middle_len = (len - prefix_len - suffix_len) as usize;

        if prefix_len != 0 {
            let prefix: u16 = self.bs.read_var(prefix_len as u32)?;
            checksum = crc16(crc.poly, checksum, prefix_len as usize) ^ prefix;
        }

        let bytes_len = middle_len >> 3;
        if bytes_len <= STACK_BUF_SIZE {
            let mut stack_buf = [0u8; STACK_BUF_SIZE];
            let buf = &mut stack_buf[..bytes_len];
            self.bs.read_bytes(buf)?;
            checksum = crc.update(checksum, buf);
        } else {
            let mut heap_buf = vec![0; bytes_len];
            self.bs.read_bytes(&mut heap_buf)?;
            checksum = crc.update(checksum, &heap_buf);
        };

        if suffix_len != 0 {
            let suffix: u16 = self.bs.read_var(suffix_len as u32)?;
            checksum = crc16(crc.poly, checksum, suffix_len as usize) ^ suffix;
        }

        self.bs.seek_bits(SeekFrom::Start(position))?;

        Ok(checksum)
    }

    #[inline(always)]
    pub fn align_16bit(&mut self) -> io::Result<()> {
        self.bs.byte_align();

        let position = self.bs.position_in_bits()?;
        if position & 15 > 0 {
            self.skip_n(8)?;
        }

        Ok(())
    }

    #[inline(always)]
    pub fn available(&mut self) -> io::Result<u64> {
        self.bs.position_in_bits().map(|pos| self.len - pos)
    }

    #[inline(always)]
    pub fn skip_n(&mut self, n: u32) -> io::Result<()> {
        // Skip bounds check for small skips - bitstream_io handles EOF internally
        if n <= 64 {
            self.bs.skip(n)
        } else {
            // For larger skips, keep bounds check
            self.available().and_then(|avail| {
                if n as u64 > avail {
                    Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "skip_n: out of bounds bits",
                    ))
                } else {
                    self.bs.skip(n)
                }
            })
        }
    }

    #[inline(always)]
    pub fn position(&mut self) -> io::Result<u64> {
        self.bs.position_in_bits()
    }
}

/// Unsigned integers [`SliceBitReader::get_n`] reads into.
pub trait UnsignedBits: Copy {
    const BITS: u32;
    fn from_bits(bits: u64) -> Self;
}

/// Signed integers [`SliceBitReader::get_s`] reads into.
pub trait SignedBits: Copy {
    const BITS: u32;
    fn from_bits(bits: i64) -> Self;
}

macro_rules! impl_bits {
    ($trait:ident, $wide:ty: $($t:ty),*) => {$(
        impl $trait for $t {
            const BITS: u32 = <$t>::BITS;
            #[inline(always)]
            fn from_bits(bits: $wide) -> Self {
                bits as $t
            }
        }
    )*};
}

impl_bits!(UnsignedBits, u64: u8, u16, u32, u64, usize);
impl_bits!(SignedBits, i64: i8, i16, i32, i64);

/// A Huffman table indexed by the next nine bits of the stream: the symbol those
/// bits start with, and the length of its code.
pub(crate) type HuffmanTable = [(i8, u8); 512];

/// The three tables share one shape. A `1` starts the codes of the smallest
/// symbols, followed by `small_bits` bits that are the symbol. A `0` starts the
/// others: a sign bit, then up to five zeros closed by a `1`, the number of zeros
/// giving the magnitude. Six zeros make the nine-bit deepest code, whose ninth bit
/// the tables ignore (see [`HUFFMAN_DEEPEST_CODE`]). The test below holds these
/// tables to the trees above, code for code.
const fn huffman_table(positive_base: i8, small_bits: u32) -> HuffmanTable {
    let mut table = [(0i8, 0u8); 512];
    let mut code = 0;
    while code < 512 {
        let bits = code as u32;
        table[code] = if bits & 0x100 != 0 {
            let symbol = (bits >> (8 - small_bits)) & ((1 << small_bits) - 1);
            (symbol as i8, 1 + small_bits as u8)
        } else {
            let rest = bits & 0x7F;
            let zeros = if rest == 0 {
                7
            } else {
                rest.leading_zeros() - 25
            };
            let zeros = if zeros > 6 { 6 } else { zeros };
            let length = if zeros == 6 { 9 } else { 3 + zeros as u8 };
            let symbol = if bits & 0x80 != 0 {
                positive_base + zeros as i8
            } else {
                -1 - zeros as i8
            };
            (symbol, length)
        };
        code += 1;
    }
    table
}

static HUFFMAN_TABLE_1: HuffmanTable = huffman_table(4, 2);
static HUFFMAN_TABLE_2: HuffmanTable = huffman_table(2, 1);
static HUFFMAN_TABLE_3: HuffmanTable = huffman_table(1, 0);

/// The table of a Huffman type, which type 0 and the types there are not lack.
pub(crate) fn huffman_table_of(huff_type: usize) -> Option<&'static HuffmanTable> {
    match huff_type {
        1 => Some(&HUFFMAN_TABLE_1),
        2 => Some(&HUFFMAN_TABLE_2),
        3 => Some(&HUFFMAN_TABLE_3),
        _ => None,
    }
}

/// The next bits of a stream held in a word, for a run of short reads.
///
/// A [`SliceBitReader`] loads its word from the slice at every read, so each read
/// waits on that load before it can tell the next one where to look. Here the
/// word is carried from one read to the next, and the load that refills it is one
/// that the read after it waits for, not this one: a run of Huffman codes, each
/// placing the next, goes about twice as fast.
///
/// A read is two steps. [`load`](Self::load) fetches the refill, and fails near
/// the end of the data, where the caller goes back to the reader. The caller
/// then takes what it wants from the top of [`bits`](Self::bits), of which at
/// least 33 are stream bits, and [`consume`](Self::consume)s at most 31 of them.
#[derive(Clone)]
pub(crate) struct BitWindow<'a> {
    data: &'a [u8],
    /// The stream from the position on, most significant bit first. The top
    /// `count` bits are stream bits, and so is every bit below them that is set.
    bits: u64,
    count: u32,
    /// The byte the stream bits of `bits` end before.
    byte: usize,
}

impl BitWindow<'_> {
    /// The stream from the position on, in the high bits of the word.
    #[inline(always)]
    pub(crate) fn bits(&self) -> u64 {
        self.bits
    }

    /// The eight bytes that refill the window, if the data has them.
    #[inline(always)]
    pub(crate) fn load(&self) -> Option<u64> {
        let bytes = self.data.get(self.byte..self.byte + 8)?;

        Some(u64::from_be_bytes(bytes.try_into().unwrap()))
    }

    /// Move past `n` bits, at most 31, and refill with what [`load`](Self::load)
    /// returned.
    #[inline(always)]
    pub(crate) fn consume(&mut self, loaded: u64, n: u32) {
        debug_assert!(n <= 31);

        // The refill lands below the bits there are, and the bytes it is counted
        // for are the whole ones: the bits of the next byte it also brings are
        // stream bits, which the next refill brings again.
        let refilled = self.bits | (loaded >> self.count);

        self.byte += ((63 - self.count) >> 3) as usize;
        self.bits = refilled << n;
        self.count = (self.count | 56) - n;
    }

    /// The next `n` bits, at most 31, or `None` near the end of the data.
    #[inline(always)]
    pub(crate) fn take(&mut self, n: u32) -> Option<u64> {
        if n == 0 {
            return Some(0);
        }

        let loaded = self.load()?;
        let value = self.bits >> (64 - n);

        self.consume(loaded, n);
        Some(value)
    }

    /// The position in the stream, in bits, unless the window never opened.
    #[inline(always)]
    fn position(&self) -> Option<u64> {
        (self.count != 0).then(|| ((self.byte as u64) << 3) - self.count as u64)
    }
}

/// A bit reader over a byte slice, most significant bit first.
///
/// It reads up to 57 bits with one unaligned big-endian load, where a reader over
/// [`io::Read`] refills bit by bit, and decodes a Huffman code with one table
/// lookup instead of walking a tree. Reads past the end fail with
/// [`io::ErrorKind::UnexpectedEof`], as they did through [`BitstreamIoReader`].
#[derive(Debug, Default, Clone)]
pub struct SliceBitReader<'a> {
    data: &'a [u8],
    /// Position, in bits from the start of `data`.
    pos: u64,
    /// Length of `data`, in bits.
    len: u64,
}

impl<'a> SliceBitReader<'a> {
    pub fn from_slice(data: &'a [u8]) -> Self {
        Self {
            data,
            pos: 0,
            len: (data.len() as u64) << 3,
        }
    }

    /// The bits from `pos` on, in the high bits of the word: at least 57 valid,
    /// zeros past the end of the data.
    #[inline(always)]
    fn peek_at(&self, pos: u64) -> u64 {
        let byte = (pos >> 3) as usize;
        let word = match self.data.get(byte..byte + 8) {
            Some(bytes) => u64::from_be_bytes(bytes.try_into().unwrap()),
            None => {
                let mut buf = [0u8; 8];
                if let Some(tail) = self.data.get(byte..) {
                    buf[..tail.len()].copy_from_slice(tail);
                }
                u64::from_be_bytes(buf)
            }
        };
        word << (pos & 7)
    }

    /// A [`BitWindow`] on the stream at the position, for a run of short reads.
    /// The reader stays where it is until [`resume`](Self::resume).
    #[inline(always)]
    pub(crate) fn window(&self) -> BitWindow<'a> {
        let mut window = BitWindow {
            data: &self.data[..(self.len >> 3) as usize],
            bits: 0,
            count: 0,
            byte: (self.pos >> 3) as usize,
        };

        // What the first load takes in, less the bits of its first byte that
        // are already read. Past the end there is nothing to load, and the
        // window stays empty, which every read from it reports.
        if let Some(loaded) = window.load() {
            window.consume(loaded, (self.pos & 7) as u32);
        }

        window
    }

    /// Move to where `window` has read to.
    #[inline(always)]
    pub(crate) fn resume(&mut self, window: &BitWindow<'a>) {
        if let Some(pos) = window.position() {
            self.pos = pos;
        }
    }

    #[cold]
    fn out_of_bounds(&self, what: &str, n: u64) -> io::Error {
        io::Error::new(
            io::ErrorKind::UnexpectedEof,
            format!("{what}({n}): out of bounds bits at {}", self.pos),
        )
    }

    #[cold]
    fn too_wide(what: &str, n: u32) -> io::Error {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{what}({n}): excessive bits for type read"),
        )
    }

    /// The next `n` bits (at most 64), as the low bits of the result.
    #[inline(always)]
    fn read_bits(&mut self, n: u32, what: &str) -> io::Result<u64> {
        if self.pos + n as u64 > self.len {
            return Err(self.out_of_bounds(what, n as u64));
        }
        let bits = if n == 0 {
            0
        } else if n <= 57 {
            self.peek_at(self.pos) >> (64 - n)
        } else {
            let high = self.peek_at(self.pos) >> 32;
            let low = self.peek_at(self.pos + 32) >> (96 - n);
            (high << (n - 32)) | low
        };
        self.pos += n as u64;
        Ok(bits)
    }

    #[inline(always)]
    pub fn get(&mut self) -> io::Result<bool> {
        self.read_bits(1, "get").map(|bit| bit != 0)
    }

    #[inline(always)]
    pub fn get_n<I: UnsignedBits>(&mut self, n: u32) -> io::Result<I> {
        if n > I::BITS {
            return Err(Self::too_wide("get_n", n));
        }
        self.read_bits(n, "get_n").map(I::from_bits)
    }

    #[inline(always)]
    pub fn get_s<S: SignedBits>(&mut self, n: u32) -> io::Result<S> {
        if n == 0 || n > S::BITS {
            return Err(Self::too_wide("get_s", n));
        }
        let bits = self.read_bits(n, "get_s")?;
        let shift = 64 - n;
        Ok(S::from_bits(((bits << shift) as i64) >> shift))
    }

    #[inline(always)]
    pub fn get_huffman(&mut self, huff_type: usize) -> io::Result<i32> {
        let table = match huff_type {
            1 => &HUFFMAN_TABLE_1,
            2 => &HUFFMAN_TABLE_2,
            3 => &HUFFMAN_TABLE_3,
            _ => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "get_huffman: unsupported huff_type",
                ));
            }
        };
        let (symbol, length) = table[(self.peek_at(self.pos) >> 55) as usize];
        if self.pos + length as u64 > self.len {
            return Err(self.out_of_bounds("get_huffman", length as u64));
        }
        self.pos += length as u64;
        Ok(symbol as i32)
    }

    #[inline(always)]
    pub fn get_variable_bits_max(&mut self, n: u32, max_num_groups: u32) -> io::Result<u32> {
        let mut value = 0;
        let mut num_group = 0;
        let mut b_read_more = true;

        while b_read_more && num_group < max_num_groups {
            let read = self.get_n::<u32>(n)?;
            value += read;
            b_read_more = self.get()?;
            if b_read_more {
                value = (value + 1) << n;
                num_group += 1;
            }
        }

        Ok(value)
    }

    #[inline(always)]
    pub fn seek(&mut self, offset: i64) -> io::Result<u64> {
        if (offset < 0 && self.pos as i64 + offset >= 0)
            || (offset >= 0 && (self.len - self.pos) as i64 >= offset)
        {
            self.pos = (self.pos as i64 + offset) as u64;
            return Ok(self.pos);
        }

        Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            format!("seek({offset}): out of bounds bits at {}", self.pos),
        ))
    }

    /// The bytes of `len` bits from `start`, the first `len >> 3` of them handed to
    /// `f` as one slice: borrowed when `start` is byte aligned, gathered otherwise.
    fn with_bytes<T>(&self, start: u64, len: u64, f: impl FnOnce(&[u8]) -> T) -> T {
        let bytes_len = (len >> 3) as usize;
        if start & 7 == 0 {
            let first = (start >> 3) as usize;
            return f(&self.data[first..first + bytes_len]);
        }
        let gather = |buf: &mut [u8]| {
            for (i, byte) in buf.iter_mut().enumerate() {
                *byte = (self.peek_at(start + 8 * i as u64) >> 56) as u8;
            }
        };
        if bytes_len <= STACK_BUF_SIZE {
            let mut stack_buf = [0u8; STACK_BUF_SIZE];
            let buf = &mut stack_buf[..bytes_len];
            gather(buf);
            f(buf)
        } else {
            let mut heap_buf = vec![0; bytes_len];
            gather(&mut heap_buf);
            f(&heap_buf)
        }
    }

    #[inline(always)]
    // TODO: byte boundary
    pub fn parity_check_for_last_n_bits(&mut self, len: u64) -> io::Result<u8> {
        if len > self.pos {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                format!("seek(-{len}): out of bounds bits at {}", self.pos),
            ));
        }

        Ok(self.with_bytes(self.pos - len, len, |buf| {
            buf.iter().fold(0, |acc, x| acc ^ x)
        }))
    }

    #[inline(always)]
    pub fn parity_check_nibble_for_last_n_bits(&mut self, len: u64) -> io::Result<u8> {
        let mut parity = self.parity_check_for_last_n_bits(len)?;

        parity ^= parity >> 4;
        parity &= 0xF;

        Ok(parity)
    }

    /// The `n` bits at `at`, which the caller has checked lie within the data.
    #[inline(always)]
    fn bits_at(&self, at: u64, n: u64) -> u64 {
        self.peek_at(at) >> (64 - n)
    }

    #[inline(always)]
    pub fn crc8_check(&mut self, crc: &Crc8, start: u64, len: u64) -> io::Result<u8> {
        if start + len > self.len {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "crc8_check: out of bounds bits",
            ));
        }

        let mut checksum = crc.init;

        let prefix_len = start & 7;
        let suffix_len = (len - prefix_len) & 7;
        let middle_len = len - prefix_len - suffix_len;

        if prefix_len != 0 {
            let prefix = self.bits_at(start, prefix_len) as u8;
            checksum = crc8(crc.poly, checksum, prefix_len as usize) ^ prefix;
        }

        let middle_start = start + prefix_len;
        checksum = self.with_bytes(middle_start, middle_len, |buf| crc.update(checksum, buf));

        if suffix_len != 0 {
            let suffix = self.bits_at(middle_start + middle_len, suffix_len) as u8;
            checksum = crc8(crc.poly, checksum, suffix_len as usize) ^ suffix;
        }

        Ok(checksum)
    }

    #[inline(always)]
    pub fn crc16_check(&mut self, crc: &Crc16, start: u64, len: u64) -> io::Result<u16> {
        if start + len > self.len {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "crc8_check: out of bounds bits",
            ));
        }

        let mut checksum = crc.init;

        let prefix_len = start & 7;
        let suffix_len = (len - prefix_len) & 7;
        let middle_len = len - prefix_len - suffix_len;

        if prefix_len != 0 {
            let prefix = self.bits_at(start, prefix_len) as u16;
            checksum = crc16(crc.poly, checksum, prefix_len as usize) ^ prefix;
        }

        let middle_start = start + prefix_len;
        checksum = self.with_bytes(middle_start, middle_len, |buf| crc.update(checksum, buf));

        if suffix_len != 0 {
            let suffix = self.bits_at(middle_start + middle_len, suffix_len) as u16;
            checksum = crc16(crc.poly, checksum, suffix_len as usize) ^ suffix;
        }

        Ok(checksum)
    }

    #[inline(always)]
    pub fn align_16bit(&mut self) -> io::Result<()> {
        self.pos = (self.pos + 7) & !7;

        if self.pos & 15 > 0 {
            self.skip_n(8)?;
        }

        Ok(())
    }

    #[inline(always)]
    pub fn available(&mut self) -> io::Result<u64> {
        Ok(self.len - self.pos)
    }

    #[inline(always)]
    pub fn skip_n(&mut self, n: u32) -> io::Result<()> {
        if self.pos + n as u64 > self.len {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "skip_n: out of bounds bits",
            ));
        }
        self.pos += n as u64;
        Ok(())
    }

    #[inline(always)]
    pub fn position(&mut self) -> io::Result<u64> {
        Ok(self.pos)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A window reads the bits the reader reads, whatever the widths and wherever
    /// it opens, and hands the reader back the position it reached.
    #[test]
    fn window_reads_match_the_reader() {
        let mut seed = 0x2545_f491_4f6c_dd1du64;
        let mut random = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        let data: Vec<u8> = (0..257).map(|_| random() as u8).collect();

        for start in [0, 1, 7, 8, 13, 250] {
            let mut reader = SliceBitReader::from_slice(&data);
            reader.skip_n(start).unwrap();
            let mut expected = reader.clone();
            let mut window = reader.window();

            while let Some(loaded) = window.load() {
                let n = (random() % 32) as u32;
                // By the two steps of a read, and by the two at once.
                let value = if random() & 1 == 0 {
                    let value = if n == 0 { 0 } else { window.bits() >> (64 - n) };

                    window.consume(loaded, n);
                    value
                } else {
                    window.take(n).unwrap()
                };

                assert_eq!(value, expected.get_n::<u32>(n).unwrap() as u64, "{n} bits");

                let mut resumed = reader.clone();
                resumed.resume(&window);
                assert_eq!(resumed.position().unwrap(), expected.position().unwrap());
            }

            // It gives up only where a refill would run past the data.
            assert!(expected.position().unwrap() + 127 >= (data.len() as u64) << 3);
        }

        // On fewer than eight bytes it never opens, and moves the reader nowhere.
        let mut reader = SliceBitReader::from_slice(&data[..5]);
        reader.skip_n(3).unwrap();
        let window = reader.window();
        assert!(window.load().is_none());
        reader.resume(&window);
        assert_eq!(reader.position().unwrap(), 3);
    }

    /// Every nine-bit pattern decodes through the table to the symbol and code
    /// length the trees give it.
    #[test]
    fn huffman_tables_match_the_trees() {
        fn tree<T: bitstream_io::huffman::FromBits<Symbol = i32>>(code: u16) -> (i32, u64) {
            let bytes = (code << 7).to_be_bytes();
            let mut reader = BitReader::endian(io::Cursor::new(&bytes[..]), BigEndian);
            let symbol = reader.read_huffman::<T>().unwrap();
            (symbol, reader.position_in_bits().unwrap())
        }

        for code in 0..512u16 {
            let bytes = (code << 7).to_be_bytes();
            for (huff_type, expected) in [
                (1, tree::<HuffTree1>(code)),
                (2, tree::<HuffTree2>(code)),
                (3, tree::<HuffTree3>(code)),
            ] {
                let mut reader = SliceBitReader::from_slice(&bytes);
                let symbol = reader.get_huffman(huff_type).unwrap();
                assert_eq!(
                    (symbol, reader.position().unwrap()),
                    expected,
                    "table {huff_type}, code {code:09b}"
                );
            }
        }
    }

    /// Reads of every width at every bit offset agree with `bitstream_io`.
    #[test]
    fn reads_match_bitstream_io() {
        let data: Vec<u8> = (0..64u32)
            .map(|i| (i.wrapping_mul(0x9E) ^ 0x5A) as u8)
            .collect();
        for start in 0..16u32 {
            for n in 1..=64u32 {
                let mut ours = SliceBitReader::from_slice(&data);
                let mut theirs =
                    BitstreamIoReader::new(io::Cursor::new(&data[..]), data.len() as u64);
                ours.skip_n(start).unwrap();
                theirs.skip_n(start).unwrap();
                assert_eq!(
                    ours.get_n::<u64>(n).unwrap(),
                    theirs.get_n::<u64>(n).unwrap(),
                    "u{n} at {start}"
                );
                if n <= 32 {
                    assert_eq!(
                        ours.get_s::<i32>(n).unwrap(),
                        theirs.get_s::<i32>(n).unwrap(),
                        "i{n} at {}",
                        start + n
                    );
                }
            }
        }
    }

    #[test]
    fn reading_past_the_end_fails() {
        let mut reader = SliceBitReader::from_slice(&[0xFF]);
        assert_eq!(reader.get_n::<u8>(7).unwrap(), 0x7F);
        assert_eq!(
            reader.get_n::<u8>(2).unwrap_err().kind(),
            io::ErrorKind::UnexpectedEof
        );
        assert!(reader.get().unwrap());
        assert_eq!(
            reader.get_huffman(3).unwrap_err().kind(),
            io::ErrorKind::UnexpectedEof
        );
    }
}
