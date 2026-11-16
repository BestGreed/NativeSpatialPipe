#ifndef AUDIO_LAYOUT_H
#define AUDIO_LAYOUT_H
#include <windows.h>
#include <stdint.h>
#ifdef AL_BUILD_DLL
#define AL_API __declspec(dllexport)
#else
#define AL_API
#endif

/* Versioned, pointer-free snapshot; endpoint IDs are opaque UTF-16 strings.
   Callers initialize COM on their calling thread. No background threads/timers. */
#define AL_VERSION 1
#define AL_FORMAT_CAP 256
typedef struct {
    uint32_t version;
    wchar_t endpoint[1024];
    uint32_t physical_present, physical;
    uint32_t fullrange_present, fullrange;
    uint32_t device_size, mix_size;
    unsigned char device[AL_FORMAT_CAP], mix[AL_FORMAT_CAP];
} AL_Snapshot;

AL_API HRESULT al_capture(const wchar_t *endpoint_or_null, AL_Snapshot *out);
AL_API HRESULT al_plan(const AL_Snapshot *before, uint32_t mask, AL_Snapshot *out);
AL_API HRESULT al_supported(const AL_Snapshot *target);
/* Verified apply. On failure, best-effort restoration of the live pre-write
   snapshot; rollback_result distinguishes successful recovery from failure. */
AL_API HRESULT al_apply(const AL_Snapshot *target, HRESULT *rollback_result);
AL_API int al_equal(const AL_Snapshot *a, const AL_Snapshot *b);
AL_API int al_valid(const AL_Snapshot *s);
AL_API void al_print(const AL_Snapshot *s);
#endif
