// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "../include/nsp_decoder.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>

// Device-independent PCM/metadata kernel. No heap allocation or atomic RMW in
// its sample loop. The caller owns the frame and the current output buffers.
struct SpatialMixer {
  static constexpr uint32_t maxSignals = 160, maxPoses = 512;
  struct Pose {
    std::array<double, 3> position{}, from{}, size{};
    double gain = 1, fromGain = 1;
    int64_t at = 0;
    uint32_t ramp = 0;
  };
  struct PoseEntry {
    uint32_t id = 0;
    Pose value{};
    int slot = -1;
  };
  struct Slot {
    uint64_t key = 0;
    uint32_t type = 0;
    bool dynamic = false;
    void *native = nullptr;
    float *buffer = nullptr;
    uint32_t bytes = 0;
    std::array<float, 3> position{};
    PoseEntry *pendingPose = nullptr;
    int64_t lastPositionSample = 0;
    bool positionPending = false, positioned = false;
  };
  struct Route {
    Slot *slot = nullptr;
    PoseEntry *pose = nullptr;
    std::array<float, 3> fixed{};
  };
  std::array<Slot, maxSignals> slots{};
  std::array<PoseEntry, maxPoses> poses{};
  std::array<Route, maxSignals> routes{};
  std::array<uint8_t, maxSignals> knownLabels{};
  std::array<uint32_t, maxSignals> knownIds{};
  uint32_t slotCount = 0, poseCount = 0, channels = 0;
  uint64_t mappedStart = UINT64_MAX;
  uint64_t mappingChecks = 0, routeBuilds = 0, positionCalculations = 0;
  static double blend(int64_t sample, int64_t at, uint32_t duration) {
    return duration ? std::clamp(double(sample - at) / duration, 0., 1.) : 1.;
  }
  static std::array<double, 3> positionAt(const Pose &p, int64_t sample) {
    double t = blend(sample, p.at, p.ramp);
    return {p.from[0] + (p.position[0] - p.from[0]) * t,
            p.from[1] + (p.position[1] - p.from[1]) * t,
            p.from[2] + (p.position[2] - p.from[2]) * t};
  }
  static double gainAt(const Pose &p, int64_t sample) {
    double t = blend(sample, p.at, p.ramp);
    return p.fromGain + (p.gain - p.fromGain) * t;
  }
  static std::array<float, 3> windowsPosition(const std::array<double, 3> &p) {
    return {float(p[0]), float(p[2]), float(-p[1])};
  }
  PoseEntry *pose(uint32_t id) {
    for (uint32_t i = 0; i < poseCount; i++)
      if (poses[i].id == id)
        return &poses[i];
    if (poseCount == maxPoses)
      return nullptr;
    auto &entry = poses[poseCount++];
    entry.id = id;
    return &entry;
  }
  Slot *slot(uint64_t key, bool dynamic, uint32_t type) {
    for (uint32_t i = 0; i < slotCount; i++)
      if (slots[i].key == key)
        return &slots[i];
    if (slotCount == maxSignals)
      return nullptr;
    auto &s = slots[slotCount++];
    s.key = key;
    s.dynamic = dynamic;
    s.type = type;
    return &s;
  }
  void finishPosition(Slot &s) {
    if (!s.positionPending)
      return;
    s.position =
        windowsPosition(positionAt(s.pendingPose->value, s.lastPositionSample));
    s.positionPending = false;
    positionCalculations++;
  }
  void beginCycle() {
    for (uint32_t i = 0; i < slotCount; i++) {
      auto &s = slots[i];
      s.buffer = nullptr;
      s.bytes = 0;
      s.positioned = false;
      s.positionPending = false;
    }
  }
  void finishCycle() {
    for (uint32_t i = 0; i < slotCount; i++)
      finishPosition(slots[i]);
  }
  bool apply(const NspFrame &f, uint32_t &event, uint64_t sample) {
    while (event < f.event_count && f.events[event].sample_pos <= sample) {
      const auto &e = f.events[event++];
      auto *entry = pose(e.id);
      if (!entry)
        return false;
      if (entry->slot >= 0) {
        auto &s = slots[entry->slot];
        if (s.positionPending && s.pendingPose == entry)
          finishPosition(s);
      }
      auto &p = entry->value;
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
    return true;
  }
  // future(i, view) only borrows queued frames while the caller remains their
  // sole consumer. resolve(label,id) returns a prepared current-cycle slot.
  template <class Future, class Resolve, class Fixed>
  bool prepare(const NspFrame &f, uint64_t start, Future future,
               Resolve resolve, Fixed fixed) {
    if (mappedStart == start)
      return true;
    mappingChecks++;
    if (!f.channels || f.channels > maxSignals)
      return false;
    const NspFrame *declaration = &f;
    NspFrame lookahead{};
    auto complete = [](const NspFrame &v) {
      if (!v.labels)
        return false;
      for (uint32_t ch = 0; ch < v.channels; ch++)
        if (v.labels[ch] == 255)
          return false;
      return true;
    };
    bool known = complete(f);
    if (!known && channels != f.channels) {
      bool found = false;
      for (size_t i = 0; future(i, lookahead); i++) {
        if (lookahead.channels == f.channels && complete(lookahead)) {
          found = true;
          break;
        }
      }
      if (!found)
        return false;
      declaration = &lookahead;
      known = true;
    }
    std::array<uint32_t, maxSignals> ids{};
    ids.fill(UINT32_MAX);
    bool changed = channels != f.channels;
    if (known) {
      for (uint32_t o = 0; o < declaration->object_count; o++) {
        const auto &object = declaration->objects[o];
        if (object.channel >= f.channels)
          return false;
        ids[object.channel] = object.id;
      }
      for (uint32_t ch = 0; ch < f.channels; ch++)
        changed |= knownLabels[ch] != declaration->labels[ch] ||
                   knownIds[ch] != ids[ch];
      channels = f.channels;
      for (uint32_t ch = 0; ch < channels; ch++) {
        knownLabels[ch] = declaration->labels[ch];
        knownIds[ch] = ids[ch];
      }
    }
    if (changed) {
      routeBuilds++;
      for (uint32_t ch = 0; ch < channels; ch++) {
        uint8_t label = knownLabels[ch];
        uint32_t id = knownIds[ch];
        if (label == 24 && id == UINT32_MAX)
          return false;
        auto *s = resolve(label, id);
        if (!s)
          return false;
        auto *p = label == 24 ? pose(id) : nullptr;
        if (label == 24 && !p)
          return false;
        routes[ch] = {s, p, windowsPosition(fixed(label))};
        if (p)
          p->slot = int(s - slots.data());
      }
    }
    mappedStart = start;
    return true;
  }
  bool render(const NspFrame &f, uint64_t start, uint32_t &event,
              uint32_t sourceOffset, uint32_t count, uint32_t destination,
              uint64_t &nonzero) {
    uint32_t done = 0;
    while (done < count) {
      uint64_t at = start + sourceOffset + done;
      if (!apply(f, event, at))
        return false;
      uint32_t span = count - done;
      if (event < f.event_count && f.events[event].sample_pos > at)
        span =
            uint32_t(std::min<uint64_t>(span, f.events[event].sample_pos - at));
      for (uint32_t ch = 0; ch < f.channels; ch++) {
        auto &r = routes[ch];
        auto &s = *r.slot;
        if (!s.buffer)
          return false;
        const int32_t *src =
            f.pcm + size_t(sourceOffset + done) * f.channels + ch;
        float *dst = s.buffer + destination + done;
        for (uint32_t i = 0; i < span; i++) {
          float value = src[size_t(i) * f.channels] / 8388608.f;
          if (r.pose)
            value *= gainAt(r.pose->value, at + i);
          dst[i] += value;
          nonzero += value != 0;
        }
        if (s.dynamic) {
          if (s.positionPending && s.pendingPose != r.pose)
            finishPosition(s);
          s.positioned = true;
          if (r.pose) {
            s.positionPending = true;
            s.pendingPose = r.pose;
            s.lastPositionSample = at + span - 1;
          } else {
            s.position = r.fixed;
            s.positionPending = false;
          }
        }
      }
      done += span;
    }
    return true;
  }
};
