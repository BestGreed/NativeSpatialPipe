// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "../include/nsp_spatial_control.h"
#include "async_mft.h"
#include "decoder.h"
#include "spatial_submit.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <memory>
static LONG64 totalInputs = 0, totalOutputs = 0, totalEvents = 0;
static void diagnostic(const char *event, HRESULT hr = S_OK, LONGLONG pts = 0,
                       UINT64 submitted = 0, UINT64 nonzero = 0) {
  wchar_t cfg[32768];
  sibling(cfg, L"nsp-mft.ini");
  if (!GetPrivateProfileIntW(L"diagnostics", L"trace", 0, cfg))
    return;
  wchar_t path[32768];
  DWORD length = GetTempPathW(32000, path);
  if (!length || length > 32000)
    return;
  swprintf(path + length, 32768 - length, L"nsp-mft-%lu.jsonl",
           GetCurrentProcessId());
  HANDLE f =
      CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                  nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (f == INVALID_HANDLE_VALUE)
    return;
  LARGE_INTEGER size{};
  GetFileSizeEx(f, &size);
  if (size.QuadPart < 262144) {
    char line[512];
    int n = snprintf(
        line, sizeof(line),
        "{\"event\":\"%s\",\"pid\":%lu,\"tick\":%llu,\"hr\":%ld,\"inputs\":%"
        "lld,\"outputs\":%lld,\"metadataEvents\":%lld,\"pts100ns\":%lld,"
        "\"submittedFrames\":%llu,\"nonzeroValues\":%llu}\n",
        event, GetCurrentProcessId(), GetTickCount64(), hr, totalInputs,
        totalOutputs, totalEvents, pts, submitted, nonzero);
    DWORD done;
    WriteFile(f, line, n, &done, nullptr);
  }
  CloseHandle(f);
}
struct TransformBase {
  IMFMediaType *input = nullptr;
  IMFMediaType *output = nullptr;
  IMFAttributes *attrs = nullptr;
  Decoder decoder;
  std::mutex mutex;
  bool draining = false, discontinuity = true;
  UINT32 bits = 16;
  bool preview = false, spatialEnabled = false;
  std::unique_ptr<SpatialSubmitter> spatial;
  IUnknown *boundClock = nullptr;
  std::wstring boundEndpoint;
  LONGLONG boundOffset = 0;
  UINT64 sourceInputs = 0;
  bool asynchronous = false;
  std::unique_ptr<AsyncMftState> async;
  struct Pose {
    double pan = 0, gain = 1;
  };
  std::array<Pose, 160> poses{};
  TransformBase() {
    InterlockedIncrement(&alive);
    MFCreateAttributes(&attrs, 2);
    if (attrs)
      attrs->SetUINT32(MF_TRANSFORM_ASYNC, FALSE);
    wchar_t cfg[32768];
    sibling(cfg, L"nsp-mft.ini");
    preview = GetPrivateProfileIntW(L"testing", L"pcmPreview", 0, cfg) != 0;
    spatialEnabled = GetPrivateProfileIntW(L"spatial", L"enabled", 0, cfg) != 0;
    asynchronous = configuredAsync();
    if (asynchronous) {
      if (attrs) {
        attrs->SetUINT32(MF_TRANSFORM_ASYNC, TRUE);
        attrs->SetUINT32(MFT_SUPPORT_DYNAMIC_FORMAT_CHANGE, TRUE);
      }
      async = std::make_unique<AsyncMftState>();
      HRESULT hr = async->init([this] {
        std::lock_guard guard(mutex);
        pumpLocked();
      });
      if (FAILED(hr))
        diagnostic("AsyncInitFailed", hr);
    }
    diagnostic("Construct");
  }
  ~TransformBase() {
    if (async) {
      async->shutdown = true;
      async->stop();
    }
    if (spatial)
      diagnostic("SpatialSubmitResult", spatial->error.load(), 0,
                 spatial->submitted, spatial->nonzero);
    spatial.reset();
    if (boundClock)
      boundClock->Release();
    decoder.close();
    if (decoder.dll) {
      FreeLibrary(decoder.dll);
      decoder.dll = nullptr;
    }
    if (input)
      input->Release();
    if (output)
      output->Release();
    if (attrs)
      attrs->Release();
    diagnostic("Destroy");
    InterlockedDecrement(&alive);
  }
  HRESULT available(DWORD id, DWORD index, IMFMediaType **type, bool in) {
    if (id)
      return MF_E_INVALIDSTREAMNUMBER;
    if (!type)
      return E_POINTER;
    *type = nullptr;
    if (index > (in ? 2u : 1u))
      return MF_E_NO_MORE_TYPES;
    HRESULT hr = MFCreateMediaType(type);
    if (FAILED(hr))
      return hr;
    auto *t = *type;
    t->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Audio);
    t->SetGUID(MF_MT_SUBTYPE,
               in ? (index == 0   ? MFAudioFormat_Dolby_DDPlus
                     : index == 1 ? TrueHD
                                  : MFAudioFormat_Dolby_AC3)
                  : (index ? MFAudioFormat_Float : MFAudioFormat_PCM));
    if (!in) {
      UINT32 b = index ? 32 : 16;
      t->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, 2);
      t->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, 48000);
      t->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, b);
      t->SetUINT32(MF_MT_AUDIO_BLOCK_ALIGNMENT, b / 4);
      t->SetUINT32(MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 48000 * b / 4);
      t->SetUINT32(MF_MT_AUDIO_CHANNEL_MASK, 3);
      t->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
    }
    return S_OK;
  }
  HRESULT set(DWORD id, IMFMediaType *type, DWORD flags, bool in) {
    if (id)
      return MF_E_INVALIDSTREAMNUMBER;
    if (flags & ~MFT_SET_TYPE_TEST_ONLY)
      return E_INVALIDARG;
    GUID major{}, sub{};
    UINT32 b = 0;
    if (type) {
      type->GetGUID(MF_MT_MAJOR_TYPE, &major);
      type->GetGUID(MF_MT_SUBTYPE, &sub);
      if (major != MFMediaType_Audio)
        return MF_E_INVALIDMEDIATYPE;
      if (in) {
        if (sub != TrueHD && sub != MFAudioFormat_Dolby_DDPlus &&
            sub != MFAudioFormat_Dolby_AC3)
          return MF_E_INVALIDMEDIATYPE;
        UINT32 rate = 0;
        type->GetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, &rate);
        if (rate && rate != 48000)
          return MF_E_INVALIDMEDIATYPE;
      } else {
        UINT32 ch = 0, rate = 0;
        type->GetUINT32(MF_MT_AUDIO_NUM_CHANNELS, &ch);
        type->GetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, &rate);
        type->GetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, &b);
        if (ch != 2 || rate != 48000 ||
            !((sub == MFAudioFormat_PCM && b == 16) ||
              (sub == MFAudioFormat_Float && b == 32)))
          return MF_E_INVALIDMEDIATYPE;
      }
    }
    if (flags & MFT_SET_TYPE_TEST_ONLY)
      return S_OK;
    std::lock_guard lock(mutex);
    if (decoder.ready())
      return MF_E_TRANSFORM_CANNOT_CHANGE_MEDIATYPE_WHILE_PROCESSING;
    if (in && type) {
      if (spatial)
        spatial->flush();
      HRESULT hr = decoder.open(sub == TrueHD                    ? 2
                                : sub == MFAudioFormat_Dolby_AC3 ? 3
                                                                 : 1);
      if (FAILED(hr)) {
        diagnostic("DecoderLoadFailed", hr);
        return hr;
      }
      discontinuity = true;
      poses = {};
      sourceInputs = 0;
    }
    auto *&previous = in ? input : output;
    if (type)
      type->AddRef();
    if (previous)
      previous->Release();
    previous = type;
    if (in && !type) {
      if (spatial)
        spatial->flush();
      decoder.close();
    }
    if (!in && type)
      bits = b;
    draining = false;
    diagnostic(in ? "SetInputType" : "SetOutputType");
    return S_OK;
  }
  HRESULT current(DWORD id, IMFMediaType **type, bool in) {
    if (id)
      return MF_E_INVALIDSTREAMNUMBER;
    if (!type)
      return E_POINTER;
    std::lock_guard lock(mutex);
    *type = in ? input : output;
    if (!*type)
      return MF_E_TRANSFORM_TYPE_NOT_SET;
    (*type)->AddRef();
    return S_OK;
  }
  HRESULT handleMessage(MFT_MESSAGE_TYPE msg, ULONG_PTR param) {
    if ((msg == MFT_MESSAGE_COMMAND_DRAIN ||
         msg == MFT_MESSAGE_NOTIFY_END_OF_STREAM) &&
        param != 0)
      return MF_E_INVALIDSTREAMNUMBER;
    // Worker lifecycle lock must never be acquired while holding the MFT
    // mutex: Shutdown joins a notifier that may be waiting for this mutex.
    if (msg == MFT_MESSAGE_NOTIFY_START_OF_STREAM && async) {
      HRESULT hr = async->start();
      if (FAILED(hr))
        return hr;
    }
    std::lock_guard lock(mutex);
    if (msg == MFT_MESSAGE_COMMAND_FLUSH) {
      if (async) {
        async->streaming = false;
        async->needInput = async->haveOutput = async->drainSent =
            async->errorSent = false;
        async->markers.clear();
      }
      sourceInputs = 0;
      if (spatial)
        spatial->flush();
      if (decoder.context && decoder.reset(decoder.context) != 0)
        return E_FAIL;
      draining = false;
      discontinuity = true;
      poses = {};
      diagnostic("Flush");
    } else if (msg == MFT_MESSAGE_COMMAND_DRAIN) {
      draining = true;
      if (async) {
        async->streaming = false;
        async->needInput = false;
        async->drainSent = false;
      }
      diagnostic("Drain");
    } else if (msg == MFT_MESSAGE_NOTIFY_START_OF_STREAM) {
      draining = false;
      if (async)
        async->streaming = true;
    } else if (msg == MFT_MESSAGE_NOTIFY_END_OF_STREAM ||
               msg == MFT_MESSAGE_NOTIFY_END_STREAMING) {
      if (async) {
        async->streaming = false;
        async->needInput = false;
      }
    } else if (msg == MFT_MESSAGE_COMMAND_MARKER && async) {
      async->markers.push_back(param);
    } else if (msg == MFT_MESSAGE_SET_D3D_MANAGER)
      return E_NOTIMPL;
    pumpLocked();
    return S_OK;
  }
  HRESULT consume(DWORD id, IMFSample *sample, DWORD flags) {
    if (id)
      return MF_E_INVALIDSTREAMNUMBER;
    if (!sample)
      return E_POINTER;
    if (flags)
      return E_INVALIDARG;
    std::lock_guard lock(mutex);
    if (!input || !output || !decoder.context)
      return MF_E_TRANSFORM_TYPE_NOT_SET;
    if (draining || decoder.ready() ||
        (async && (!async->streaming || !async->needInput)))
      return MF_E_NOTACCEPTING;
    DWORD bytes = 0;
    HRESULT hr = sample->GetTotalLength(&bytes);
    if (FAILED(hr))
      return hr;
    if (!bytes || bytes > 65536)
      return MF_E_INVALID_STREAM_DATA;
    UINT32 disc = 0;
    sample->GetUINT32(MFSampleExtension_Discontinuity, &disc);
    if (disc) {
      if (spatial)
        spatial->flush();
      if (decoder.reset(decoder.context) != 0)
        return E_FAIL;
      discontinuity = true;
      poses = {};
    }
    LONGLONG pts = 0;
    sample->GetSampleTime(&pts);
    DWORD count = 0;
    sample->GetBufferCount(&count);
    IMFMediaBuffer *buffer = nullptr;
    hr = count == 1 ? sample->GetBufferByIndex(0, &buffer)
                    : sample->ConvertToContiguousBuffer(&buffer);
    if (FAILED(hr))
      return hr;
    BYTE *data = nullptr;
    DWORD size = 0;
    hr = buffer->Lock(&data, nullptr, &size);
    if (SUCCEEDED(hr)) {
      int result = decoder.push(decoder.context, data, size, pts);
      buffer->Unlock();
      hr = result == -2 ? MF_E_NOTACCEPTING
           : result     ? MF_E_INVALID_STREAM_DATA
                        : S_OK;
    }
    buffer->Release();
    if (SUCCEEDED(hr)) {
      if (async)
        async->needInput = false;
      if (!sourceInputs++)
        diagnostic("SourceInputTime", S_OK, pts);
      if (InterlockedIncrement64(&totalInputs) == 1)
        diagnostic("FirstInput");
    }
    if (SUCCEEDED(hr))
      pumpLocked();
    return hr;
  }
  HRESULT produce(DWORD flags, DWORD count, MFT_OUTPUT_DATA_BUFFER *out,
                  DWORD *status) {
    if (!out || !status)
      return E_POINTER;
    *status = 0;
    if (flags || count != 1)
      return E_INVALIDARG;
    if (out[0].dwStreamID)
      return MF_E_INVALIDSTREAMNUMBER;
    out[0].dwStatus = 0;
    out[0].pEvents = nullptr;
    std::lock_guard lock(mutex);
    if (!input || !output || !decoder.context)
      return MF_E_TRANSFORM_TYPE_NOT_SET;
    if (async && !async->haveOutput)
      return E_UNEXPECTED;
    NspFrame frame{};
    int ready = decoder.front(decoder.context, &frame);
    if (ready == 0)
      return async ? E_UNEXPECTED : MF_E_TRANSFORM_NEED_MORE_INPUT;
    if (ready < 0)
      return E_FAIL;
    if (frame.rate != 48000)
      return MF_E_TRANSFORM_STREAM_CHANGE;
    if (!preview && !spatialEnabled)
      return MF_E_INVALIDREQUEST;
    if (spatialEnabled) {
      if (!spatial)
        return MF_E_NOT_INITIALIZED;
      if (FAILED(spatial->error))
        return spatial->error.load();
      if (!spatial->capacity(frame.samples))
        return async ? E_UNEXPECTED : MF_E_NOTACCEPTING;
    }
    DWORD bytes = frame.samples * 2 * (bits / 8);
    IMFSample *sample = out[0].pSample;
    bool own = sample == nullptr;
    HRESULT hr = own ? MFCreateSample(&sample) : S_OK;
    IMFMediaBuffer *buffer = nullptr;
    if (SUCCEEDED(hr))
      hr = MFCreateMemoryBuffer(bytes, &buffer);
    if (FAILED(hr)) {
      if (own && sample)
        sample->Release();
      return hr;
    }
    BYTE *data = nullptr;
    hr = buffer->Lock(&data, nullptr, nullptr);
    if (SUCCEEDED(hr)) {
      if (spatialEnabled) {
        memset(data, 0, bytes);
      } else {
        // Preview only: retains Size in decoder-owned events; does not claim
        // LIRA rendering.
        for (UINT32 i = 0; i < frame.event_count; i++)
          for (UINT32 j = 0; j < frame.object_count; j++)
            if (frame.objects[j].id == frame.events[i].id) {
              UINT32 ch = frame.objects[j].channel;
              if (ch < poses.size()) {
                if (frame.events[i].flags & 1)
                  poses[ch].pan = std::clamp(frame.events[i].pos[0], -1., 1.);
                poses[ch].gain = pow(10., frame.events[i].gain_db / 20.);
              }
            }
        double normalization = 1. / sqrt((double)frame.channels);
        for (UINT32 n = 0; n < frame.samples; n++) {
          double l = 0, r = 0;
          for (UINT32 ch = 0; ch < frame.channels; ch++) {
            double pan = frame.labels[ch] == 24 ? poses[ch].pan : 0;
            if (frame.labels[ch] == 0 || frame.labels[ch] == 4 ||
                frame.labels[ch] == 14)
              pan = -1;
            if (frame.labels[ch] == 1 || frame.labels[ch] == 5 ||
                frame.labels[ch] == 15)
              pan = 1;
            double x = frame.pcm[n * frame.channels + ch] / 8388608. *
                       poses[ch].gain * normalization;
            l += x * sqrt((1 - pan) * .5);
            r += x * sqrt((1 + pan) * .5);
          }
          l = std::clamp(l, -1., 1.);
          r = std::clamp(r, -1., 1.);
          if (bits == 16) {
            reinterpret_cast<short *>(data)[n * 2] = (short)lrint(l * 32767);
            reinterpret_cast<short *>(data)[n * 2 + 1] =
                (short)lrint(r * 32767);
          } else {
            reinterpret_cast<float *>(data)[n * 2] = (float)l;
            reinterpret_cast<float *>(data)[n * 2 + 1] = (float)r;
          }
        }
      }
      buffer->Unlock();
      hr = buffer->SetCurrentLength(bytes);
    }
    if (SUCCEEDED(hr))
      hr = sample->RemoveAllBuffers();
    if (SUCCEEDED(hr))
      hr = sample->AddBuffer(buffer);
    buffer->Release();
    if (SUCCEEDED(hr)) {
      sample->SetSampleTime(frame.pts);
      sample->SetSampleDuration((LONGLONG)frame.samples * 10000000 /
                                frame.rate);
      if (discontinuity) {
        sample->SetUINT32(MFSampleExtension_Discontinuity, TRUE);
        discontinuity = false;
      }
      if (spatialEnabled) {
        hr = spatial->enqueue(frame);
        if (FAILED(hr)) {
          if (own)
            sample->Release();
          return hr;
        }
      }
      InterlockedAdd64(&totalEvents, frame.event_count);
      if (InterlockedIncrement64(&totalOutputs) == 1)
        diagnostic("FirstOutput");
      if (!spatialEnabled)
        decoder.pop(decoder.context);
      if (async)
        async->haveOutput = false;
      out[0].pSample = sample;
      if (!async && decoder.ready())
        out[0].dwStatus = MFT_OUTPUT_DATA_BUFFER_INCOMPLETE;
    } else if (own)
      sample->Release();
    if (SUCCEEDED(hr))
      pumpLocked();
    return hr;
  }
  HRESULT access() {
    if (!async)
      return S_OK;
    if (async->shutdown)
      return MF_E_SHUTDOWN;
    UINT32 unlocked = 0;
    if (attrs)
      attrs->GetUINT32(MF_TRANSFORM_ASYNC_UNLOCK, &unlocked);
    return unlocked ? S_OK : MF_E_TRANSFORM_ASYNC_LOCKED;
  }
  void pumpLocked() {
    if (!async || async->shutdown || async->errorSent || !input || !output ||
        !decoder.context)
      return;
    HRESULT hr = spatial ? spatial->error.load() : S_OK;
    if (FAILED(hr)) {
      async->event(MEError, hr);
      async->errorSent = true;
      return;
    }
    if (!async->streaming && !draining)
      return;
    NspFrame frame{};
    int ready = decoder.front(decoder.context, &frame);
    if (ready > 0) {
      if (!async->haveOutput && spatial) {
        // Arm before checking: a concurrent capacity recovery cannot be lost.
        async->progress->armed.store(true, std::memory_order_release);
        if (spatial->capacity(frame.samples)) {
          async->progress->armed.store(false, std::memory_order_relaxed);
          if (SUCCEEDED(async->event(METransformHaveOutput)))
            async->haveOutput = true;
        }
      }
      return;
    }
    if (ready < 0) {
      async->event(MEError, E_FAIL);
      async->errorSent = true;
      return;
    }
    while (!async->markers.empty()) {
      IMFMediaEvent *marker = nullptr;
      if (SUCCEEDED(MFCreateMediaEvent(METransformMarker, GUID_NULL, S_OK,
                                       nullptr, &marker))) {
        marker->SetUINT64(MF_EVENT_MFT_CONTEXT, async->markers.front());
        async->events->QueueEvent(marker);
        marker->Release();
      }
      async->markers.pop_front();
    }
    if (draining) {
      if (!async->drainSent) {
        async->event(METransformDrainComplete);
        async->drainSent = true;
      }
      return;
    }
    if (async->streaming && !async->needInput) {
      if (SUCCEEDED(async->event(METransformNeedInput)))
        async->needInput = true;
    }
  }
  HRESULT shutdownMft() {
    if (!async)
      return E_NOINTERFACE;
    async->shutdown = true;
    async->stop();
    std::lock_guard guard(mutex);
    if (spatial)
      diagnostic("SpatialSubmitResult", spatial->error.load(), 0,
                 spatial->submitted, spatial->nonzero);
    spatial.reset();
    if (boundClock) {
      boundClock->Release();
      boundClock = nullptr;
    }
    decoder.close();
    async->completed = true;
    return S_OK;
  }
  HRESULT bindClock(IMFPresentationClock *clock, LPCWSTR endpoint,
                    LONGLONG offset) {
    if (async) {
      HRESULT hr = async->start();
      if (FAILED(hr))
        return hr;
    }
    std::lock_guard guard(mutex);
    if (async && async->shutdown)
      return MF_E_SHUTDOWN;
    if (!spatialEnabled)
      return MF_E_INVALIDREQUEST;
    if (!clock) {
      spatial.reset();
      if (boundClock)
        boundClock->Release();
      boundClock = nullptr;
      return S_OK;
    }
    if (!endpoint || !*endpoint)
      return E_INVALIDARG;
    IUnknown *identity = nullptr;
    HRESULT hr = clock->QueryInterface(IID_PPV_ARGS(&identity));
    if (FAILED(hr))
      return hr;
    if (identity == boundClock && boundEndpoint == endpoint &&
        boundOffset == offset) {
      identity->Release();
      return S_FALSE;
    }
    auto next = std::make_unique<SpatialSubmitter>(
        decoder, async ? async->progress : nullptr);
    hr = next->bind(clock, endpoint, offset);
    if (FAILED(hr)) {
      identity->Release();
      diagnostic("SpatialClockBindFailed", hr);
      return hr;
    }
    spatial = std::move(next);
    if (boundClock)
      boundClock->Release();
    boundClock = identity;
    boundEndpoint = endpoint;
    boundOffset = offset;
    diagnostic("SpatialClockBound");
    pumpLocked();
    return S_OK;
  }
  HRESULT spatialStatistics(UINT64 *frames, UINT64 *values, HRESULT *result) {
    if (!frames || !values || !result)
      return E_POINTER;
    std::lock_guard guard(mutex);
    if (!spatial)
      return MF_E_NOT_INITIALIZED;
    *frames = spatial->submitted;
    *values = spatial->nonzero;
    *result = spatial->error;
    return S_OK;
  }
};
