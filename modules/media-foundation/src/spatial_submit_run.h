// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
inline void SpatialSubmitter::run() {
  HRESULT co = CoInitializeEx(nullptr, COINIT_MULTITHREADED), hr = co;
  IMFPresentationClock *clock = nullptr;
  IMFRateControl *rateControl = nullptr;
  IMMDeviceEnumerator *enumerator = nullptr;
  IMMDevice *device = nullptr;
  ISpatialAudioClient *client = nullptr;
  ISpatialAudioObjectRenderStream *stream = nullptr;
  HANDLE audioEvent = nullptr, mmcss = nullptr;
  UINT32 mask = 0, maxDynamic = 0;
  bool streamRunning = false, haveOrigin = false, haveCursor = false;
  LONGLONG origin = 0;
  INT64 cursor = 0;
  UINT64 epoch = 0;
  auto &mix = *mixer;
  auto releaseSlots = [&] {
    for (UINT32 i = 0; i < mix.slotCount; i++)
      if (mix.slots[i].native) {
        static_cast<ISpatialAudioObject *>(mix.slots[i].native)->Release();
        mix.slots[i].native = nullptr;
      }
  };
  if (SUCCEEDED(hr)) {
    IStream *transfer = marshaledClock;
    marshaledClock = nullptr;
    hr = CoGetInterfaceAndReleaseStream(
        transfer, __uuidof(IMFPresentationClock), (void **)&clock);
  }
  if (SUCCEEDED(hr))
    clock->QueryInterface(IID_PPV_ARGS(&rateControl));
  if (SUCCEEDED(hr))
    hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                          CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&enumerator));
  if (SUCCEEDED(hr))
    hr = enumerator->GetDevice(endpoint.c_str(), &device);
  if (SUCCEEDED(hr))
    hr = device->Activate(__uuidof(ISpatialAudioClient), CLSCTX_INPROC_SERVER,
                          nullptr, (void **)&client);
  if (SUCCEEDED(hr)) {
    AudioObjectType types{};
    hr = client->GetNativeStaticObjectTypeMask(&types);
    mask = types;
  }
  if (SUCCEEDED(hr))
    hr = client->GetMaxDynamicObjectCount(&maxDynamic);
  WAVEFORMATEX format{WAVE_FORMAT_IEEE_FLOAT, 1, 48000, 192000, 4, 32, 0};
  if (SUCCEEDED(hr))
    hr = client->IsAudioObjectFormatSupported(&format);
  if (SUCCEEDED(hr)) {
    audioEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!audioEvent)
      hr = HRESULT_FROM_WIN32(GetLastError());
  }
  SpatialAudioObjectRenderStreamActivationParams params{};
  PROPVARIANT pv{};
  if (SUCCEEDED(hr)) {
    params.ObjectFormat = &format;
    params.StaticObjectTypeMask = (AudioObjectType)mask;
    params.MinDynamicObjectCount = 0;
    params.MaxDynamicObjectCount = std::min(128u, maxDynamic);
    params.Category = AudioCategory_Media;
    params.EventHandle = audioEvent;
    pv.vt = VT_BLOB;
    pv.blob.cbSize = sizeof(params);
    pv.blob.pBlobData = (BYTE *)&params;
    hr = client->ActivateSpatialAudioStream(
        &pv, __uuidof(ISpatialAudioObjectRenderStream), (void **)&stream);
  }
  if (SUCCEEDED(hr)) {
    DWORD task = 0;
    mmcss = AvSetMmThreadCharacteristicsW(L"Pro Audio", &task);
    if (!mmcss)
      hr = HRESULT_FROM_WIN32(GetLastError());
  }
  {
    std::lock_guard guard(startupLock);
    error = hr;
    initialized = true;
  }
  ready.notify_all();
  while (SUCCEEDED(hr) && !stopping) {
    bool recycle = false;
    UINT64 requested = generation.load(std::memory_order_acquire);
    if (requested != epoch) {
      if (streamRunning) {
        hr = stream->Stop();
        streamRunning = false;
        if (FAILED(hr))
          break;
      }
      releaseSlots();
      if (FAILED(hr = stream->Reset()))
        break;
      mix = SpatialMixer{};
      haveOrigin = haveCursor = false;
      epoch = requested;
      if (haveActive && active.generation < epoch) {
        if (!retireActive()) {
          hr = E_UNEXPECTED;
          break;
        }
        recycle = true;
      }
      Held front;
      while (!haveActive && queue.peek(front) && front.generation < epoch) {
        queue.pop(active);
        haveActive = true;
        if (!retireActive()) {
          hr = E_UNEXPECTED;
          break;
        }
        recycle = true;
      }
      if (recycle) {
        SetEvent(recycleEvent);
        notifyCapacity();
      }
      if (FAILED(hr))
        break;
    }
    MFCLOCK_STATE state = MFCLOCK_STATE_INVALID;
    hr = clock->GetState(0, &state);
    if (FAILED(hr))
      break;
    if (rateControl) {
      BOOL thin = FALSE;
      float rate = 1;
      hr = rateControl->GetRate(&thin, &rate);
      if (FAILED(hr))
        break;
      if (rate == 0)
        state = MFCLOCK_STATE_PAUSED;
      else if (thin || fabs(rate - 1) > 1e-6) {
        hr = MF_E_UNSUPPORTED_RATE;
        break;
      }
    }
    if (state != MFCLOCK_STATE_RUNNING) {
      if (streamRunning) {
        hr = stream->Stop();
        streamRunning = false;
      }
      haveCursor = false;
      HANDLE waits[]{stopEvent, controlEvent};
      if (WaitForMultipleObjects(2, waits, FALSE, 5) == WAIT_OBJECT_0)
        break;
      continue;
    }
    if (!streamRunning) {
      hr = stream->Start();
      if (FAILED(hr))
        break;
      streamRunning = true;
    }
    HANDLE waits[]{stopEvent, controlEvent, audioEvent};
    DWORD waited = WaitForMultipleObjects(3, waits, FALSE, 100);
    if (waited == WAIT_OBJECT_0)
      break;
    if (waited == WAIT_OBJECT_0 + 1 || waited == WAIT_TIMEOUT)
      continue;
    if (waited != WAIT_OBJECT_0 + 2) {
      hr = HRESULT_FROM_WIN32(GetLastError());
      break;
    }
    if (FAILED(hr = clock->GetState(0, &state)))
      break;
    if (state != MFCLOCK_STATE_RUNNING)
      continue;
    LONGLONG now = 0;
    if (FAILED(hr = clock->GetTime(&now)))
      break;
    if (!haveActive) {
      Held front;
      if (queue.peek(front) && front.generation == epoch) {
        queue.pop(active);
        haveActive = true;
      }
    }
    if (!haveOrigin && haveActive) {
      origin = active.view.pts - (LONGLONG)(active.start * 10000000 / 48000);
      haveOrigin = true;
    }
    if (haveOrigin) {
      INT64 at = (now - clockOffset - origin) * 48000 / 10000000;
      if (!haveCursor || llabs(at - cursor) > 1440) {
        cursor = at;
        haveCursor = true;
      }
    }
    UINT32 available = 0, n = 0;
    if (FAILED(hr = stream->BeginUpdatingAudioObjects(&available, &n)))
      break;
    if (n > 4096)
      hr = E_UNEXPECTED;
    mix.beginCycle();
    UINT64 blockNonzero = 0, blockConsumed = 0, blockPositions = 0;
    auto prepareBuffer = [&](Slot &s) -> bool {
      if (s.buffer)
        return true;
      auto *object = static_cast<ISpatialAudioObject *>(s.native);
      if (!object) {
        hr = stream->ActivateSpatialAudioObject((AudioObjectType)s.type,
                                                &object);
        if (FAILED(hr))
          return false;
        s.native = object;
      }
      BYTE *buffer = nullptr;
      hr = object->GetBuffer(&buffer, &s.bytes);
      if (FAILED(hr) || s.bytes < n * 4) {
        if (SUCCEEDED(hr))
          hr = E_UNEXPECTED;
        return false;
      }
      s.buffer = (float *)buffer;
      memset(buffer, 0, n * 4);
      return true;
    };
    for (UINT32 i = 0; i < mix.slotCount && SUCCEEDED(hr); i++)
      prepareBuffer(mix.slots[i]);
    UINT32 k = 0;
    while (k < n && SUCCEEDED(hr) && haveCursor) {
      INT64 sample = cursor + k;
      if (sample < 0) {
        k += UINT32(std::min<UINT64>(n - k, UINT64(-sample)));
        continue;
      }
      if (!haveActive) {
        Held front;
        if (!queue.peek(front) || front.generation > epoch)
          break;
        queue.pop(active);
        haveActive = true;
      }
      if (active.generation < epoch) {
        if (!retireActive()) {
          hr = E_UNEXPECTED;
          break;
        }
        recycle = true;
        continue;
      }
      auto &f = active.view;
      if (active.start + f.samples <= UINT64(sample)) {
        if (!mix.apply(f, active.event, active.start + f.samples)) {
          hr = MF_E_INVALID_STREAM_DATA;
          break;
        }
        blockConsumed += f.samples;
        if (!retireActive()) {
          hr = E_UNEXPECTED;
          break;
        }
        recycle = true;
        continue;
      }
      if (UINT64(sample) < active.start) {
        k += UINT32(std::min<UINT64>(n - k, active.start - sample));
        continue;
      }
      auto future = [&](size_t i, NspFrame &out) {
        Held next;
        if (!queue.peek(next, i) || next.generation != epoch)
          return false;
        out = next.view;
        return true;
      };
      auto resolve = [&](UINT8 label, UINT32 objectId) -> Slot * {
        UINT32 type = staticType(label);
        bool dynamic = label == 24 || !(type & mask);
        UINT32 id = label == 24 ? objectId : 0x80000000u + label;
        UINT64 key = dynamic ? (1ull << 32) | id : type;
        auto *s =
            mix.slot(key, dynamic, dynamic ? AudioObjectType_Dynamic : type);
        return s && prepareBuffer(*s) ? s : nullptr;
      };
      if (!mix.prepare(f, active.start, future, resolve,
                       SpatialSubmitter::fixedPosition)) {
        if (generation.load(std::memory_order_acquire) == epoch &&
            SUCCEEDED(hr))
          hr = MF_E_INVALIDMEDIATYPE;
        break;
      }
      UINT32 offset = UINT32(UINT64(sample) - active.start),
             count = std::min(n - k, f.samples - offset);
      if (!mix.render(f, active.start, active.event, offset, count, k,
                      blockNonzero)) {
        hr = MF_E_INVALID_STREAM_DATA;
        break;
      }
      k += count;
    }
    mix.finishCycle();
    bool superseded = generation.load(std::memory_order_acquire) != epoch;
    for (UINT32 i = 0; i < mix.slotCount && SUCCEEDED(hr); i++) {
      auto &s = mix.slots[i];
      if (!s.buffer && !prepareBuffer(s))
        break;
      if (superseded) {
        memset(s.buffer, 0, n * 4);
        continue;
      }
      auto *object = static_cast<ISpatialAudioObject *>(s.native);
      if (s.positioned) {
        hr = object->SetPosition(s.position[0], s.position[1], s.position[2]);
        blockPositions++;
      }
      if (SUCCEEDED(hr))
        hr = object->SetVolume(1.f);
    }
    HRESULT ended = stream->EndUpdatingAudioObjects();
    if (SUCCEEDED(hr))
      hr = ended;
    if (SUCCEEDED(hr)) {
      submitted.fetch_add(n, std::memory_order_relaxed);
      nonzero.fetch_add(superseded ? 0 : blockNonzero,
                        std::memory_order_relaxed);
      consumedSamples.fetch_add(blockConsumed, std::memory_order_relaxed);
      positionUpdates.fetch_add(blockPositions, std::memory_order_relaxed);
      if (haveCursor)
        cursor += n;
    }
    if (recycle) {
      SetEvent(recycleEvent);
      notifyCapacity();
    }
  }
  // Consumer teardown also transfers ownership to the recycler. Stop() joins
  // this worker before directly releasing any invariant-failure remainder.
  bool moved = false;
  for (;;) {
    if (!haveActive) {
      if (!queue.pop(active))
        break;
      haveActive = true;
    }
    if (!retireActive())
      break;
    moved = true;
  }
  if (moved)
    SetEvent(recycleEvent);
  error = hr;
  if (progress)
    progress->signal();
  if (streamRunning)
    stream->Stop();
  releaseSlots();
  if (stream)
    stream->Release();
  if (client)
    client->Release();
  if (device)
    device->Release();
  if (enumerator)
    enumerator->Release();
  if (rateControl)
    rateControl->Release();
  if (clock)
    clock->Release();
  if (audioEvent)
    CloseHandle(audioEvent);
  if (mmcss)
    AvRevertMmThreadCharacteristics(mmcss);
  if (SUCCEEDED(co))
    CoUninitialize();
}
