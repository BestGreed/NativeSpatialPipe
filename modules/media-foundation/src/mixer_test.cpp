// SPDX-License-Identifier: Apache-2.0
// Differential reference deliberately retains the original sample-first loop.
#include "decoder.h"
#include "spatial_mixer.h"
#include "spsc_ring.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <memory>
#include <new>
#include <thread>
#include <vector>
static std::atomic<uint64_t> allocations{0};
void *operator new(size_t n) {
  allocations++;
  if (auto *p = malloc(n ? n : 1))
    return p;
  throw std::bad_alloc();
}
void operator delete(void *p) noexcept { free(p); }
void operator delete(void *p, size_t) noexcept { free(p); }
struct Frame {
  NspFrame view{};
  uint64_t start = 0;
  NspOwnedFrame *owner = nullptr;
};
static bool fallbackBeds = false;
static uint64_t key(uint8_t label, uint32_t id) {
  if (label == 24)
    return (1ull << 32) | id;
  if (fallbackBeds)
    return (1ull << 32) | (0x80000000u + label);
  return label == 23 ? 3 : label;
}
static std::array<double, 3> fixed(uint8_t label) {
  return {label * .01, .5, -.25};
}
struct Reference {
  using Pose = SpatialMixer::Pose;
  std::map<uint32_t, Pose> poses;
  std::map<uint64_t, std::vector<float>> pcm;
  std::map<uint64_t, std::array<float, 3>> positions;
  std::array<uint8_t, 160> labels{};
  std::array<uint32_t, 160> ids{};
  uint32_t channels = 0;
  std::atomic<uint64_t> nonzero{0};
  uint64_t calculations = 0;
  static double t(int64_t s, const Pose &p) {
    return p.ramp ? std::clamp(double(s - p.at) / p.ramp, 0., 1.) : 1.;
  }
  static std::array<double, 3> positionAt(const Pose &p, int64_t s) {
    double b = t(s, p);
    return {p.from[0] + (p.position[0] - p.from[0]) * b,
            p.from[1] + (p.position[1] - p.from[1]) * b,
            p.from[2] + (p.position[2] - p.from[2]) * b};
  }
  static double gainAt(const Pose &p, int64_t s) {
    return p.fromGain + (p.gain - p.fromGain) * t(s, p);
  }
  static std::array<float, 3> windows(const std::array<double, 3> &p) {
    return {float(p[0]), float(p[2]), float(-p[1])};
  }
  void apply(const Frame &frame, uint32_t &event, uint64_t sample) {
    auto &f = frame.view;
    while (event < f.event_count && f.events[event].sample_pos <= sample) {
      auto &e = f.events[event++];
      auto &p = poses[e.id];
      p.from = positionAt(p, e.sample_pos);
      p.fromGain = gainAt(p, e.sample_pos);
      p.position = (e.flags & 1)
                       ? std::array<double, 3>{e.pos[0], e.pos[1], e.pos[2]}
                       : p.from;
      p.gain = pow(10., e.gain_db / 20.);
      p.at = e.sample_pos;
      p.ramp = e.ramp;
      p.size = {e.size[0], e.size[1], e.size[2]};
    }
  }
  void cycle(const std::vector<Frame> &frames, size_t &index,
             std::vector<uint32_t> &events, int64_t cursor, uint32_t n) {
    positions.clear();
    for (auto &[id, buffer] : pcm)
      buffer.assign(n, 0);
    for (uint32_t k = 0; k < n; k++) {
      int64_t sample = cursor + k;
      if (sample < 0)
        continue;
      while (index < frames.size() &&
             frames[index].start + frames[index].view.samples <=
                 uint64_t(sample)) {
        apply(frames[index], events[index],
              frames[index].start + frames[index].view.samples);
        index++;
      }
      if (index == frames.size() || uint64_t(sample) < frames[index].start)
        continue;
      auto &frame = frames[index];
      auto &f = frame.view;
      apply(frame, events[index], sample);
      bool known = true;
      for (uint32_t ch = 0; ch < f.channels; ch++)
        known &= f.labels[ch] != 255;
      if (known) {
        channels = f.channels;
        for (uint32_t ch = 0; ch < channels; ch++) {
          labels[ch] = f.labels[ch];
          ids[ch] = UINT32_MAX;
        }
        for (uint32_t j = 0; j < f.object_count; j++)
          ids[f.objects[j].channel] = f.objects[j].id;
      }
      if (!known && channels != f.channels) {
        for (size_t future = index; future < frames.size(); future++) {
          auto &next = frames[future].view;
          bool complete = next.channels == f.channels;
          for (uint32_t ch = 0; ch < next.channels; ch++)
            complete &= next.labels[ch] != 255;
          if (complete) {
            channels = next.channels;
            for (uint32_t ch = 0; ch < channels; ch++) {
              labels[ch] = next.labels[ch];
              ids[ch] = UINT32_MAX;
            }
            for (uint32_t j = 0; j < next.object_count; j++)
              ids[next.objects[j].channel] = next.objects[j].id;
            break;
          }
        }
      }
      assert(channels == f.channels);
      for (uint32_t ch = 0; ch < f.channels; ch++) {
        auto id = key(labels[ch], ids[ch]);
        auto [it, inserted] = pcm.try_emplace(id);
        if (inserted)
          it->second.assign(n, 0);
        if (labels[ch] == 24 || fallbackBeds) {
          positions[id] =
              windows(labels[ch] == 24 ? positionAt(poses[ids[ch]], sample)
                                       : fixed(labels[ch]));
          calculations++;
        }
        float value =
            f.pcm[size_t(sample - frame.start) * f.channels + ch] / 8388608.f;
        if (labels[ch] == 24)
          value *= gainAt(poses[ids[ch]], sample);
        it->second[k] += value;
        if (value != 0)
          nonzero++;
      }
    }
  }
};
struct Buffers {
  std::vector<float> data;
  Buffers() : data(size_t(SpatialMixer::maxSignals) * 4096) {};
};
static void optimizedCycle(SpatialMixer &mix, Buffers &buffers,
                           const std::vector<Frame> &frames, size_t &index,
                           std::vector<uint32_t> &events, int64_t cursor,
                           uint32_t n, uint64_t &nonzero) {
  mix.beginCycle();
  auto prepare = [&](SpatialMixer::Slot &s) {
    s.buffer = buffers.data.data() + size_t(&s - mix.slots.data()) * 4096;
    memset(s.buffer, 0, n * 4);
  };
  for (uint32_t i = 0; i < mix.slotCount; i++)
    prepare(mix.slots[i]);
  uint32_t k = 0;
  while (k < n) {
    int64_t sample = cursor + k;
    if (sample < 0) {
      k += uint32_t(std::min<uint64_t>(n - k, -sample));
      continue;
    }
    while (index < frames.size() &&
           frames[index].start + frames[index].view.samples <=
               uint64_t(sample)) {
      assert(mix.apply(frames[index].view, events[index],
                       frames[index].start + frames[index].view.samples));
      index++;
    }
    if (index == frames.size())
      break;
    const auto &frame = frames[index];
    const auto &f = frame.view;
    if (uint64_t(sample) < frame.start) {
      k += uint32_t(std::min<uint64_t>(n - k, frame.start - sample));
      continue;
    }
    auto future = [&](size_t offset, NspFrame &out) {
      if (index + offset + 1 >= frames.size())
        return false;
      out = frames[index + offset + 1].view;
      return true;
    };
    auto resolve = [&](uint8_t label, uint32_t id) {
      auto *s = mix.slot(key(label, id), label == 24 || fallbackBeds, label);
      if (s && !s->buffer)
        prepare(*s);
      return s;
    };
    assert(mix.prepare(f, frame.start, future, resolve, fixed));
    uint32_t offset = uint32_t(sample - frame.start),
             count = std::min(n - k, f.samples - offset);
    assert(
        mix.render(f, frame.start, events[index], offset, count, k, nonzero));
    k += count;
  }
  mix.finishCycle();
}
static void differential(const std::vector<Frame> &frames, uint32_t period,
                         const char *label) {
  auto mix = std::make_unique<SpatialMixer>();
  Buffers buffers;
  Reference reference;
  size_t oldIndex = 0, newIndex = 0;
  std::vector<uint32_t> oldEvents(frames.size()), newEvents(frames.size());
  uint64_t nonzero = 0, cycles = 0;
  uint64_t end = frames.back().start + frames.back().view.samples;
  for (int64_t cursor = -23; cursor < int64_t(end + period); cursor += period) {
    reference.cycle(frames, oldIndex, oldEvents, cursor, period);
    uint64_t before = allocations.load();
    optimizedCycle(*mix, buffers, frames, newIndex, newEvents, cursor, period,
                   nonzero);
    assert(allocations.load() == before);
    assert(oldIndex == newIndex && oldEvents == newEvents);
    assert(reference.nonzero == nonzero);
    for (uint32_t i = 0; i < mix->slotCount; i++) {
      auto &s = mix->slots[i];
      auto found = reference.pcm.find(s.key);
      assert(found != reference.pcm.end());
      assert(memcmp(s.buffer, found->second.data(), period * 4) == 0);
      auto position = reference.positions.find(s.key);
      assert(s.positioned == (position != reference.positions.end()));
      if (s.positioned)
        assert(memcmp(s.position.data(), position->second.data(), 12) == 0);
    }
    for (auto &[id, p] : reference.poses) {
      auto *next = mix->pose(id);
      assert(next);
      assert(next->value.size == p.size);
    }
    cycles++;
  }
  printf(
      "{\"case\":\"%s\",\"period\":%u,\"cycles\":%llu,\"pcmAndMetadataExact\":"
      "true,\"kernelHeapAllocations\":0,\"referencePositionCalculations\":%llu,"
      "\"optimizedPositionCalculations\":%llu,\"routeBuilds\":%llu,"
      "\"mappingChecks\":%llu}\n",
      label, period, cycles, reference.calculations, mix->positionCalculations,
      mix->routeBuilds, mix->mappingChecks);
}
struct Synthetic {
  std::vector<std::vector<int32_t>> pcm;
  std::vector<std::vector<uint8_t>> labels;
  std::vector<std::vector<NspObject>> objects;
  std::vector<std::vector<NspEvent>> events;
  std::vector<Frame> frames;
  Synthetic(uint32_t channels, uint32_t frameSamples, bool unknown,
            bool mutation) {
    uint32_t seed = 123;
    uint64_t at = 0;
    const uint32_t frameCount = 17;
    pcm.resize(frameCount);
    labels.resize(frameCount);
    objects.resize(frameCount);
    events.resize(frameCount);
    frames.resize(frameCount);
    for (uint32_t f = 0; f < frameCount; f++) {
      auto &data = pcm[f];
      data.resize(size_t(channels) * frameSamples);
      for (auto &v : data) {
        seed = seed * 1664525 + 1013904223;
        v = (seed % 17) ? int32_t(seed % 1000001) - 500000 : 0;
      }
      bool missing = unknown && f < 3;
      labels[f].assign(channels, missing ? 255 : 24);
      if (!missing)
        labels[f][0] = 3;
      uint32_t change = mutation && f >= 8 ? 10000 : 0;
      for (uint32_t ch = 1; ch < channels; ch++)
        if (!missing)
          objects[f].push_back({100 + ch * 17 + change, ch});
      if (!missing)
        for (uint32_t time : {0u, frameSamples / 2, frameSamples})
          for (uint32_t ch = 1; ch < channels; ch++) {
            NspEvent e{};
            e.id = 100 + ch * 17 + change;
            e.sample_pos = at + time;
            e.flags = (time == frameSamples / 2) ? 0 : 1;
            e.pos[0] = .1 * int(ch % 5) - .2;
            e.pos[1] = .75;
            e.pos[2] = .03 * f;
            e.gain_db = float(-int((f + ch) % 13));
            e.ramp = frameSamples + 7;
            e.size[0] = .1;
            e.size[1] = .2;
            e.size[2] = .3;
            events[f].push_back(e);
          }
      auto &frame = frames[f];
      frame.start = at;
      frame.view = {data.data(),
                    labels[f].data(),
                    objects[f].data(),
                    events[f].data(),
                    0,
                    48000,
                    frameSamples,
                    channels,
                    uint32_t(objects[f].size()),
                    uint32_t(events[f].size()),
                    0};
      at += frameSamples;
    }
  }
};
static void ringTest() {
  SpscRing<uint64_t, 8> small;
  uint64_t value = 0;
  for (uint64_t i = 0; i < 8; i++)
    assert(small.push(i));
  assert(!small.push(9));
  assert(small.peek(value, 7) && value == 7);
  assert(!small.peek(value, 8));
  for (uint64_t i = 0; i < 8; i++) {
    assert(small.pop(value));
    assert(value == i);
  }
  assert(!small.pop(value));
  auto queue = std::make_unique<SpscRing<uint64_t, 1024>>();
  constexpr uint64_t count = 400000;
  std::thread producer([&] {
    for (uint64_t i = 0; i < count; i++)
      while (!queue->push(i))
        std::this_thread::yield();
  });
  for (uint64_t i = 0; i < count; i++) {
    while (!queue->pop(value))
      std::this_thread::yield();
    assert(value == i);
  }
  producer.join();
  assert(queue->producerSize() == 0);
  puts("{\"spscSequenceWrapAndBackpressurePassed\":true}");
}
static void fixture(const wchar_t *dll, const wchar_t *path, uint32_t codec) {
  HMODULE module = LoadLibraryExW(dll, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
  assert(module);
  auto create = (decltype(&nsp_decoder_create))GetProcAddress(
      module, "nsp_decoder_create");
  auto destroy = (decltype(&nsp_decoder_destroy))GetProcAddress(
      module, "nsp_decoder_destroy");
  auto push =
      (decltype(&nsp_decoder_push))GetProcAddress(module, "nsp_decoder_push");
  auto front =
      (decltype(&nsp_decoder_front))GetProcAddress(module, "nsp_decoder_front");
  auto take =
      (decltype(&nsp_decoder_take))GetProcAddress(module, "nsp_decoder_take");
  auto release =
      (decltype(&nsp_frame_release))GetProcAddress(module, "nsp_frame_release");
  auto start =
      (decltype(&nsp_frame_start))GetProcAddress(module, "nsp_frame_start");
  assert(create && destroy && push && front && take && release && start);
  auto *decoder = create(codec);
  assert(decoder);
  std::ifstream file(path, std::ios::binary);
  char magic[4];
  uint32_t packets = 0;
  file.read(magic, 4);
  file.read((char *)&packets, 4);
  assert(!memcmp(magic, "BPK1", 4));
  std::vector<Frame> frames;
  for (uint32_t i = 0; i < packets; i++) {
    uint32_t n = 0;
    file.read((char *)&n, 4);
    assert(n && n <= 65536);
    std::vector<uint8_t> bytes(n);
    file.read((char *)bytes.data(), n);
    assert(file);
    assert(push(decoder, bytes.data(), n, 0) == 0);
    Frame frame;
    while (front(decoder, &frame.view) == 1) {
      frame.owner = take(decoder);
      assert(frame.owner);
      frame.start = start(frame.owner);
      frames.push_back(frame);
    }
  }
  destroy(decoder);
  assert(!frames.empty());
  for (uint32_t period : {64u, 480u, 4096u})
    differential(frames, period, codec == 1 ? "joc-fixture" : "truehd-fixture");
  for (auto &f : frames)
    release(f.owner);
  FreeLibrary(module);
}
static void benchmark() {
  using clock = std::chrono::steady_clock;
  for (uint32_t channels : {16u, 32u, 128u}) {
    Synthetic source(channels, 1536, false, false);
    auto &frames = source.frames;
    constexpr uint32_t n = 480;
    uint64_t oldNs = 0, newNs = 0;
    for (int repeat = 0; repeat < 4; repeat++) {
      Reference ref;
      size_t index = 0;
      std::vector<uint32_t> events(frames.size());
      auto begin = clock::now();
      for (int64_t at = 0; at < 1536 * 17; at += n)
        ref.cycle(frames, index, events, at, n);
      auto elapsed = clock::now() - begin;
      if (repeat)
        oldNs += std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed)
                     .count();
      auto mix = std::make_unique<SpatialMixer>();
      Buffers buffers;
      index = 0;
      events.assign(frames.size(), 0);
      uint64_t nonzero = 0;
      begin = clock::now();
      for (int64_t at = 0; at < 1536 * 17; at += n)
        optimizedCycle(*mix, buffers, frames, index, events, at, n, nonzero);
      elapsed = clock::now() - begin;
      if (repeat)
        newNs += std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed)
                     .count();
      assert(nonzero == ref.nonzero);
    }
    printf("{\"benchmarkSignals\":%u,\"referenceMs\":%.6f,\"optimizedMs\":%.6f,"
           "\"kernelSpeedup\":%.3f,\"syntheticKernelOnly\":true}\n",
           channels, oldNs / 3e6, newNs / 3e6, double(oldNs) / newNs);
  }
}
int wmain(int argc, wchar_t **argv) {
  ringTest();
  for (uint32_t channels : {16u, 32u, 128u})
    for (uint32_t period : {32u, 480u, 4096u}) {
      Synthetic data(channels, 40, true, channels == 16);
      differential(data.frames, period, "sparse-id-ramp-unknown-layout");
    }
  Synthetic beds(8, 40, false, false);
  for (size_t f = 0; f < beds.frames.size(); f++) {
    uint8_t labels[] = {0, 1, 2, 3, 23, 4, 5, 6};
    for (size_t ch = 0; ch < 8; ch++)
      beds.labels[f][ch] = labels[ch];
    beds.frames[f].view.object_count = 0;
    beds.frames[f].view.event_count = 0;
  }
  differential(beds.frames, 480, "static-bed-duplicate-lfe");
  fallbackBeds = true;
  differential(beds.frames, 480, "unsupported-bed-dynamic-position");
  fallbackBeds = false;
  Synthetic large(16, 1536, false, true);
  differential(large.frames, 480, "joc-size-gain-boundaries");
  if (argc == 4) {
    fixture(argv[1], argv[2], 1);
    fixture(argv[1], argv[3], 2);
  } else
    assert(argc == 1);
  benchmark();
  return 0;
}
