/* Integration test: reads hardware, writes only its isolated fixture directory.
   Never invokes switch_mode/al_apply/spatial set. */
#include "final_spatial_audio.c"
#undef NDEBUG
#include <assert.h>
int wmain(int argc,wchar_t **argv) {
    if(argc!=2)return 2;
    wcscpy(root,argv[1]);swprintf(logpath,MAX_PATH,L"%ls\\test.log",root);
    assert(SUCCEEDED(CoInitializeEx(NULL,COINIT_MULTITHREADED)));
    int first=0;assert(initialize_profiles(&first));assert(first);
    for(int i=0;i<3;i++) {
        assert(al_valid(&profiles[i]));
        WAVEFORMATEXTENSIBLE *d=(WAVEFORMATEXTENSIBLE*)profiles[i].device;
        assert(d->Format.nSamplesPerSec==48000 && d->Samples.wValidBitsPerSample==24);
        assert(profiles[i].fullrange==(profiles[i].physical&~8u));
        printf("PCM %d 24-in-32 support: 0x%08lx\n",i,(unsigned long)al_supported(&profiles[i]));
        AL_Snapshot packed=profiles[i];WAVEFORMATEXTENSIBLE *pf=(WAVEFORMATEXTENSIBLE*)packed.device;
        pf->Format.wBitsPerSample=24;pf->Format.nBlockAlign=pf->Format.nChannels*3;pf->Format.nAvgBytesPerSec=pf->Format.nSamplesPerSec*pf->Format.nBlockAlign;
        printf("PCM %d packed-24 support: 0x%08lx\n",i,(unsigned long)al_supported(&packed));
    }
    AL_Snapshot expected[3];memcpy(expected,profiles,sizeof(expected));
    assert(initialize_profiles(&first) && !first);assert(!memcmp(expected,profiles,sizeof(expected)));
    AL_Snapshot a=profiles[0];SpatialSettings settings={0};assert(classify(&a,&settings)==MODE_STEREO);
    AL_Snapshot changed=a;((WAVEFORMATEX*)changed.device)->nSamplesPerSec=96000;
    assert(!actual_equal(&a,&settings,&changed,&settings));
    SpatialPolicy *p=NULL;
    HRESULT hr=CoCreateInstance(&spatial_policy_class,NULL,CLSCTX_ALL,&spatial_policy_iid,(void**)&p);
    if(SUCCEEDED(hr)) {
        check_capabilities(p);
        printf("Read-only endpoint capabilities: 0x%lx; Sonic=%d; Sonic NSp=%d\n",(unsigned long)available_modes,!!(available_modes&(1<<MODE_SONIC)),!!(available_modes&(1<<MODE_NSP)));
        AL_Snapshot before,after;SpatialSettings sb,sa;int mb,ma;
        assert(SUCCEEDED(read_actual(p,&before,&sb,&mb)));
        if(mb>=0) {
            assert(SUCCEEDED(capture_intent(p)));assert(initialize_profiles(&first) && !first);
            if(mb<3)assert(al_equal(&before,&profiles[mb]));
            else {assert(spatial_captured&(1u<<(mb-3)));assert(!memcmp(&before,&spatial_profiles[mb-3],sizeof(before)));}
        }
        assert(SUCCEEDED(read_actual(p,&after,&sa,&ma)));assert(actual_equal(&before,&sb,&after,&sa));p->v->release(p);
    }
    /* Recreate an old two-spatial-slot file and verify lossless v1 migration. */
    struct {uint32_t magic,version,captured;AL_Snapshot pcm[3],spatial[2];SpatialSettings settings[2];} legacy={0};
    legacy.magic=0x46534150;legacy.version=1;legacy.captured=spatial_captured&3;
    memcpy(legacy.pcm,profiles,sizeof(profiles));memcpy(legacy.spatial,spatial_profiles,sizeof(legacy.spatial));memcpy(legacy.settings,spatial_settings,sizeof(legacy.settings));
    wchar_t old_path[MAX_PATH];wcscpy(old_path,profile_path);wchar_t *suffix=wcsstr(old_path,L"-nsp-v2.bin");assert(suffix);wcscpy(suffix,L".bin");
    FILE *f=_wfopen(old_path,L"wb");assert(f);assert(fwrite(&legacy,1,sizeof(legacy),f)==sizeof(legacy));fclose(f);
    assert(DeleteFileW(profile_path));assert(initialize_profiles(&first));assert(!memcmp(legacy.pcm,profiles,sizeof(profiles)));
    assert(spatial_captured==legacy.captured);
    f=_wfopen(old_path,L"rb");assert(f);assert(fgetc(f)==0x50);fclose(f);
    f=_wfopen(profile_path,L"wb");assert(f);fputs("invalid snapshot",f);fclose(f);
    assert(!initialize_profiles(&first));
    f=_wfopen(profile_path,L"rb");assert(f);assert(fgetc(f)=='i');fclose(f);
    CoUninitialize();puts("PASS first-run 24/48 defaults, profile reload, actual-state capture, no audio writes, corrupt-file preservation");return 0;
}
