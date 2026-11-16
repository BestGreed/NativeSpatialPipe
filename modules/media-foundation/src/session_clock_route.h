// SPDX-License-Identifier: GPL-3.0-or-later
// Bind only transforms found in this exact resolved session topology.
#pragma once
#include "../include/nsp_spatial_control.h"
#include <cstdio>
static void *clockCreateTarget = nullptr;
static void *sessionStartTarget = nullptr;
using CreateSessionFn = HRESULT(WINAPI *)(IMFAttributes *, IMFMediaSession **);
using StartSessionFn = HRESULT(STDMETHODCALLTYPE *)(IMFMediaSession *,
                                                    const GUID *,
                                                    const PROPVARIANT *);
static CreateSessionFn createSessionOriginal = nullptr;
static StartSessionFn sessionStartOriginal = nullptr;
static SRWLOCK sessionHookLock = SRWLOCK_INIT;
static volatile LONG clockRoutingStopping = 1;
static void clockRouteLog(const char *event, HRESULT hr, UINT32 found = 0,
                          LONGLONG time = 0) {
  wchar_t path[32768];
  DWORD n = GetTempPathW(32000, path);
  if (!n || n > 32000)
    return;
  swprintf(path + n, 32768 - n, L"nsp-clock-route-%lu.jsonl",
           GetCurrentProcessId());
  HANDLE f =
      CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                  nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (f == INVALID_HANDLE_VALUE)
    return;
  LARGE_INTEGER size{};
  GetFileSizeEx(f, &size);
  if (size.QuadPart < 65536) {
    char line[256];
    int length = snprintf(
        line, sizeof(line),
        "{\"event\":\"%s\",\"hr\":%ld,\"found\":%u,\"time100ns\":%lld}\n",
        event, hr, found, time);
    DWORD done;
    WriteFile(f, line, length, &done, nullptr);
  }
  CloseHandle(f);
}
static HRESULT bindSession(IMFMediaSession *session) {
  IMFTopology *topology = nullptr;
  IMFClock *base = nullptr;
  IMFPresentationClock *clock = nullptr;
  HRESULT hr =
      session->GetFullTopology(MFSESSION_GETFULLTOPOLOGY_CURRENT, 0, &topology);
  if (SUCCEEDED(hr))
    hr = session->GetClock(&base);
  if (SUCCEEDED(hr))
    hr = base->QueryInterface(IID_PPV_ARGS(&clock));
  UINT32 found = 0;
  if (SUCCEEDED(hr)) {
    WORD count = 0;
    hr = topology->GetNodeCount(&count);
    wchar_t config[32768], endpoint[512], offset[64];
    sibling(config, L"nsp-mft.ini");
    GetPrivateProfileStringW(L"spatial", L"endpoint", L"", endpoint, 512,
                             config);
    GetPrivateProfileStringW(L"spatial", L"sourceToClockOffset100ns", L"0",
                             offset, 64, config);
    if (count > 256)
      hr = E_UNEXPECTED;
    for (WORD i = 0; i < count && SUCCEEDED(hr); i++) {
      IMFTopologyNode *node = nullptr;
      IUnknown *object = nullptr;
      INspSpatialControl *control = nullptr;
      HRESULT one = topology->GetNode(i, &node);
      if (SUCCEEDED(one))
        one = node->GetObject(&object);
      if (SUCCEEDED(one))
        one = object->QueryInterface(IID_INspSpatialControl, (void **)&control);
      if (control) {
        found++;
        one = control->BindPresentationClock(clock, endpoint,
                                             _wcstoi64(offset, nullptr, 10));
        clockRouteLog("BindTopologyTransform", one, found);
        if (FAILED(one))
          hr = one;
        control->Release();
      }
      if (object)
        object->Release();
      if (node)
        node->Release();
    }
  }
  if (clock)
    clock->Release();
  if (base)
    base->Release();
  if (topology)
    topology->Release();
  clockRouteLog("SessionTopology", hr, found);
  return hr;
}
static HRESULT STDMETHODCALLTYPE sessionStart(IMFMediaSession *session,
                                              const GUID *format,
                                              const PROPVARIANT *position) {
  InterlockedIncrement(&callbacks);
  HRESULT bound = bindSession(session);
  clockRouteLog("BeforeSessionStart", bound, 0,
                position && position->vt == VT_I8 ? position->hVal.QuadPart
                                                  : 0);
  HRESULT hr = sessionStartOriginal(session, format, position);
  InterlockedDecrement(&callbacks);
  return hr;
}
static HRESULT WINAPI createSession(IMFAttributes *attrs,
                                    IMFMediaSession **out) {
  InterlockedIncrement(&callbacks);
  HRESULT hr = createSessionOriginal(attrs, out);
  if (SUCCEEDED(hr) && out && *out) {
    void *start = (*reinterpret_cast<void ***>(*out))[9];
    AcquireSRWLockExclusive(&sessionHookLock);
    if (!InterlockedCompareExchange(&clockRoutingStopping, 0, 0) &&
        !sessionStartTarget) {
      if (MH_CreateHook(start, (void *)&sessionStart,
                        (void **)&sessionStartOriginal) == MH_OK) {
        sessionStartTarget = start;
        if (MH_EnableHook(start) != MH_OK)
          clockRouteLog("StartHookEnable", E_FAIL);
      } else
        clockRouteLog("StartHookCreate", E_FAIL);
    }
    ReleaseSRWLockExclusive(&sessionHookLock);
  }
  clockRouteLog("MFCreateMediaSession", hr);
  InterlockedDecrement(&callbacks);
  return hr;
}
static void disableClockRouting() {
  InterlockedExchange(&clockRoutingStopping, 1);
  if (clockCreateTarget)
    MH_DisableHook(clockCreateTarget);
  AcquireSRWLockExclusive(&sessionHookLock);
  if (sessionStartTarget)
    MH_DisableHook(sessionStartTarget);
  ReleaseSRWLockExclusive(&sessionHookLock);
}
static void removeClockRouting() {
  AcquireSRWLockExclusive(&sessionHookLock);
  for (void **p : {&clockCreateTarget, &sessionStartTarget})
    if (*p) {
      MH_RemoveHook(*p);
      *p = nullptr;
    }
  ReleaseSRWLockExclusive(&sessionHookLock);
}
static HRESULT startClockRouting() {
  HMODULE mf = GetModuleHandleW(L"mf.dll");
  if (!mf)
    mf = LoadLibraryExW(L"mf.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
  if (!mf)
    return HRESULT_FROM_WIN32(GetLastError());
  void *fn = (void *)GetProcAddress(mf, "MFCreateMediaSession");
  if (!fn)
    return E_NOINTERFACE;
  if (MH_CreateHook(fn, (void *)&createSession,
                    (void **)&createSessionOriginal) != MH_OK)
    return E_FAIL;
  clockCreateTarget = fn;
  InterlockedExchange(&clockRoutingStopping, 0);
  if (MH_EnableHook(fn) != MH_OK)
    return E_FAIL;
  return S_OK;
}
