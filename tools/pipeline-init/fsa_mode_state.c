/* Isolated integration fixture: preserve the real endpoint around mode tests. */
#include "../fsa-integration/native/final_spatial_audio.c"
typedef struct {DWORD magic;AL_Snapshot audio;SpatialSettings spatial;} SavedMode;
int wmain(int argc,wchar_t **argv){
    if(argc!=3)return 2;
    HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);if(FAILED(hr))return 1;
    SpatialPolicy *p=NULL;hr=CoCreateInstance(&spatial_policy_class,NULL,CLSCTX_ALL,&spatial_policy_iid,(void**)&p);
    SavedMode saved={0};saved.magic=0x4d415346;
    if(SUCCEEDED(hr) && !wcscmp(argv[1],L"snapshot")){
        hr=al_capture(NULL,&saved.audio);
        if(SUCCEEDED(hr)){profiles[0]=saved.audio;hr=read_spatial(p,&saved.spatial);}
        FILE *f=SUCCEEDED(hr)?_wfopen(argv[2],L"wb"):NULL;
        if(f){if(fwrite(&saved,1,sizeof(saved),f)!=sizeof(saved))hr=E_FAIL;fclose(f);}else hr=E_FAIL;
    }else if(SUCCEEDED(hr) && !wcscmp(argv[1],L"restore")){
        FILE *f=_wfopen(argv[2],L"rb");
        if(!f)hr=E_FAIL;
        else{if(fread(&saved,1,sizeof(saved),f)!=sizeof(saved) || saved.magic!=0x4d415346)hr=E_INVALIDARG;fclose(f);}
        AL_Snapshot active={0};if(SUCCEEDED(hr))hr=al_capture(NULL,&active);
        if(SUCCEEDED(hr) && wcscmp(active.endpoint,saved.audio.endpoint))hr=E_INVALIDARG;
        if(SUCCEEDED(hr))hr=p->v->set(p,saved.audio.endpoint,&saved.spatial,NULL);
        if(SUCCEEDED(hr) && !saved.spatial.words[0])hr=al_apply(&saved.audio,NULL);
        if(SUCCEEDED(hr)){
            profiles[0]=saved.audio;SpatialSettings actual={0};hr=read_spatial(p,&actual);
            if(SUCCEEDED(hr) && (actual.words[0]!=saved.spatial.words[0] || memcmp(&actual.words[3],&saved.spatial.words[3],16)))hr=E_FAIL;
        }
    }else if(SUCCEEDED(hr))hr=E_INVALIDARG;
    printf("{\"hr\":\"0x%08lx\",\"mode\":%d}\n",(unsigned long)hr,classify(&saved.audio,&saved.spatial));
    if(p)p->v->release(p);CoUninitialize();return FAILED(hr)?1:0;
}
