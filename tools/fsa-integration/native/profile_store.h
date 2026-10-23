/* Endpoint-bound, versioned snapshots. Included only by the worker implementation. */
#ifdef FSA_PROFILE_TEST
static HRESULT (*pcm_support)(const AL_Snapshot*)=al_supported;
static HRESULT (*pcm_capture)(const wchar_t*,AL_Snapshot*)=al_capture;
#else
#define pcm_support al_supported
#define pcm_capture al_capture
#endif
typedef struct {
    uint32_t magic,version,captured;
    AL_Snapshot pcm[3],spatial[3];
    SpatialSettings settings[3];
} ProfileStore;
static int save_profiles(void) {
    ProfileStore data={0};data.magic=0x46534150;data.version=2;data.captured=spatial_captured;
    memcpy(data.pcm,profiles,sizeof(profiles));memcpy(data.spatial,spatial_profiles,sizeof(spatial_profiles));memcpy(data.settings,spatial_settings,sizeof(spatial_settings));
    wchar_t temp[MAX_PATH];swprintf(temp,MAX_PATH,L"%ls.tmp",profile_path);
    HANDLE f=CreateFileW(temp,GENERIC_WRITE,0,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if(f==INVALID_HANDLE_VALUE)return 0;
    DWORD written=0;BOOL ok=WriteFile(f,&data,sizeof(data),&written,NULL) && written==sizeof(data) && FlushFileBuffers(f);CloseHandle(f);
    if(ok)ok=MoveFileExW(temp,profile_path,MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH);
    if(!ok)DeleteFileW(temp);
    return ok;
}
static void default_profile(const AL_Snapshot *actual,uint32_t mask,AL_Snapshot *out) {
    memset(out,0,sizeof(*out));out->version=AL_VERSION;wcscpy(out->endpoint,actual->endpoint);
    out->physical_present=out->fullrange_present=1;out->physical=mask;out->fullrange=mask&~8u;
    WAVEFORMATEXTENSIBLE f={0};f.Format.wFormatTag=WAVE_FORMAT_EXTENSIBLE;f.Format.cbSize=22;
    f.Format.nChannels=(WORD)count_bits(mask);f.Format.nSamplesPerSec=48000;f.Format.wBitsPerSample=32;
    f.Format.nBlockAlign=f.Format.nChannels*4;f.Format.nAvgBytesPerSec=48000*f.Format.nBlockAlign;
    f.dwChannelMask=mask;f.Samples.wValidBitsPerSample=24;f.SubFormat=KSDATAFORMAT_SUBTYPE_PCM;
    out->device_size=out->mix_size=sizeof(f);memcpy(out->device,&f,sizeof(f));
    f.Samples.wValidBitsPerSample=32;f.SubFormat=KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;memcpy(out->mix,&f,sizeof(f));
}
/* Legacy files do not store provenance. Only exact built-in defaults qualify;
   every differing format or speaker property remains an explicit snapshot. */
static int native_default_pcm(const AL_Snapshot *profile,const AL_Snapshot *actual,AL_Snapshot *out) {
    if(!al_valid(profile) || !al_valid(actual) || wcscmp(profile->endpoint,actual->endpoint))return 0;
    AL_Snapshot generated,native;
    default_profile(actual,profile->physical,&generated);
    if(!al_equal(profile,&generated) || FAILED(al_plan(actual,profile->physical,&native)))return 0;
    *out=*profile;
    out->device_size=native.device_size;out->mix_size=native.mix_size;
    memcpy(out->device,native.device,sizeof(out->device));memcpy(out->mix,native.mix,sizeof(out->mix));
    return 1;
}
/* FSA snapshot preparation only. Queries and restores never rewrite profiles. */
static int prepare_default_pcm_profiles(const AL_Snapshot *actual){
    int changed=0;
    for(int i=0;i<3;i++){
        AL_Snapshot native;
        if(pcm_support(&profiles[i])!=AUDCLNT_E_UNSUPPORTED_FORMAT || !native_default_pcm(&profiles[i],actual,&native))continue;
        if(pcm_support(&native)==S_OK){profiles[i]=native;changed=1;continue;}
        if(i==1){
            AL_Snapshot alternate;
            default_profile(actual,profiles[i].physical==0x3f?0x60f:0x3f,&alternate);
            if(pcm_support(&alternate)==S_OK){profiles[i]=alternate;changed=1;continue;}
            if(native_default_pcm(&alternate,actual,&native) && pcm_support(&native)==S_OK){profiles[i]=native;changed=1;}
        }
    }
    return changed;
}
static unsigned pcm_profile_capabilities(void){
    unsigned flags=0;
    /* S_FALSE (exclusive access disabled) is inconclusive, not unsupported. */
    for(int i=0;i<3;i++)if(SUCCEEDED(pcm_support(&profiles[i])))flags|=1u<<i;
    return flags;
}
/* FSA owns the active render endpoint binding, independently of spatial queries. */
static int default_pcm_endpoint_changed(void){
    IMMDevice *device=NULL;LPWSTR id=NULL;
    HRESULT hr=device_open(NULL,&device);
    if(SUCCEEDED(hr))hr=IMMDevice_GetId(device,&id);
    int changed=SUCCEEDED(hr)?wcscmp(id,profiles[0].endpoint)!=0:-1;
    CoTaskMemFree(id);if(device)IMMDevice_Release(device);
    return changed;
}
static int finish_loaded_profiles(const AL_Snapshot *actual){
    AL_Snapshot before[3];memcpy(before,profiles,sizeof(before));
    if(!prepare_default_pcm_profiles(actual))return 1;
    wchar_t backup[MAX_PATH];swprintf(backup,MAX_PATH,L"%ls.before-native-pcm.bak",profile_path);
    if(!CopyFileW(profile_path,backup,TRUE) && GetLastError()!=ERROR_FILE_EXISTS){memcpy(profiles,before,sizeof(before));return 0;}
    if(!save_profiles()){memcpy(profiles,before,sizeof(before));return 0;}
    log_line(L"FSA default PCM snapshot migration",S_OK);return 1;
}
static int initialize_profiles(int *first) {
    AL_Snapshot actual;HRESULT hr=pcm_capture(NULL,&actual);*first=0;
    if(FAILED(hr)){log_line(L"Default endpoint read failed",hr);return 0;}
    uint32_t hash=2166136261u;for(const wchar_t *c=actual.endpoint;*c;c++){hash^=*c;hash*=16777619u;}
    wchar_t directory[MAX_PATH];swprintf(directory,MAX_PATH,L"%ls\\profiles",root);
    if(!CreateDirectoryW(directory,NULL) && GetLastError()!=ERROR_ALREADY_EXISTS)return 0;
    swprintf(profile_path,MAX_PATH,L"%ls\\%08lx-nsp-v2.bin",directory,(unsigned long)hash);
    FILE *f=_wfopen(profile_path,L"rb");
    if(f) {
        ProfileStore data;size_t n=fread(&data,1,sizeof(data),f);int extra=fgetc(f);fclose(f);
        int valid=n==sizeof(data) && extra==EOF && data.magic==0x46534150 && data.version==2 && data.captured<=7;
        for(int i=0;valid && i<3;i++)valid=al_valid(&data.pcm[i]) && !wcscmp(data.pcm[i].endpoint,actual.endpoint);
        for(int i=0;valid && i<3;i++)if(data.captured&(1u<<i)) {
            AL_Snapshot *a=&data.spatial[i];
            valid=a->version==AL_VERSION && wmemchr(a->endpoint,0,1024) && !wcscmp(a->endpoint,actual.endpoint) && a->device_size<=AL_FORMAT_CAP && a->mix_size<=AL_FORMAT_CAP && a->fullrange_present<=1 && data.settings[i].words[0] && !memcmp(&data.settings[i].words[3],&encoders[i],16);
        }
        if(!valid){log_line(L"Profile file invalid; preserved without overwrite",E_INVALIDARG);return 0;}
        memcpy(profiles,data.pcm,sizeof(profiles));memcpy(spatial_profiles,data.spatial,sizeof(spatial_profiles));memcpy(spatial_settings,data.settings,sizeof(spatial_settings));spatial_captured=data.captured;
        return finish_loaded_profiles(&actual);
    }
    if(GetFileAttributesW(profile_path)!=INVALID_FILE_ATTRIBUTES){log_line(L"Profile file cannot be opened",E_ACCESSDENIED);return 0;}
    /* Read the original v1 file without modifying it. Extend with an empty Sonic slot. */
    wchar_t legacy_path[MAX_PATH];swprintf(legacy_path,MAX_PATH,L"%ls\\%08lx.bin",directory,(unsigned long)hash);
    f=_wfopen(legacy_path,L"rb");
    if(f) {
        struct {uint32_t magic,version,captured;AL_Snapshot pcm[3],spatial[2];SpatialSettings settings[2];} old;
        size_t n=fread(&old,1,sizeof(old),f);int extra=fgetc(f);fclose(f);
        int valid=n==sizeof(old) && extra==EOF && old.magic==0x46534150 && old.version==1 && old.captured<=3;
        for(int i=0;valid && i<3;i++)valid=al_valid(&old.pcm[i]) && !wcscmp(old.pcm[i].endpoint,actual.endpoint);
        for(int i=0;valid && i<2;i++)if(old.captured&(1u<<i)) {
            AL_Snapshot *a=&old.spatial[i];
            valid=a->version==AL_VERSION && wmemchr(a->endpoint,0,1024) && !wcscmp(a->endpoint,actual.endpoint) && a->device_size<=AL_FORMAT_CAP && a->mix_size<=AL_FORMAT_CAP && a->fullrange_present<=1 && old.settings[i].words[0] && !memcmp(&old.settings[i].words[3],&encoders[i],16);
        }
        if(!valid){log_line(L"Legacy profile invalid; preserved",E_INVALIDARG);return 0;}
        memcpy(profiles,old.pcm,sizeof(profiles));
        memset(spatial_profiles,0,sizeof(spatial_profiles));memset(spatial_settings,0,sizeof(spatial_settings));
        memcpy(spatial_profiles,old.spatial,sizeof(old.spatial));memcpy(spatial_settings,old.settings,sizeof(old.settings));
        spatial_captured=old.captured;prepare_default_pcm_profiles(&actual);return save_profiles();
    }
    if(GetFileAttributesW(legacy_path)!=INVALID_FILE_ATTRIBUTES)return 0;
    /* Migrate the existing installation only when all snapshots belong to this endpoint. */
    const wchar_t *files[]={L"stereo",L"5.1-back",L"7.1"};int migrated=1;
    for(int i=0;i<3;i++) {
        wchar_t path[MAX_PATH];swprintf(path,MAX_PATH,L"%ls\\profiles-24-192\\%ls.bin",root,files[i]);
        f=_wfopen(path,L"rb");if(!f){migrated=0;break;}
        size_t n=fread(&profiles[i],1,sizeof(AL_Snapshot),f);int extra=fgetc(f);fclose(f);
        if(n!=sizeof(AL_Snapshot) || extra!=EOF || !al_valid(&profiles[i]) || wcscmp(profiles[i].endpoint,actual.endpoint)){migrated=0;break;}
    }
    if(!migrated) {
        uint32_t mask=0x3f;
        if(al_valid(&actual)) {const WAVEFORMATEXTENSIBLE *x=(const WAVEFORMATEXTENSIBLE*)actual.device;if(x->Format.wFormatTag==WAVE_FORMAT_EXTENSIBLE && (x->dwChannelMask==0x3f || x->dwChannelMask==0x60f))mask=x->dwChannelMask;}
        default_profile(&actual,3,&profiles[0]);default_profile(&actual,mask,&profiles[1]);default_profile(&actual,0x63f,&profiles[2]);
        if(FAILED(pcm_support(&profiles[1]))) {
            AL_Snapshot alternate;default_profile(&actual,mask==0x3f?0x60f:0x3f,&alternate);
            if(pcm_support(&alternate)==S_OK)profiles[1]=alternate;
        }
    }
    spatial_captured=0;memset(spatial_profiles,0,sizeof(spatial_profiles));memset(spatial_settings,0,sizeof(spatial_settings));
    prepare_default_pcm_profiles(&actual);
    *first=1;log_line(migrated?L"Migrating existing endpoint snapshots":L"Initializing 24-bit 48-kHz PCM profiles",S_OK);
    return save_profiles();
}
