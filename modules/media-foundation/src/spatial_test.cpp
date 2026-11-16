// SPDX-License-Identifier: GPL-3.0-or-later
#include "../include/nsp_spatial_control.h"
#include "decoder.h"
#include <cassert>
#include <cstdio>
#include <fstream>
#include <vector>
static void check(HRESULT hr, const char *stage) {
  if (FAILED(hr)) {
    printf("{\"stage\":\"%s\",\"hr\":\"0x%08lx\"}\n", stage, (unsigned long)hr);
    fflush(stdout);
    exit(1);
  }
}
int wmain(int argc, wchar_t **argv) {
  if (argc != 5) {
    fprintf(stderr,
            "usage: spatial_test MFT.dll INPUT.bpk eac3|truehd ENDPOINT\n");
    return 2;
  }
  check(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "COM");
  check(MFStartup(MF_VERSION), "MFStartup");
  bool truehd = !wcscmp(argv[3], L"truehd");
  std::ifstream file(argv[2], std::ios::binary);
  char magic[4];
  UINT32 count = 0;
  file.read(magic, 4);
  file.read((char *)&count, 4);
  assert(!memcmp(magic, "BPK1", 4) && count > 3 && count < 20000);
  std::vector<std::vector<char>> packets;
  for (UINT32 i = 0; i < count; i++) {
    UINT32 n = 0;
    file.read((char *)&n, 4);
    assert(n && n <= 65536);
    packets.emplace_back(n);
    file.read(packets.back().data(), n);
    assert(file);
  }
  HMODULE dll = LoadLibraryExW(argv[1], nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
  if (!dll) {
    printf("{\"loadError\":%lu}\n", GetLastError());
    return 3;
  }
  auto get = (HRESULT(WINAPI *)(REFCLSID, REFIID, void **))GetProcAddress(
      dll, "DllGetClassObject");
  IClassFactory *factory = nullptr;
  check(get(Class, IID_IClassFactory, (void **)&factory), "factory");
  IMFTransform *transform = nullptr;
  check(factory->CreateInstance(nullptr, IID_IMFTransform, (void **)&transform),
        "transform");
  factory->Release();
  IMFAttributes *attributes = nullptr;
  check(transform->GetAttributes(&attributes), "attributes");
  UINT32 asynchronous = 0;
  attributes->GetUINT32(MF_TRANSFORM_ASYNC, &asynchronous);
  if (asynchronous) {
    assert(transform->GetOutputStatus(nullptr) == MF_E_TRANSFORM_ASYNC_LOCKED);
    check(attributes->SetUINT32(MF_TRANSFORM_ASYNC_UNLOCK, TRUE), "unlock");
  }
  attributes->Release();
  IMFMediaEventGenerator *events = nullptr;
  IMFShutdown *shutdown = nullptr;
  if (asynchronous) {
    check(transform->QueryInterface(IID_PPV_ARGS(&events)), "events");
    check(transform->QueryInterface(IID_PPV_ARGS(&shutdown)), "shutdown");
  }
  INspSpatialControl *control = nullptr;
  check(transform->QueryInterface(IID_INspSpatialControl, (void **)&control),
        "control");
  IMFMediaType *type = nullptr;
  check(MFCreateMediaType(&type), "input type");
  type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Audio);
  type->SetGUID(MF_MT_SUBTYPE, truehd ? TrueHD : MFAudioFormat_Dolby_DDPlus);
  type->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, 48000);
  check(transform->SetInputType(0, type, 0), "SetInput");
  type->Release();
  check(transform->GetOutputAvailableType(0, 1, &type), "OutputType");
  check(transform->SetOutputType(0, type, 0), "SetOutput");
  type->Release();
  IMFPresentationClock *clock = nullptr;
  IMFPresentationTimeSource *source = nullptr;
  check(MFCreatePresentationClock(&clock), "clock");
  check(MFCreateSystemTimeSource(&source), "time source");
  check(clock->SetTimeSource(source), "SetTimeSource");
  source->Release();
  check(control->BindPresentationClock(clock, argv[4], 0), "BindClock");
  printf("{\"ready\":true,\"pid\":%lu,\"codec\":\"%s\"}\n",
         GetCurrentProcessId(), truehd ? "truehd" : "eac3");
  fflush(stdout);
  UINT64 totalSamples = 0;
  UINT64 silentCarrierBytes = 0;
  bool pauseChecked = false, prerollChecked = false;
  DWORD startedAt = 0;
  LONGLONG base = 0;
  for (UINT32 segment = 0; segment < 2; segment++) {
    check(transform->ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0), "Flush");
    check(transform->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0),
          "StartOfStream");
    base = segment ? 600000000 : 0;
    LONGLONG firstPts = base + 5000000;
    UINT64 segmentSamples = 0;
    bool needInput = false, drained = false;
    auto drain = [&]() {
      for (;;) {
        if (startedAt && !pauseChecked && GetTickCount() - startedAt > 1400) {
          check(clock->Pause(), "Pause");
          Sleep(60);
          UINT64 before = 0, after = 0, values = 0;
          HRESULT result = S_OK;
          check(control->GetSpatialStatistics(&before, &values, &result),
                "paused statistics");
          check(result, "worker paused");
          Sleep(250);
          check(control->GetSpatialStatistics(&after, &values, &result),
                "pause check");
          assert(before == after);
          LONGLONG position = 0;
          check(clock->GetTime(&position), "paused time");
          check(clock->Start(position), "Resume");
          pauseChecked = true;
          printf("{\"pauseStable\":true}\n");
          fflush(stdout);
        }
        if (startedAt && GetTickCount() - startedAt > 14000) {
          fprintf(stderr, "segment watchdog\n");
          exit(4);
        }
        if (asynchronous) {
          IMFMediaEvent *event = nullptr;
          HRESULT queued = events->GetEvent(MF_EVENT_FLAG_NO_WAIT, &event);
          if (queued == MF_E_NO_EVENTS_AVAILABLE)
            break;
          check(queued, "GetEvent");
          MediaEventType kind = MEUnknown;
          HRESULT status = S_OK;
          event->GetType(&kind);
          event->GetStatus(&status);
          PROPVARIANT value{};
          check(event->GetValue(&value), "event value");
          assert(value.vt == VT_EMPTY);
          if (kind == METransformNeedInput || kind == METransformDrainComplete) {
            UINT32 streamId = UINT32_MAX;
            check(event->GetUINT32(MF_EVENT_MFT_INPUT_STREAM_ID, &streamId),
                  "event input stream ID");
            assert(streamId == 0);
          }
          event->Release();
          check(status, "event status");
          if (kind == METransformNeedInput) {
            needInput = true;
            continue;
          }
          if (kind == METransformDrainComplete) {
            drained = true;
            continue;
          }
          if (kind != METransformHaveOutput)
            continue;
        }
        MFT_OUTPUT_DATA_BUFFER output{};
        DWORD status = 0;
        HRESULT hr = transform->ProcessOutput(0, 1, &output, &status);
        if (asynchronous)
          assert(hr != MF_E_TRANSFORM_NEED_MORE_INPUT && hr != MF_E_NOTACCEPTING);
        if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT)
          break;
        if (hr == MF_E_NOTACCEPTING) {
          Sleep(1);
        } else {
          check(hr, "ProcessOutput");
          assert(output.pSample);
          LONGLONG duration = 0;
          check(output.pSample->GetSampleDuration(&duration),
                "output duration");
          IMFMediaBuffer *buffer = nullptr;
          check(output.pSample->ConvertToContiguousBuffer(&buffer), "carrier");
          BYTE *p = nullptr;
          DWORD n = 0;
          check(buffer->Lock(&p, nullptr, &n), "carrier lock");
          for (DWORD j = 0; j < n; j++)
            assert(p[j] == 0);
          silentCarrierBytes += n;
          buffer->Unlock();
          buffer->Release();
          UINT64 samples = n / 8;
          segmentSamples += samples;
          totalSamples += samples;
          output.pSample->Release();
        }
        if (startedAt && !pauseChecked && GetTickCount() - startedAt > 1400) {
          check(clock->Pause(), "Pause");
          Sleep(60);
          UINT64 before = 0, after = 0, values = 0;
          HRESULT result = S_OK;
          check(control->GetSpatialStatistics(&before, &values, &result),
                "paused statistics");
          check(result, "worker paused");
          Sleep(250);
          check(control->GetSpatialStatistics(&after, &values, &result),
                "pause check");
          assert(before == after);
          LONGLONG position = 0;
          check(clock->GetTime(&position), "paused time");
          check(clock->Start(position), "Resume");
          pauseChecked = true;
          printf("{\"pauseStable\":true}\n");
          fflush(stdout);
        }
        if (startedAt && GetTickCount() - startedAt > 14000) {
          fprintf(stderr, "segment watchdog\n");
          exit(4);
        }
      }
    };
    startedAt = 0;
    for (UINT32 i = 0; i < packets.size(); i++) {
      if (i == (truehd ? 100u : 3u)) {
        UINT64 submitted = 0, values = 0;
        HRESULT result = S_OK;
        Sleep(80);
        check(control->GetSpatialStatistics(&submitted, &values, &result),
              "preroll stats");
        check(result, "preroll worker");
        if (segment == 0) {
          assert(submitted == 0 && values == 0);
          prerollChecked = true;
        }
        check(clock->Start(base), "ClockStart");
        startedAt = GetTickCount();
      }
      if (asynchronous) {
        while (!needInput) {
          drain();
          if (!needInput)
            Sleep(1);
        }
        needInput = false;
      }
      auto &data = packets[i];
      IMFSample *sample = nullptr;
      IMFMediaBuffer *buffer = nullptr;
      check(MFCreateSample(&sample), "sample");
      check(MFCreateMemoryBuffer((DWORD)data.size(), &buffer), "buffer");
      BYTE *p = nullptr;
      check(buffer->Lock(&p, nullptr, nullptr), "lock");
      memcpy(p, data.data(), data.size());
      buffer->Unlock();
      buffer->SetCurrentLength((DWORD)data.size());
      sample->AddBuffer(buffer);
      buffer->Release();
      sample->SetSampleTime(firstPts);
      check(transform->ProcessInput(0, sample, 0), "ProcessInput");
      sample->Release();
      drain();
    }
    if (asynchronous) {
      check(transform->ProcessMessage(MFT_MESSAGE_NOTIFY_END_OF_STREAM, 0),
            "EndOfStream");
      check(transform->ProcessMessage(MFT_MESSAGE_COMMAND_DRAIN, 0), "Drain");
      while (!drained) {
        drain();
        Sleep(1);
      }
    }
    LONGLONG end = firstPts + (LONGLONG)segmentSamples * 10000000 / 48000 +
                   1500000,
             position = 0;
    do {
      Sleep(5);
      check(clock->GetTime(&position), "wait clock");
      if (GetTickCount() - startedAt > 14000)
        exit(5);
    } while (position < end);
    check(clock->Stop(), "ClockStop");
    printf("{\"segment\":%u,\"samples\":%llu,\"sourceAnchor\":%lld}\n", segment,
           segmentSamples, firstPts);
    fflush(stdout);
  }
  UINT64 frames = 0, values = 0;
  HRESULT result = S_OK;
  check(control->GetSpatialStatistics(&frames, &values, &result), "stats");
  check(result, "worker result");
  assert(frames && values && prerollChecked && pauseChecked);
  check(control->BindPresentationClock(nullptr, nullptr, 0), "unbind");
  control->Release();
  if (shutdown) {
    check(shutdown->Shutdown(), "Shutdown");
    MFSHUTDOWN_STATUS status;
    check(shutdown->GetShutdownStatus(&status), "ShutdownStatus");
    assert(status == MFSHUTDOWN_COMPLETED);
    shutdown->Release();
  }
  if (events)
    events->Release();
  transform->Release();
  clock->Release();
  auto unload = (HRESULT(WINAPI *)())GetProcAddress(dll, "DllCanUnloadNow");
  assert(unload() == S_OK);
  FreeLibrary(dll);
  MFShutdown();
  CoUninitialize();
  printf("{\"passed\":true,\"clockPrerollPauseSeekPassed\":true,"
         "\"submittedFrames\":%llu,\"nonzeroObjectPcmValues\":%llu,"
         "\"decodedSamples\":%llu,\"silentClockCarrierBytes\":%llu}\n",
         frames, values, totalSamples, silentCarrierBytes);
  return 0;
}
