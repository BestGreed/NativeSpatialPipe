#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <ks.h>
#include <ksmedia.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include "audio_layout.h"

/* Windows policy ABI. Undocumented: isolated here, never injected into another
   process. Current Windows ABI includes BOOL fx_store for property methods. */
typedef struct Policy Policy;
typedef struct {
    HRESULT (STDMETHODCALLTYPE *query)(Policy*, REFIID, void**);
    ULONG (STDMETHODCALLTYPE *addref)(Policy*);
    ULONG (STDMETHODCALLTYPE *release)(Policy*);
    HRESULT (STDMETHODCALLTYPE *mix)(Policy*, LPCWSTR, WAVEFORMATEX**);
    HRESULT (STDMETHODCALLTYPE *format)(Policy*, LPCWSTR, BOOL, WAVEFORMATEX**);
    HRESULT (STDMETHODCALLTYPE *reset)(Policy*, LPCWSTR);
    HRESULT (STDMETHODCALLTYPE *setformat)(Policy*, LPCWSTR, WAVEFORMATEX*, WAVEFORMATEX*);
    HRESULT (STDMETHODCALLTYPE *period)(Policy*, LPCWSTR, BOOL, INT64*, INT64*);
    HRESULT (STDMETHODCALLTYPE *setperiod)(Policy*, LPCWSTR, INT64*);
    HRESULT (STDMETHODCALLTYPE *share)(Policy*, LPCWSTR, void*);
    HRESULT (STDMETHODCALLTYPE *setshare)(Policy*, LPCWSTR, void*);
    HRESULT (STDMETHODCALLTYPE *getprop)(Policy*, LPCWSTR, BOOL, const PROPERTYKEY*, PROPVARIANT*);
    HRESULT (STDMETHODCALLTYPE *setprop)(Policy*, LPCWSTR, BOOL, const PROPERTYKEY*, PROPVARIANT*);
    HRESULT (STDMETHODCALLTYPE *setdefault)(Policy*, LPCWSTR, ERole);
    HRESULT (STDMETHODCALLTYPE *visible)(Policy*, LPCWSTR, BOOL);
} PolicyVtbl;
struct Policy { const PolicyVtbl *v; };
static const GUID policy_class = {0x870af99c,0x171d,0x4f9e,{0xaf,0x0d,0xe6,0x3d,0xf4,0x0c,0x2b,0xc9}};
static const GUID policy_iid = {0xf8679f50,0x850a,0x41cf,{0x9c,0x72,0x43,0x0f,0x29,0x02,0x90,0xc8}};

