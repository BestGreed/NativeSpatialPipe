// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <array>
#include <atomic>
#include <cstddef>

// One serialized producer, one consumer. Storage is allocated with its owner,
// never in push/pop. Indices are monotonic; every array slot is usable.
template <class T, size_t Capacity> struct SpscRing {
  static_assert(Capacity && !(Capacity & (Capacity - 1)));
  alignas(64) std::atomic<size_t> written{0};
  alignas(64) std::atomic<size_t> read{0};
  std::array<T, Capacity> data{};
  bool push(const T &value) {
    size_t w = written.load(std::memory_order_relaxed);
    if (w - read.load(std::memory_order_acquire) == Capacity)
      return false;
    data[w & (Capacity - 1)] = value;
    written.store(w + 1, std::memory_order_release);
    return true;
  }
  bool peek(T &value, size_t offset = 0) const {
    size_t r = read.load(std::memory_order_relaxed);
    if (offset >= written.load(std::memory_order_acquire) - r)
      return false;
    value = data[(r + offset) & (Capacity - 1)];
    return true;
  }
  bool pop(T &value) {
    size_t r = read.load(std::memory_order_relaxed);
    if (r == written.load(std::memory_order_acquire))
      return false;
    value = data[r & (Capacity - 1)];
    read.store(r + 1, std::memory_order_release);
    return true;
  }
  // Producer-side size (the caller must be the serialized producer).
  size_t producerSize() const {
    return written.load(std::memory_order_relaxed) -
           read.load(std::memory_order_acquire);
  }
};
