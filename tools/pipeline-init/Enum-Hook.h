#pragma once
#define _WIN32_WINNT 0x0A00
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#define NSP_HOOK_MAGIC 0x454e5331
/* Private control ABI. No PCM, capture or rendering state crosses this boundary. */
typedef struct {
 uint32_t magic,size,fixture,duration_ms;
 uint32_t owner_pid,host_pid;
 uint64_t target_created;
 uint32_t legacy_stopped;
 uint64_t owner_handle,host_handle;
 uint32_t mask,error,win32;
 wchar_t endpoint[512];
 char output[10000];
} HookConfig;
