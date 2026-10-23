/* Simulated drivers/endpoints; writes only the caller's isolated fixture folder. */
#define FSA_PROFILE_TEST
#include "final_spatial_audio.c"
#include <assert.h>
static AL_Snapshot fake_actual;
static HRESULT fake_capture(const wchar_t *endpoint,AL_Snapshot *out){
    assert(!endpoint);*out=fake_actual;return S_OK;
}
static HRESULT fake_support(const AL_Snapshot *s){
    assert(!wcscmp(s->endpoint,fake_actual.endpoint));
    const WAVEFORMATEXTENSIBLE *f=(const WAVEFORMATEXTENSIBLE*)s->device;
    return f->Format.wBitsPerSample==24 && f->Format.nSamplesPerSec==48000 && f->dwChannelMask!=0x3f?S_OK:AUDCLNT_E_UNSUPPORTED_FORMAT;
}
static unsigned spatial_reads;
static HRESULT STDMETHODCALLTYPE fake_spatial_get(SpatialPolicy *p,LPCWSTR endpoint,BOOL fx,WAVEFORMATEX **f,SpatialSettings **s,UINT32 *count,BYTE **data){
    (void)p;assert(!fx && !wcscmp(endpoint,fake_actual.endpoint));
    *f=NULL;*s=NULL;*count=2;*data=CoTaskMemAlloc(2*0x342);assert(*data);memset(*data,0,2*0x342);
    GUID g;assert(SUCCEEDED(CLSIDFromString(pipeline_guids[1],&g)));memcpy(*data+0x300,&g,16);
    DWORD supported=1;memcpy(*data+0x310,&supported,4);
    assert(SUCCEEDED(CLSIDFromString(pipeline_guids[3],&g)));memcpy(*data+0x342+0x300,&g,16);
    spatial_reads++;return S_OK;
}
static void read_store(ProfileStore *store){
    FILE *f=_wfopen(profile_path,L"rb");assert(f);
    assert(fread(store,1,sizeof(*store),f)==sizeof(*store));assert(fgetc(f)==EOF);fclose(f);
}
int wmain(int argc,wchar_t **argv){
    assert(argc==2);wcscpy(root,argv[1]);swprintf(logpath,MAX_PATH,L"%ls\\test.log",root);
    AL_Snapshot identity={0},actual,profile,candidate;
    wcscpy(identity.endpoint,L"test-endpoint");
    default_profile(&identity,3,&actual);
    WAVEFORMATEXTENSIBLE *f=(WAVEFORMATEXTENSIBLE*)actual.device;
    f->Format.wBitsPerSample=24;f->Format.nBlockAlign=6;f->Format.nAvgBytesPerSec=288000;
    default_profile(&actual,3,&profile);
    assert(native_default_pcm(&profile,&actual,&candidate));
    assert(((WAVEFORMATEX*)candidate.device)->wBitsPerSample==24);
    assert(candidate.physical==profile.physical && candidate.fullrange==profile.fullrange);
    assert(al_valid(&candidate));
    default_profile(&actual,0x63f,&profile);
    assert(native_default_pcm(&profile,&actual,&candidate));
    assert(((WAVEFORMATEX*)candidate.device)->nChannels==8);
    assert(((WAVEFORMATEX*)candidate.device)->nBlockAlign==24);
    profile.fullrange=0;assert(!native_default_pcm(&profile,&actual,&candidate));
    default_profile(&actual,3,&profile);
    ((WAVEFORMATEX*)profile.device)->nSamplesPerSec=96000;
    ((WAVEFORMATEX*)profile.device)->nAvgBytesPerSec=768000;
    assert(!native_default_pcm(&profile,&actual,&candidate));
    default_profile(&actual,3,&profile);wcscpy(actual.endpoint,L"other-endpoint");
    assert(!native_default_pcm(&profile,&actual,&candidate));
    wcscpy(actual.endpoint,L"test-endpoint-a");fake_actual=actual;
    pcm_support=fake_support;pcm_capture=fake_capture;
    int first=0;assert(initialize_profiles(&first) && first);
    assert(profiles[1].physical==0x60f && pcm_profile_capabilities()==7);
    AL_Snapshot saved_a[3];memcpy(saved_a,profiles,sizeof(saved_a));
    wchar_t path_a[MAX_PATH];wcscpy(path_a,profile_path);
    ProfileStore before,after;read_store(&before);
    SpatialPolicyVtable table={0};table.get=fake_spatial_get;SpatialPolicy policy={&table};
    unsigned spatial=pipeline_query_capabilities(&policy,fake_actual.endpoint);
    assert((spatial&(1u<<MODE_DOLBY_HP)) && !(spatial&(1u<<MODE_ATMOS)) && !(spatial&7));
    check_capabilities(&policy);check_capabilities(&policy);read_store(&after);
    assert(spatial_reads==3 && (available_modes&7)==7);
    assert(!memcmp(&before,&after,sizeof(before)) && !memcmp(saved_a,profiles,sizeof(saved_a)));
    wcscpy(fake_actual.endpoint,L"test-endpoint-b");
    assert(initialize_profiles(&first) && first && wcscmp(path_a,profile_path));
    /* Unsupported explicit 96-kHz snapshot must survive reload unchanged. */
    WAVEFORMATEX *custom=(WAVEFORMATEX*)profiles[0].device;
    custom->nSamplesPerSec=96000;custom->nAvgBytesPerSec=custom->nBlockAlign*96000;
    assert(save_profiles());AL_Snapshot saved_b=profiles[0];
    wcscpy(fake_actual.endpoint,L"test-endpoint-a");assert(initialize_profiles(&first) && !first);
    assert(!wcscmp(path_a,profile_path) && !memcmp(saved_a,profiles,sizeof(saved_a)));
    wcscpy(fake_actual.endpoint,L"test-endpoint-b");assert(initialize_profiles(&first) && !first);
    assert(!memcmp(&saved_b,&profiles[0],sizeof(saved_b)));
    assert(fsa_edid_transport_allowed(9,L"HDAUDIO"));
    assert(!fsa_edid_transport_allowed(9,L"USB") && !fsa_edid_transport_allowed(9,L"ROOT"));
    assert(!fsa_edid_transport_allowed(1,L"HDAUDIO"));
    assert(fsa_edid_state(1u<<MODE_ATMOS,1,0)==(FSA_EDID_ELIGIBLE|FSA_EDID_SUPPORTED));
    assert(fsa_edid_state(1u<<MODE_DTS,0,0)==FSA_EDID_SUPPORTED);
    assert(fsa_edid_state(1u<<MODE_DOLBY_HP,1,0)==FSA_EDID_ELIGIBLE);
    edid_menu_state=FSA_EDID_ELIGIBLE|FSA_EDID_SUPPORTED;
    pipeline_menu_action(414);assert(!edid_requested && !edid_busy && !edid_process);
    edid_menu_state=0;pipeline_menu_action(414);assert(!edid_requested && !edid_busy);
    wcscpy(edid_state_endpoint,profiles[0].endpoint);
    edid_menu_state=FSA_EDID_ELIGIBLE;pipeline_menu_action(414);
    assert(edid_requested==1 && edid_busy==1 && !wcscmp(edid_request_endpoint,profiles[0].endpoint));
    pipeline_menu_action(414);assert(edid_requested==1 && !edid_process);
    exit_requested=1;assert(!exit_ready());exit_requested=0;
    edid_requested=edid_busy=0;
    /* Repair a known legacy built-in default only during profile loading. */
    default_profile(&fake_actual,0x3f,&profiles[1]);assert(save_profiles());
    assert(initialize_profiles(&first) && !first && profiles[1].physical==0x60f);
    wchar_t backup[MAX_PATH];swprintf(backup,MAX_PATH,L"%ls.before-native-pcm.bak",profile_path);
    assert(GetFileAttributesW(backup)!=INVALID_FILE_ATTRIBUTES);
    assert(!memcmp(&saved_b,&profiles[0],sizeof(saved_b)));
    puts("PASS native PCM default adaptation, channel layout, custom format and endpoint isolation");
    puts("PASS A/B/A snapshot binding, read-only queries, explicit snapshot preservation and backed-up legacy migration");
    puts("PASS local EDID transport filtering, HT checkmark, checked no-op and single manual request");
    return 0;
}
