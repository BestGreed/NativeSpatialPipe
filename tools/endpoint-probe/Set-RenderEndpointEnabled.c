/* Explicit test helper. Reuse the repository's existing policy ABI and keep
 * all default render roles unchanged. No EDID writes or driver restart. */
#include "../fsa-integration/native/audio_layout.c"
int wmain(int argc,wchar_t **argv) {
    if(argc!=3 || (wcscmp(argv[2],L"0") && wcscmp(argv[2],L"1")))return 2;
    if(wcsncmp(argv[1],L"{0.0.0.00000000}.{",18))return 2;
    HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);if(FAILED(hr))return 1;
    IMMDeviceEnumerator *e=NULL;Policy *p=NULL;IMMDevice *target=NULL;
    LPWSTR defaults[3]={0};DWORD before=0,after=0;int stable=1;
    hr=CoCreateInstance(&CLSID_MMDeviceEnumerator,NULL,CLSCTX_INPROC_SERVER,&IID_IMMDeviceEnumerator,(void**)&e);
    if(FAILED(hr))goto done;
    for(int role=0;role<3;role++) {
        IMMDevice *d=NULL;hr=IMMDeviceEnumerator_GetDefaultAudioEndpoint(e,eRender,(ERole)role,&d);
        if(FAILED(hr))goto done;
        hr=IMMDevice_GetId(d,&defaults[role]);IMMDevice_Release(d);if(FAILED(hr))goto done;
    }
    hr=IMMDeviceEnumerator_GetDevice(e,argv[1],&target);if(FAILED(hr))goto done;
    hr=IMMDevice_GetState(target,&before);if(FAILED(hr))goto done;
    hr=CoCreateInstance(&policy_class,NULL,CLSCTX_ALL,&policy_iid,(void**)&p);if(FAILED(hr))goto done;
    hr=p->v->visible(p,argv[1],argv[2][0]==L'1');if(FAILED(hr))goto done;
    for(int i=0;i<50;i++) {
        IMMDevice_Release(target);target=NULL;
        hr=IMMDeviceEnumerator_GetDevice(e,argv[1],&target);if(FAILED(hr))break;
        hr=IMMDevice_GetState(target,&after);if(FAILED(hr))break;
        if(argv[2][0]==L'1' ? after==DEVICE_STATE_ACTIVE : after==DEVICE_STATE_DISABLED)break;
        Sleep(100);
    }
    if(SUCCEEDED(hr) && !(argv[2][0]==L'1' ? after==DEVICE_STATE_ACTIVE : after==DEVICE_STATE_DISABLED))hr=E_FAIL;
    for(int role=0;role<3;role++) {
        IMMDevice *d=NULL;LPWSTR id=NULL;
        HRESULT q=IMMDeviceEnumerator_GetDefaultAudioEndpoint(e,eRender,(ERole)role,&d);
        if(SUCCEEDED(q)){q=IMMDevice_GetId(d,&id);IMMDevice_Release(d);}
        if(FAILED(q) || !id || wcscmp(id,defaults[role])) {
            stable=0;if(FAILED(p->v->setdefault(p,defaults[role],(ERole)role)))hr=E_FAIL;
        }
        if(id)CoTaskMemFree(id);
    }
    if(!stable) {
        int verified=1;
        for(int role=0;role<3;role++) {
            IMMDevice *d=NULL;LPWSTR id=NULL;
            HRESULT q=IMMDeviceEnumerator_GetDefaultAudioEndpoint(e,eRender,(ERole)role,&d);
            if(SUCCEEDED(q)){q=IMMDevice_GetId(d,&id);IMMDevice_Release(d);}
            if(FAILED(q) || !id || wcscmp(id,defaults[role]))verified=0;
            if(id)CoTaskMemFree(id);
        }
        if(verified)stable=2;
    }
done:
    printf("{\"hr\":\"0x%08lX\",\"beforeState\":%lu,\"afterState\":%lu,\"defaultRolesUnchanged\":%s,\"defaultRolesRestored\":%s}\n",(unsigned long)hr,(unsigned long)before,(unsigned long)after,stable==1?"true":"false",stable==2?"true":"false");
    if(target)IMMDevice_Release(target);if(p)p->v->release(p);if(e)IMMDeviceEnumerator_Release(e);
    for(int i=0;i<3;i++)if(defaults[i])CoTaskMemFree(defaults[i]);CoUninitialize();
    return FAILED(hr) || !stable ? 1:0;
}
