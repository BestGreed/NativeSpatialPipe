// SPDX-License-Identifier: GPL-3.0-or-later
#include "../../../work/minhook/include/MinHook.h"
#include "decoder.h"
#include <cwctype>
#include <initializer_list>
static IClassFactory *factory = nullptr;
static HMODULE mft = nullptr;
static void *target = nullptr;
static bool registered = false, created = false, hooked = false,
            ownsEngine = false;
static volatile LONG callbacks = 0, running = 0;
static HANDLE watcher = nullptr;
static SRWLOCK control = SRWLOCK_INIT;
using EnumFn = HRESULT(WINAPI *)(GUID, UINT32, const MFT_REGISTER_TYPE_INFO *,
                                 const MFT_REGISTER_TYPE_INFO *,
                                 IMFActivate ***, UINT32 *);
static EnumFn enumerateOriginal = nullptr;
#include "session_clock_route.h"
static bool matchesProcess() {
  wchar_t image[32768];
  GetModuleFileNameW(nullptr, image, 32768);
  for (auto *p = image; *p; p++)
    *p = towlower(*p);
  wchar_t config[32768];
  sibling(config, L"nsp-mft.ini");
  if (!GetPrivateProfileIntW(L"routing", L"enabled", 0, config))
    return false;
  return (wcsstr(image, L"\\windowsapps\\appleinc.applemusicwin_") &&
          wcsstr(image, L"\\amplibraryagent.exe") &&
          GetPrivateProfileIntW(L"routing", L"appleMusic", 0, config)) ||
         (wcsstr(image, L"\\windowsapps\\microsoft.zunevideo_") &&
          wcsstr(image, L"\\video.ui.exe") &&
          GetPrivateProfileIntW(L"routing", L"filmsTV", 0, config));
}
static HRESULT WINAPI enumerate(GUID category, UINT32 flags,
                                const MFT_REGISTER_TYPE_INFO *input,
                                const MFT_REGISTER_TYPE_INFO *output,
                                IMFActivate ***items, UINT32 *count) {
  InterlockedIncrement(&callbacks);
  bool match = category == MFT_CATEGORY_AUDIO_DECODER &&
               (!input || (input->guidMajorType == MFMediaType_Audio &&
                           (input->guidSubtype == MFAudioFormat_Dolby_DDPlus ||
                            input->guidSubtype == TrueHD ||
                            input->guidSubtype == MFAudioFormat_Dolby_AC3))) &&
               (!output || (output->guidMajorType == MFMediaType_Audio &&
                            (output->guidSubtype == MFAudioFormat_PCM ||
                             output->guidSubtype == MFAudioFormat_Float)));
  HRESULT hr = enumerateOriginal(
      category,
      match ? flags | MFT_ENUM_FLAG_LOCALMFT |
                  (configuredAsync() ? MFT_ENUM_FLAG_ASYNCMFT : 0)
            : flags,
      input, output, items, count);
  if (match && SUCCEEDED(hr) && items && count) {
    for (UINT32 i = 0; i < *count; i++) {
      GUID cls{};
      auto *activation = (*items)[i];
      if (SUCCEEDED(activation->GetGUID(MFT_TRANSFORM_CLSID_Attribute, &cls)) &&
          cls == Class) {
        activation->SetString(MFT_FRIENDLY_NAME_Attribute, FriendlyName);
        if (i) {
          auto *own = activation;
          memmove(*items + 1, *items, i * sizeof(IMFActivate *));
          (*items)[0] = own;
        }
        break;
      }
    }
  }
  InterlockedDecrement(&callbacks);
  return hr;
}
static HRESULT stopInternal() {
  AcquireSRWLockExclusive(&control);
  HRESULT hr = S_OK;
  disableClockRouting();
  if (hooked) {
    if (MH_DisableHook(target) != MH_OK) {
      ReleaseSRWLockExclusive(&control);
      return E_FAIL;
    }
    hooked = false;
  }
  ULONGLONG end = GetTickCount64() + 2000;
  while (callbacks && GetTickCount64() < end)
    Sleep(1);
  if (callbacks) {
    ReleaseSRWLockExclusive(&control);
    return HRESULT_FROM_WIN32(ERROR_BUSY);
  }
  if (registered) {
    hr = MFTUnregisterLocalByCLSID(Class);
    if (FAILED(hr)) {
      ReleaseSRWLockExclusive(&control);
      return hr;
    }
    registered = false;
  }
  if (created) {
    if (MH_RemoveHook(target) != MH_OK) {
      ReleaseSRWLockExclusive(&control);
      return E_FAIL;
    }
    created = false;
  }
  removeClockRouting();
  if (ownsEngine) {
    MH_Uninitialize();
    ownsEngine = false;
  }
  if (factory) {
    factory->Release();
    factory = nullptr;
  }
  if (mft) {
    auto unload = (HRESULT(WINAPI *)())GetProcAddress(mft, "DllCanUnloadNow");
    if (unload && unload() == S_OK) {
      FreeLibrary(mft);
      mft = nullptr;
    }
  }
  running = 0;
  ReleaseSRWLockExclusive(&control);
  return hr;
}
static DWORD WINAPI watch(LPVOID) {
  HRESULT co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  while (running) {
    Sleep(500);
    if (!matchesProcess()) {
      if (SUCCEEDED(stopInternal()))
        break;
    }
  }
  if (SUCCEEDED(co))
    CoUninitialize();
  return 0;
}
extern "C" __declspec(dllexport) DWORD WINAPI NspRouteStopThread(LPVOID) {
  HRESULT hr = stopInternal();
  if (watcher) {
    if (WaitForSingleObject(watcher, 3000) != WAIT_OBJECT_0)
      return HRESULT_FROM_WIN32(ERROR_BUSY);
    CloseHandle(watcher);
    watcher = nullptr;
  }
  return hr;
}
extern "C" __declspec(dllexport) DWORD WINAPI NspRouteStatusThread(LPVOID) {
  return (running ? 1 : 0) | (hooked ? 2 : 0) | (registered ? 4 : 0) |
         (mft ? 8 : 0);
}
extern "C" __declspec(dllexport) DWORD WINAPI NspRouteStartThread(LPVOID) {
  if (!matchesProcess())
    return E_ACCESSDENIED;
  AcquireSRWLockExclusive(&control);
  if (running || created || registered) {
    ReleaseSRWLockExclusive(&control);
    return S_FALSE;
  }
  HRESULT co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  if (FAILED(co)) {
    ReleaseSRWLockExclusive(&control);
    return co;
  }
  wchar_t path[32768];
  sibling(path, L"NspDolbyMft.dll");
  mft = LoadLibraryExW(path, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
  HRESULT hr = mft ? S_OK : HRESULT_FROM_WIN32(GetLastError());
  auto get =
      mft ? reinterpret_cast<HRESULT(WINAPI *)(REFCLSID, REFIID, void **)>(
                GetProcAddress(mft, "DllGetClassObject"))
          : nullptr;
  if (SUCCEEDED(hr))
    hr = get ? get(Class, IID_IClassFactory, (void **)&factory) : E_NOINTERFACE;
  if (SUCCEEDED(hr)) {
    MFT_REGISTER_TYPE_INFO in[] = {{MFMediaType_Audio,
                                    MFAudioFormat_Dolby_DDPlus},
                                   {MFMediaType_Audio, TrueHD},
                                   {MFMediaType_Audio,
                                    MFAudioFormat_Dolby_AC3}},
                           out[] = {{MFMediaType_Audio, MFAudioFormat_PCM},
                                    {MFMediaType_Audio, MFAudioFormat_Float}};
    hr = MFTRegisterLocalByCLSID(
        Class, MFT_CATEGORY_AUDIO_DECODER, FriendlyName,
        (configuredAsync() ? MFT_ENUM_FLAG_ASYNCMFT : MFT_ENUM_FLAG_SYNCMFT), 3,
        in, 2, out);
    registered = SUCCEEDED(hr);
  }
  wchar_t config[32768];
  sibling(config, L"nsp-mft.ini");
  bool useHook =
      GetPrivateProfileIntW(L"routing", L"hookEnumeration", 0, config) != 0;
  wchar_t image[32768];
  GetModuleFileNameW(nullptr, image, 32768);
  auto *leaf = wcsrchr(image, 92);
  bool films = leaf && _wcsicmp(leaf + 1, L"Video.UI.exe") == 0;
  if (useHook)
    useHook = GetPrivateProfileIntW(L"routing",
                                    films ? L"hookFilmsTV" : L"hookAppleMusic",
                                    films ? 0 : 1, config) != 0;
  bool useClock =
      GetPrivateProfileIntW(L"routing", L"bindSessionClock", 0, config) != 0;
  // CLSID registration retains a stable identity for targeted candidate
  // ordering.
  if (SUCCEEDED(hr) && (useHook || useClock)) {
    auto status = MH_Initialize();
    ownsEngine = status == MH_OK;
    if (status != MH_OK && status != MH_ERROR_ALREADY_INITIALIZED)
      hr = E_FAIL;
  }
  if (SUCCEEDED(hr) && useHook) {
    target =
        (void *)GetProcAddress(GetModuleHandleW(L"mfplat.dll"), "MFTEnumEx");
    if (!target || MH_CreateHook(target, (void *)&enumerate,
                                 (void **)&enumerateOriginal) != MH_OK)
      hr = E_FAIL;
    else
      created = true;
  }
  if (SUCCEEDED(hr) && useHook) {
    if (MH_EnableHook(target) != MH_OK)
      hr = E_FAIL;
    else
      hooked = true;
  }
  if (SUCCEEDED(hr) && useClock)
    hr = startClockRouting();
  if (SUCCEEDED(hr)) {
    running = 1;
    watcher = CreateThread(nullptr, 0, watch, nullptr, 0, nullptr);
    if (!watcher)
      hr = HRESULT_FROM_WIN32(GetLastError());
  }
  CoUninitialize();
  ReleaseSRWLockExclusive(&control);
  if (FAILED(hr))
    stopInternal();
  return hr;
}
BOOL WINAPI DllMain(HINSTANCE h, DWORD why, LPVOID) {
  if (why == DLL_PROCESS_ATTACH) {
    module = h;
    DisableThreadLibraryCalls(h);
  }
  return TRUE;
}
