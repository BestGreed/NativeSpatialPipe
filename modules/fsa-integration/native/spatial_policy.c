/* Experimental ABI adapter verified against this machine's AudioHandlers/AudioSes.
   Uses COM/RPC, never property replay or remote process injection.
   Keep isolated: this interface is not a supported public Windows SDK contract. */
#include "audio_layout.c"
#include <stddef.h>
#include <spatialaudioclient.h>

static const GUID spatial_policy_iid={0xe8478600,0xa74b,0x4b3a,{0xa9,0x6b,0x1f,0xc3,0xe7,0x96,0xfc,0x46}};
static const GUID spatial_policy_class={0x870af99c,0x171d,0x4f9e,{0xaf,0x0d,0xe6,0x3d,0xf4,0x0c,0x2b,0xc9}};
typedef struct { uint32_t words[18]; } SpatialSettings;
typedef struct SpatialPolicy SpatialPolicy;
typedef struct {
    HRESULT (STDMETHODCALLTYPE *query)(SpatialPolicy*,REFIID,void**);
    ULONG (STDMETHODCALLTYPE *addref)(SpatialPolicy*);
    ULONG (STDMETHODCALLTYPE *release)(SpatialPolicy*);
    void *reserved[31];
    HRESULT (STDMETHODCALLTYPE *get)(SpatialPolicy*,LPCWSTR,BOOL,WAVEFORMATEX**,SpatialSettings**,UINT32*,BYTE**);
    HRESULT (STDMETHODCALLTYPE *set)(SpatialPolicy*,LPCWSTR,const SpatialSettings*,const WAVEFORMATEX*);
} SpatialPolicyVtable;
struct SpatialPolicy { const SpatialPolicyVtable *v; };
_Static_assert(sizeof(SpatialSettings)==72,"SpatialSettings ABI");
_Static_assert(offsetof(SpatialPolicyVtable,get)==0x110,"get ABI");
_Static_assert(offsetof(SpatialPolicyVtable,set)==0x118,"set ABI");

