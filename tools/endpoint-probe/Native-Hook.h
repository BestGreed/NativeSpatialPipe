#pragma once
#define _WIN32_WINNT 0x0A00
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#define NSP_HOOK_MAGIC 0x4e534831
typedef struct {
 uint32_t magic,size,fixture,duration_ms;uint64_t budget;
 wchar_t capture[1024],send[1024],mapping[256],event[256];
 uint32_t error,win32;char output[10000];
} HookConfig;
