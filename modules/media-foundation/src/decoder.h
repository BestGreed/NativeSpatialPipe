// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "../include/nsp_decoder.h"
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mftransform.h>
#include <mutex>
#include <windows.h>
static HMODULE module;
static LONG alive = 0;
static const CLSID Class = {0x7c860c4b,
                            0x817c,
                            0x481f,
                            {0xac, 0x76, 0x6d, 0x4c, 0xc2, 0xb5, 0x54, 0x9d}};
static const GUID TrueHD = {0xeb27cec4,
                            0x163e,
                            0x4ca3,
                            {0x8b, 0x74, 0x8e, 0x25, 0xf9, 0x1b, 0x51, 0x7e}};
static const wchar_t FriendlyName[] =
    L"NSP Dolby Digital Plus Decoder MFT (EAC3 JOC / TrueHD)";
static const wchar_t ClassKey[] =
    L"Software\\Classes\\CLSID\\{7C860C4B-817C-481F-AC76-6D4CC2B5549D}";
static void sibling(wchar_t *out, const wchar_t *name) {
  GetModuleFileNameW(module, out, 32768);
  auto *p = wcsrchr(out, 92);
  if (p)
    wcscpy(p + 1, name);
}
static bool configuredAsync() {
  wchar_t config[32768];
  sibling(config, L"nsp-mft.ini");
  return GetPrivateProfileIntW(L"spatial", L"enabled", 0, config) &&
         GetPrivateProfileIntW(L"spatial", L"asynchronous", 1, config);
}
struct Decoder {
  HMODULE dll = nullptr;
  NspDecoder *context = nullptr;
  decltype(&nsp_decoder_create) create = nullptr;
  decltype(&nsp_decoder_destroy) destroy = nullptr;
  decltype(&nsp_decoder_push) push = nullptr;
  decltype(&nsp_decoder_front) front = nullptr;
  decltype(&nsp_decoder_pop) pop = nullptr;
  decltype(&nsp_decoder_reset) reset = nullptr;
  decltype(&nsp_decoder_take) take = nullptr;
  decltype(&nsp_frame_release) releaseFrame = nullptr;
  decltype(&nsp_frame_view) viewFrame = nullptr;
  decltype(&nsp_frame_start) frameStart = nullptr;
  ~Decoder() {
    close();
    if (dll)
      FreeLibrary(dll);
  }
  void close() {
    if (context) {
      destroy(context);
      context = nullptr;
    }
  }
  HRESULT open(UINT32 codec) {
    close();
    if (!dll) {
      wchar_t path[32768];
      sibling(path, L"nsp_mf_decoder.dll");
      dll = LoadLibraryExW(path, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
      if (!dll)
        return HRESULT_FROM_WIN32(GetLastError());
#define LOAD(name)                                                             \
  name = reinterpret_cast<decltype(name)>(                                     \
      GetProcAddress(dll, "nsp_decoder_" #name));                              \
  if (!name)                                                                   \
  return E_NOINTERFACE
      LOAD(create);
      LOAD(destroy);
      LOAD(push);
      LOAD(front);
      LOAD(pop);
      LOAD(reset);
      LOAD(take);
#undef LOAD
      releaseFrame = reinterpret_cast<decltype(releaseFrame)>(
          GetProcAddress(dll, "nsp_frame_release"));
      viewFrame = reinterpret_cast<decltype(viewFrame)>(
          GetProcAddress(dll, "nsp_frame_view"));
      frameStart = reinterpret_cast<decltype(frameStart)>(
          GetProcAddress(dll, "nsp_frame_start"));
    }
    if (!create || !destroy || !push || !front || !pop || !reset || !take ||
        !releaseFrame || !viewFrame || !frameStart)
      return E_NOINTERFACE;
    context = create(codec);
    return context ? S_OK : E_FAIL;
  }
  bool ready() {
    NspFrame frame{};
    return context && front(context, &frame) > 0;
  }
};