static HRESULT device_open(const wchar_t *id, IMMDevice **out) {
    IMMDeviceEnumerator *e = NULL;
    HRESULT hr = CoCreateInstance(&CLSID_MMDeviceEnumerator, NULL, CLSCTX_INPROC_SERVER,
                                  &IID_IMMDeviceEnumerator, (void**)&e);
    if (FAILED(hr)) return hr;
    hr = id ? IMMDeviceEnumerator_GetDevice(e, id, out)
            : IMMDeviceEnumerator_GetDefaultAudioEndpoint(e, eRender, eConsole, out);
    IMMDeviceEnumerator_Release(e);
    if (SUCCEEDED(hr)) {
        IMMEndpoint *ep = NULL;
        EDataFlow flow;
        DWORD state;
        hr = IMMDevice_QueryInterface(*out, &IID_IMMEndpoint, (void**)&ep);
        if (SUCCEEDED(hr)) {
            hr = IMMEndpoint_GetDataFlow(ep, &flow);
            IMMEndpoint_Release(ep);
            if (SUCCEEDED(hr) && flow != eRender) hr = E_INVALIDARG;
        }
        if (SUCCEEDED(hr)) hr = IMMDevice_GetState(*out, &state);
        if (SUCCEEDED(hr) && state != DEVICE_STATE_ACTIVE) hr = HRESULT_FROM_WIN32(ERROR_DEVICE_NOT_CONNECTED);
        if (FAILED(hr)) { IMMDevice_Release(*out); *out = NULL; }
    }
    return hr;
}
static HRESULT policy_open(Policy **out) {
    return CoCreateInstance(&policy_class, NULL, CLSCTX_INPROC_SERVER, &policy_iid, (void**)out);
}
static HRESULT read_mask(IPropertyStore *store, const PROPERTYKEY *key, uint32_t *present, uint32_t *value) {
    PROPVARIANT p; PropVariantInit(&p);
    HRESULT hr = IPropertyStore_GetValue(store, key, &p);
    if (SUCCEEDED(hr)) {
        if (p.vt == VT_EMPTY) *present = 0;
        else if (p.vt == VT_UI4) { *present = 1; *value = p.ulVal; }
        else hr = HRESULT_FROM_WIN32(ERROR_DATATYPE_MISMATCH);
    }
    PropVariantClear(&p);
    return hr;
}
static HRESULT copy_format(unsigned char *out, uint32_t *size, WAVEFORMATEX *f) {
    if (!f || sizeof(*f) + f->cbSize > AL_FORMAT_CAP) return E_INVALIDARG;
    *size = (uint32_t)sizeof(*f) + f->cbSize;
    memcpy(out, f, *size);
    return S_OK;
}
HRESULT al_capture(const wchar_t *id, AL_Snapshot *out) {
    IMMDevice *d = NULL; IPropertyStore *store = NULL; IAudioClient *client = NULL;
    Policy *p = NULL; wchar_t *actual = NULL; WAVEFORMATEX *f = NULL;
    HRESULT hr;
    if (!out) return E_POINTER;
    memset(out, 0, sizeof(*out)); out->version = AL_VERSION;
    hr = device_open(id, &d); if (FAILED(hr)) goto done;
    hr = IMMDevice_GetId(d, &actual); if (FAILED(hr)) goto done;
    if (wcslen(actual) >= 1024) { hr = E_INVALIDARG; goto done; }
    wcscpy(out->endpoint, actual);
    hr = IMMDevice_OpenPropertyStore(d, STGM_READ, &store); if (FAILED(hr)) goto done;
    hr = read_mask(store, &PKEY_AudioEndpoint_PhysicalSpeakers, &out->physical_present, &out->physical);
    if (FAILED(hr)) goto done;
    hr = read_mask(store, &PKEY_AudioEndpoint_FullRangeSpeakers, &out->fullrange_present, &out->fullrange);
    if (FAILED(hr)) goto done;
    hr = policy_open(&p); if (FAILED(hr)) goto done;
    hr = p->v->format(p, actual, FALSE, &f); if (FAILED(hr)) goto done;
    hr = copy_format(out->device, &out->device_size, f);
    CoTaskMemFree(f); f = NULL; if (FAILED(hr)) goto done;
    hr = IMMDevice_Activate(d, &IID_IAudioClient, CLSCTX_INPROC_SERVER, NULL, (void**)&client);
    if (FAILED(hr)) goto done;
    hr = IAudioClient_GetMixFormat(client, &f); if (FAILED(hr)) goto done;
    hr = copy_format(out->mix, &out->mix_size, f);
done:
    CoTaskMemFree(f); CoTaskMemFree(actual);
    if (client) IAudioClient_Release(client);
    if (p) p->v->release(p);
    if (store) IPropertyStore_Release(store);
    if (d) IMMDevice_Release(d);
    return hr;
}
static unsigned count_bits(uint32_t x) { unsigned n=0; while(x) { n += x&1; x >>= 1; } return n; }
static int format_valid(const unsigned char *bytes, uint32_t size) {
    const WAVEFORMATEX *f = (const WAVEFORMATEX*)bytes;
    if (size < sizeof(*f) || size > AL_FORMAT_CAP || sizeof(*f)+f->cbSize != size) return 0;
    if (!f->nChannels || f->nChannels > 32 || !f->nSamplesPerSec || !f->wBitsPerSample || f->wBitsPerSample%8) return 0;
    if (f->nBlockAlign != f->nChannels * (f->wBitsPerSample/8) ||
        f->nAvgBytesPerSec != (uint64_t)f->nSamplesPerSec * f->nBlockAlign) return 0;
    if (f->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
        const WAVEFORMATEXTENSIBLE *x = (const WAVEFORMATEXTENSIBLE*)f;
        if (size < sizeof(*x) || count_bits(x->dwChannelMask) != f->nChannels) return 0;
        if (!IsEqualGUID(&x->SubFormat,&KSDATAFORMAT_SUBTYPE_PCM) && !IsEqualGUID(&x->SubFormat,&KSDATAFORMAT_SUBTYPE_IEEE_FLOAT)) return 0;
        if (!x->Samples.wValidBitsPerSample || x->Samples.wValidBitsPerSample > f->wBitsPerSample) return 0;
    } else if (f->wFormatTag != WAVE_FORMAT_PCM && f->wFormatTag != WAVE_FORMAT_IEEE_FLOAT) return 0;
    return 1;
}
int al_valid(const AL_Snapshot *s) {
    return s && s->version == AL_VERSION && s->endpoint[0] && wmemchr(s->endpoint,0,1024) &&
        s->physical_present <= 1 && s->fullrange_present <= 1 &&
        format_valid(s->device,s->device_size) && format_valid(s->mix,s->mix_size);
}
static HRESULT relayout(unsigned char *bytes, uint32_t *size, uint32_t mask) {
    WAVEFORMATEX *f = (WAVEFORMATEX*)bytes;
    WAVEFORMATEXTENSIBLE x = {0};
    if (f->wFormatTag == WAVE_FORMAT_EXTENSIBLE) memcpy(&x, bytes, sizeof(x));
    else {
        x.Format = *f;
        x.SubFormat = f->wFormatTag == WAVE_FORMAT_PCM ? KSDATAFORMAT_SUBTYPE_PCM : KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
        x.Samples.wValidBitsPerSample = f->wBitsPerSample;
    }
    x.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    x.Format.cbSize = 22; x.Format.nChannels = (WORD)count_bits(mask);
    x.Format.nBlockAlign = x.Format.nChannels * (x.Format.wBitsPerSample/8);
    x.Format.nAvgBytesPerSec = x.Format.nSamplesPerSec * x.Format.nBlockAlign;
    x.dwChannelMask = mask;
    memset(bytes,0,AL_FORMAT_CAP); memcpy(bytes,&x,sizeof(x)); *size=sizeof(x);
    return S_OK;
}
HRESULT al_plan(const AL_Snapshot *before, uint32_t mask, AL_Snapshot *out) {
    if (!al_valid(before) || !out) return E_INVALIDARG;
    /* Standard layouts are explicit user requests, not a substitute for captured profiles.
       5.1 back and side are separate; never silently change between them. */
    if (mask != 0x3 && mask != 0x3f && mask != 0x60f && mask != 0x63f) return E_INVALIDARG;
    *out = *before;
    out->physical_present = 1; out->physical = mask;
    /* Preserve bass-management choices for existing speakers. New speakers are not
       automatically marked full-range. Missing FullRange remains missing. */
    out->fullrange &= mask;
    relayout(out->device,&out->device_size,mask);
    relayout(out->mix,&out->mix_size,mask);
    return S_OK;
}
HRESULT al_supported(const AL_Snapshot *s) {
    IMMDevice *d = NULL; IAudioClient *a = NULL;
    if (!al_valid(s)) return E_INVALIDARG;
    HRESULT hr = device_open(s->endpoint,&d); if (FAILED(hr)) return hr;
    hr = IMMDevice_Activate(d,&IID_IAudioClient,CLSCTX_INPROC_SERVER,NULL,(void**)&a);
    if (SUCCEEDED(hr)) {
        hr = IAudioClient_IsFormatSupported(a,AUDCLNT_SHAREMODE_EXCLUSIVE,(WAVEFORMATEX*)s->device,NULL);
        /* Shared-mode checks describe the current engine configuration, not the
           new system configuration. Disabled exclusive access is inconclusive. */
        if (hr == AUDCLNT_E_EXCLUSIVE_MODE_NOT_ALLOWED) hr = S_FALSE;
    }
    if (a) IAudioClient_Release(a);
    IMMDevice_Release(d);
    return hr;
}
int al_equal(const AL_Snapshot *a,const AL_Snapshot *b) {
    return al_valid(a) && al_valid(b) && !wcscmp(a->endpoint,b->endpoint) &&
        a->physical_present==b->physical_present && a->physical==b->physical &&
        a->fullrange_present==b->fullrange_present && a->fullrange==b->fullrange &&
        a->device_size==b->device_size && a->mix_size==b->mix_size &&
        !memcmp(a->device,b->device,a->device_size) && !memcmp(a->mix,b->mix,a->mix_size);
}
static HRESULT write_mask(Policy *p,const wchar_t *id,const PROPERTYKEY *key,uint32_t present,uint32_t value) {
    PROPVARIANT v; PropVariantInit(&v);
    if (present) { v.vt=VT_UI4; v.ulVal=value; }
    return p->v->setprop(p,id,FALSE,key,&v);
}
static HRESULT write_state(const AL_Snapshot *s) {
    Policy *p = NULL;
    HRESULT hr = policy_open(&p); if (FAILED(hr)) return hr;
    hr = write_mask(p,s->endpoint,&PKEY_AudioEndpoint_PhysicalSpeakers,s->physical_present,s->physical);
    if (SUCCEEDED(hr)) hr = write_mask(p,s->endpoint,&PKEY_AudioEndpoint_FullRangeSpeakers,s->fullrange_present,s->fullrange);
    if (SUCCEEDED(hr)) {
        AL_Snapshot copy = *s;
        hr = p->v->setformat(p,copy.endpoint,(WAVEFORMATEX*)copy.device,(WAVEFORMATEX*)copy.mix);
    }
    p->v->release(p); return hr;
}
static HRESULT verify(const AL_Snapshot *s) {
    HRESULT hr = E_FAIL; AL_Snapshot actual;
    for (int n=0;n<12;n++) {
        hr=al_capture(s->endpoint,&actual);
        if (SUCCEEDED(hr) && al_equal(s,&actual)) return S_OK;
        Sleep(100);
    }
    return FAILED(hr) ? hr : HRESULT_FROM_WIN32(ERROR_INVALID_DATA);
}
HRESULT al_apply(const AL_Snapshot *target,HRESULT *rollback_result) {
    AL_Snapshot before;
    if (rollback_result) *rollback_result=S_FALSE;
    if (!al_valid(target)) return E_INVALIDARG;
    HRESULT hr=al_capture(target->endpoint,&before); if (FAILED(hr)) return hr;
    if (al_equal(&before,target)) return S_FALSE;
    if (!al_valid(&before)) return E_INVALIDARG;
    hr=al_supported(target); if (FAILED(hr)) return hr;
    hr=write_state(target);
    if (SUCCEEDED(hr)) hr=verify(target);
    if (FAILED(hr)) {
        HRESULT rollback=write_state(&before);
        if (SUCCEEDED(rollback)) rollback=verify(&before);
        if (rollback_result) *rollback_result=rollback;
    }
    return hr;
}
static void print_format(const char *label,const unsigned char *data) {
    const WAVEFORMATEX *f=(const WAVEFORMATEX*)data;
    printf("%s: channels=%u bits=%u rate=%lu tag=0x%04x",label,f->nChannels,f->wBitsPerSample,(unsigned long)f->nSamplesPerSec,f->wFormatTag);
    if (f->wFormatTag==WAVE_FORMAT_EXTENSIBLE && f->cbSize>=22) {
        const WAVEFORMATEXTENSIBLE *x=(const WAVEFORMATEXTENSIBLE*)f;
        printf(" mask=0x%lx valid_bits=%u",(unsigned long)x->dwChannelMask,x->Samples.wValidBitsPerSample);
        int pcm=IsEqualGUID(&x->SubFormat,&KSDATAFORMAT_SUBTYPE_PCM) || IsEqualGUID(&x->SubFormat,&KSDATAFORMAT_SUBTYPE_IEEE_FLOAT);
        const char *layout=!pcm?"Encoded carrier (not PCM)":x->dwChannelMask==3?"Stereo":x->dwChannelMask==0x3f?"5.1 Back":x->dwChannelMask==0x60f?"5.1 Side":x->dwChannelMask==0x63f?"7.1":"Other";
        printf(" layout=%s",layout);
    }
    puts("");
}
void al_print(const AL_Snapshot *s) {
    printf("Endpoint: %ls\nPhysicalSpeakers: %s0x%x\nFullRangeSpeakers: %s0x%x\n",s->endpoint,
        s->physical_present?"":"absent / ",s->physical,s->fullrange_present?"":"absent / ",s->fullrange);
    print_format("Device",s->device); print_format("Mix",s->mix);
}
