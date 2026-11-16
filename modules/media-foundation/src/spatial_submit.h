// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "decoder.h"
#include "progress_signal.h"
#include "spatial_mixer.h"
#include "spsc_ring.h"
#include <avrt.h>
#include <condition_variable>
#include <memory>
#include <mmdeviceapi.h>
#include <spatialaudioclient.h>
#include <string>
#include <thread>
struct SpatialSubmitter {
  using Pose = SpatialMixer::Pose;
  using Slot = SpatialMixer::Slot;
  struct Held {
    NspOwnedFrame *owner = nullptr;
    NspFrame view{};
    UINT64 start = 0, generation = 0;
    UINT32 event = 0;
  };
  static constexpr UINT64 maxSamples = 96000;
  static constexpr size_t maxFrames = 8192;
  Decoder &decoder;
  std::mutex startupLock;
  std::condition_variable ready;
  SpscRing<Held, maxFrames> queue;
  SpscRing<NspOwnedFrame *, maxFrames> retired;
  std::atomic<UINT64> queuedSamples{0}, generation{0};
  std::atomic<size_t> credits{maxFrames};
  std::atomic<bool> stopping{false}, reclaimerStopping{false};
  std::thread worker, reclaimer;
  HANDLE stopEvent = nullptr, controlEvent = nullptr, recycleEvent = nullptr;
  IStream *marshaledClock = nullptr;
  std::wstring endpoint;
  LONGLONG clockOffset = 0;
  bool initialized = false;
  Held active{};
  bool haveActive = false;
  std::shared_ptr<ProgressSignal> progress;
  std::unique_ptr<SpatialMixer> mixer = std::make_unique<SpatialMixer>();
  std::atomic<HRESULT> error{S_OK};
  std::atomic<UINT64> submitted{0}, nonzero{0}, consumedSamples{0},
      positionUpdates{0};
  explicit SpatialSubmitter(Decoder &d,
                            std::shared_ptr<ProgressSignal> signal = {})
      : decoder(d), progress(std::move(signal)) {}
  ~SpatialSubmitter() { stop(); }
  void notifyCapacity() {
    if (progress)
      progress->notifyCapacity();
  }
  void reclaim() {
    for (;;) {
      WaitForSingleObject(recycleEvent, INFINITE);
      NspOwnedFrame *frame = nullptr;
      size_t count = 0;
      while (retired.pop(frame)) {
        decoder.releaseFrame(frame);
        count++;
      }
      if (count) {
        credits.fetch_add(count, std::memory_order_release);
        notifyCapacity();
      }
      if (reclaimerStopping)
        break;
    }
  }
  void stop() {
    stopping = true;
    if (stopEvent)
      SetEvent(stopEvent);
    if (worker.joinable())
      worker.join();
    // Any emergency remainder is released by the caller, never the audio
    // thread.
    if (haveActive) {
      decoder.releaseFrame(active.owner);
      haveActive = false;
    }
    Held frame;
    while (queue.pop(frame))
      decoder.releaseFrame(frame.owner);
    reclaimerStopping = true;
    if (recycleEvent)
      SetEvent(recycleEvent);
    if (reclaimer.joinable())
      reclaimer.join();
    NspOwnedFrame *owner = nullptr;
    while (retired.pop(owner))
      decoder.releaseFrame(owner);
    if (marshaledClock) {
      marshaledClock->Release();
      marshaledClock = nullptr;
    }
    for (HANDLE *event : {&stopEvent, &controlEvent, &recycleEvent})
      if (*event) {
        CloseHandle(*event);
        *event = nullptr;
      }
  }
  HRESULT bind(IMFPresentationClock *clock, LPCWSTR device, LONGLONG offset) {
    if (!clock || !device || !*device)
      return E_INVALIDARG;
    stop();
    stopping = false;
    reclaimerStopping = false;
    initialized = false;
    error = S_OK;
    queuedSamples = 0;
    credits = maxFrames;
    generation = 0;
    *mixer = SpatialMixer{};
    endpoint = device;
    clockOffset = offset;
    stopEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    controlEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    recycleEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!stopEvent || !controlEvent || !recycleEvent)
      return HRESULT_FROM_WIN32(GetLastError());
    HRESULT hr = CoMarshalInterThreadInterfaceInStream(
        __uuidof(IMFPresentationClock), clock, &marshaledClock);
    if (FAILED(hr))
      return hr;
    reclaimer = std::thread([this] { reclaim(); });
    worker = std::thread([this] { run(); });
    std::unique_lock guard(startupLock);
    if (!ready.wait_for(guard, std::chrono::seconds(5),
                        [this] { return initialized; })) {
      guard.unlock();
      stop();
      return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
    }
    return error;
  }
  bool capacity(UINT32 n) {
    return !stopping && SUCCEEDED(error) &&
           credits.load(std::memory_order_acquire) > 0 &&
           queuedSamples.load(std::memory_order_acquire) + n <= maxSamples;
  }
  HRESULT enqueue(const NspFrame &view) {
    if (stopping || FAILED(error))
      return FAILED(error) ? error.load() : MF_E_SHUTDOWN;
    if (!view.samples || view.rate != 48000 || !view.channels ||
        view.channels > SpatialMixer::maxSignals)
      return MF_E_INVALID_STREAM_DATA;
    if (!capacity(view.samples))
      return MF_E_NOTACCEPTING;
    auto *owner = decoder.take(decoder.context);
    if (!owner)
      return E_FAIL;
    credits.fetch_sub(1, std::memory_order_relaxed);
    queuedSamples.fetch_add(view.samples, std::memory_order_relaxed);
    Held frame{owner, view, decoder.frameStart(owner),
               generation.load(std::memory_order_acquire)};
    if (!queue.push(frame)) {
      queuedSamples.fetch_sub(view.samples, std::memory_order_relaxed);
      decoder.releaseFrame(owner);
      credits.fetch_add(1, std::memory_order_release);
      return E_UNEXPECTED;
    }
    return S_OK;
  }
  void flush() {
    generation.fetch_add(1, std::memory_order_release);
    if (controlEvent)
      SetEvent(controlEvent);
  }
  bool retireActive() {
    if (!retired.push(active.owner))
      return false;
    queuedSamples.fetch_sub(active.view.samples, std::memory_order_release);
    haveActive = false;
    return true;
  }
  static std::array<float, 3>
  windowsPosition(const std::array<double, 3> &adm) {
    return {(float)adm[0], (float)adm[2], (float)-adm[1]};
  }
  static UINT32 staticType(UINT8 label) {
    switch (label) {
    case 0:
      return AudioObjectType_FrontLeft;
    case 1:
      return AudioObjectType_FrontRight;
    case 2:
      return AudioObjectType_FrontCenter;
    case 3:
    case 23:
      return AudioObjectType_LowFrequency;
    case 4:
      return AudioObjectType_SideLeft;
    case 5:
      return AudioObjectType_SideRight;
    case 6:
      return AudioObjectType_TopFrontLeft;
    case 7:
      return AudioObjectType_TopFrontRight;
    case 10:
      return AudioObjectType_TopBackLeft;
    case 11:
      return AudioObjectType_TopBackRight;
    case 14:
      return AudioObjectType_BackLeft;
    case 15:
      return AudioObjectType_BackRight;
    case 16:
      return AudioObjectType_BackCenter;
    default:
      return 0;
    }
  }
  static std::array<double, 3> fixedPosition(UINT8 label) {
    switch (label) {
    case 8:
      return {-1, 0, 1};
    case 9:
      return {1, 0, 1};
    case 12:
      return {-.5, 1, 0};
    case 13:
      return {.5, 1, 0};
    case 17:
      return {0, 0, 1};
    case 18:
      return {-1, -.5, 0};
    case 19:
      return {1, -.5, 0};
    case 20:
      return {-1, .5, 0};
    case 21:
      return {1, .5, 0};
    case 22:
      return {0, 1, 1};
    case 25:
      return {-1, 1, .5};
    case 26:
      return {1, 1, .5};
    case 27:
      return {0, 1, .5};
    case 28:
      return {-1, -.5, .5};
    case 29:
      return {1, -.5, .5};
    default:
      return {0, 0, 0};
    }
  }
  static double blend(INT64 now, INT64 at, UINT32 duration) {
    return duration ? std::clamp((double)(now - at) / duration, 0., 1.) : 1.;
  }
  static std::array<double, 3> positionAt(const Pose &p, INT64 sample) {
    double t = blend(sample, p.at, p.ramp);
    return {p.from[0] + (p.position[0] - p.from[0]) * t,
            p.from[1] + (p.position[1] - p.from[1]) * t,
            p.from[2] + (p.position[2] - p.from[2]) * t};
  }
  static double gainAt(const Pose &p, INT64 sample) {
    double t = blend(sample, p.at, p.ramp);
    return p.fromGain + (p.gain - p.fromGain) * t;
  }
  void run();
};
#include "spatial_submit_run.h"
