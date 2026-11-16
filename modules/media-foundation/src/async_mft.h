// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "progress_signal.h"
#include <atomic>
#include <deque>
#include <functional>
#include <memory>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mutex>
#include <thread>
// Event delivery is independent of the audio thread; decoded PCM stays owned by
// Rust.
struct AsyncMftState {
  IMFMediaEventQueue *events = nullptr;
  std::thread worker;
  std::shared_ptr<ProgressSignal> progress = std::make_shared<ProgressSignal>();
  std::atomic<bool> stopping{false}, shutdown{false}, completed{false};
  std::mutex stopLock;
  bool streaming = false, needInput = false, haveOutput = false,
       drainSent = false, errorSent = false;
  std::deque<UINT64> markers;
  HRESULT init(std::function<void()> pump) {
    if (!progress->event)
      return HRESULT_FROM_WIN32(GetLastError());
    HRESULT hr = MFCreateEventQueue(&events);
    if (FAILED(hr))
      return hr;
    pumpCallback = std::move(pump);
    return S_OK;
  }
  std::function<void()> pumpCallback;
  HRESULT start() {
    std::lock_guard guard(stopLock);
    if (!events || !pumpCallback)
      return E_UNEXPECTED;
    if (stopping || shutdown)
      return MF_E_SHUTDOWN;
    if (worker.joinable())
      return S_OK;
    try {
      worker = std::thread([this] {
        HRESULT co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        while (!stopping) {
          WaitForSingleObject(progress->event, INFINITE);
          if (!stopping)
            pumpCallback();
        }
        if (SUCCEEDED(co))
          CoUninitialize();
      });
    } catch (...) {
      return E_FAIL;
    }
    return S_OK;
  }
  void stop() {
    std::lock_guard guard(stopLock);
    stopping = true;
    progress->signal();
    if (worker.joinable())
      worker.join();
    if (events)
      events->Shutdown();
  }
  ~AsyncMftState() {
    stop();
    if (events)
      events->Release();
  }
  HRESULT event(MediaEventType kind, HRESULT status = S_OK) {
    if (!events)
      return E_OUTOFMEMORY;
    IMFMediaEvent *notification = nullptr;
    HRESULT hr =
        MFCreateMediaEvent(kind, GUID_NULL, status, nullptr, &notification);
    if (FAILED(hr))
      return hr;
    if (kind == METransformNeedInput || kind == METransformDrainComplete)
      hr = notification->SetUINT32(MF_EVENT_MFT_INPUT_STREAM_ID, 0);
    if (SUCCEEDED(hr))
      hr = events->QueueEvent(notification);
    notification->Release();
    return hr;
  }
};