#ifndef SPATIAL_POLICY_NO_MAIN
int wmain(int argc,wchar_t **argv) {
    int disable=argc==2 && !wcscmp(argv[1],L"off");
    int dtsx=argc==2 && (!wcscmp(argv[1],L"roundtrip-dtsx") || !wcscmp(argv[1],L"dtsx-ht"));
    int roundtrip=argc==2 && (!wcscmp(argv[1],L"roundtrip-dtsx") || !wcscmp(argv[1],L"roundtrip"));
    int activate=dtsx || roundtrip || (argc==2 && !wcscmp(argv[1],L"atmos-ht"));
    if(argc!=2 || (!disable && !activate && wcscmp(argv[1],L"read"))) { fwprintf(stderr,L"Usage: spatial-policy read|off|atmos-ht|roundtrip|roundtrip-dtsx\n"); return 2; }
    HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);
    if(FAILED(hr)) return 1;
    AL_Snapshot audio; SpatialPolicy *p=NULL;
    WAVEFORMATEX *format=NULL; SpatialSettings *settings=NULL; BYTE *descriptors=NULL; UINT32 count=0;
    hr=al_capture(NULL,&audio);
    if(SUCCEEDED(hr)) hr=CoCreateInstance(&spatial_policy_class,NULL,CLSCTX_ALL,&spatial_policy_iid,(void**)&p);
    if(SUCCEEDED(hr)) hr=p->v->get(p,audio.endpoint,FALSE,&format,&settings,&count,&descriptors);
    printf("GetDeviceFormatAndSpatialSettings HRESULT=0x%08lx\n",(unsigned long)hr);
    if(SUCCEEDED(hr) && settings && format && count<=128 && (!count || descriptors)) {
        printf("Endpoint: %ls\nFormat: %u channels, %lu Hz\n",audio.endpoint,format->nChannels,(unsigned long)format->nSamplesPerSec);
        for(unsigned i=0;i<18;i++) printf("Settings[%02x]=%08x\n",i*4,settings->words[i]);
        for(UINT32 i=0;i<count;i++) {
            BYTE *d=descriptors+i*0x342; GUID id; wchar_t guid[40],title[257];
            memcpy(&id,d+0x300,16); StringFromGUID2(&id,guid,40);
            memcpy(title,d,512); title[256]=0;
            uint32_t flags[4]; memcpy(flags,d+0x310,16);
            printf("Encoder %u: %ls flags=%u,%u,%u,%u\n",i,guid,flags[0],flags[1],flags[2],flags[3]);
        }
        if(disable) {
            SpatialSettings off=*settings;
            off.words[0]=0;
            memset(&off.words[3],0,16);
            off.words[17]=0;
            hr=p->v->set(p,audio.endpoint,&off,NULL);
            printf("Spatial Off HRESULT=0x%08lx\n",(unsigned long)hr);
        }
        if(activate) {
            const GUID atmos={0xa289735d,0xfa3e,0x4e35,{0x9d,0x7d,0xb6,0xf8,0x96,0xac,0xb2,0xe7}};
            const GUID dts={0x10201b4a,0x3322,0x4967,{0xbf,0x40,0x2c,0xaa,0x9b,0xaf,0xca,0x44}};
            const GUID *encoder=dtsx?&dts:&atmos;
            BYTE *selected=NULL;
            for(UINT32 i=0;i<count;i++) if(!memcmp(descriptors+i*0x342+0x300,encoder,16)) selected=descriptors+i*0x342;
            if(!selected) hr=HRESULT_FROM_WIN32(ERROR_NOT_SUPPORTED);
            else {
                SpatialSettings target=*settings;
                /* Same field updates as AudioHandlers::SetSpatialByDescriptor.
                   Preserve unknown/reserved fields obtained from the service. */
                target.words[0]=1;
                memcpy(&target.words[1],selected+0x314,4);
                target.words[2]=1;
                memcpy(&target.words[3],selected+0x300,16);
                memcpy(&target.words[7],selected+0x300,16);
                target.words[15]=1;
                uint32_t flag; memcpy(&flag,selected+0x318,4);
                target.words[17]=flag!=0;
                HRESULT off_result=S_OK;
                if(roundtrip) {
                    if(settings->words[0]!=1 || memcmp(&settings->words[3],encoder,16)) {
                        fprintf(stderr,"Roundtrip requires the requested HT encoder to be active.\n");
                        hr=E_INVALIDARG;
                        goto cleanup;
                    }
                    SpatialSettings off=*settings;
                    /* AudioHandlers::SetProperty Off branch: preserve other fields. */
                    off.words[0]=0;
                    memset(&off.words[3],0,16);
                    off.words[17]=0;
                    off_result=p->v->set(p,audio.endpoint,&off,NULL);
                    printf("Spatial Off HRESULT=0x%08lx\n",(unsigned long)off_result);
                    if(SUCCEEDED(off_result)) {
                        Sleep(1500);
                        WAVEFORMATEX *f=NULL; SpatialSettings *s=NULL;
                        UINT32 n=0; BYTE *d=NULL;
                        off_result=p->v->get(p,audio.endpoint,FALSE,&f,&s,&n,&d);
                        if(SUCCEEDED(off_result) && f && s) {
                            printf("Off readback: enabled=%u channels=%u rate=%lu tag=0x%x\n",s->words[0],f->nChannels,(unsigned long)f->nSamplesPerSec,f->wFormatTag);
                            if(s->words[0]!=0 || f->nChannels!=2) off_result=E_UNEXPECTED;
                            AL_Snapshot pcm;
                            HRESULT captured=al_capture(audio.endpoint,&pcm);
                            if(SUCCEEDED(captured)) { al_print(&pcm); if(!al_valid(&pcm)) off_result=E_UNEXPECTED; }
                            else off_result=captured;
                        } else if(SUCCEEDED(off_result)) off_result=E_UNEXPECTED;
                        CoTaskMemFree(f); CoTaskMemFree(s); CoTaskMemFree(d);
                        fflush(stdout);
                        if(SUCCEEDED(off_result)) Sleep(5000);
                    }
                    /* Always attempt to re-enable the original encoder on the same endpoint,
                       including after an Off/readback failure. No raw replay. */
                }
                hr=p->v->set(p,audio.endpoint,&target,NULL);
                printf("SetDeviceSpatialSettings %s HRESULT=0x%08lx\n",dtsx?"DTS:X HT":"Atmos HT",(unsigned long)hr);
                if(FAILED(off_result)) { printf("Off stage failed: 0x%08lx\n",(unsigned long)off_result); if(SUCCEEDED(hr)) hr=off_result; }
            }
        }
    } else if(SUCCEEDED(hr)) hr=E_UNEXPECTED;
cleanup:
    CoTaskMemFree(format); CoTaskMemFree(settings); CoTaskMemFree(descriptors);
    if(p) p->v->release(p);
    CoUninitialize(); return FAILED(hr)?1:0;
}
#endif
