#ifndef FSA_NSP_ROUTE_LOCK_H
#define FSA_NSP_ROUTE_LOCK_H
/* Only spatial requests are rewritten; PCM remains a distinct rule target. */
static int route_lock_target(int frontend,int target){
    return frontend>=0 && frontend<7 && target>=MODE_ATMOS && target<MODE_COUNT?MODE_NSP:target;
}
static unsigned route_lock_hooks(int frontend){return frontend==1 || frontend==4?2u:frontend==2 || frontend==5?4u:0u;}
static int route_lock_bound(void){return InterlockedCompareExchange(&route_lock_index,0,0)>=0 && !_wcsicmp(route_lock_endpoint,profiles[0].endpoint);}
static int route_lock_reassert(int frontend,unsigned hooks){
    if(frontend<0 || frontend>=7)return 0;
    if(hooks&~route_lock_hooks(frontend))return 0;
    int ok=WritePrivateProfileStringW(L"NSP",L"FrontendFormat",pipeline_names[frontend],ini);
    if(hooks)ok=WritePrivateProfileStringW(L"NSP",L"PersistEnumerationHook",L"1",ini) && ok;
    if(hooks&2)ok=WritePrivateProfileStringW(L"NSP",L"HookHeadphones",L"1",ini) && ok;
    if(hooks&4)ok=WritePrivateProfileStringW(L"NSP",L"HookSpeakers",L"1",ini) && ok;
    return ok;
}
static void route_lock_load(void){
    InterlockedExchange(&route_lock_index,-1);
    if(!GetPrivateProfileIntW(L"NSP",L"NSpLock",0,ini))return;
    wchar_t name[80];GetPrivateProfileStringW(L"NSP",L"LockedFrontend",L"",name,80,ini);
    GetPrivateProfileStringW(L"NSP",L"LockEndpoint",L"",route_lock_endpoint,512,ini);
    for(int i=0;i<7;i++)if(!wcscmp(name,pipeline_names[i]) && route_lock_endpoint[0]){
        unsigned hooks=(unsigned)GetPrivateProfileIntW(L"NSP",L"LockHookMask",route_lock_hooks(i),ini);
        if(route_lock_reassert(i,hooks)){InterlockedExchange(&route_lock_hook_mask,(LONG)hooks);InterlockedExchange(&route_lock_index,i);}return;
    }
    log_line(L"NSp Lock snapshot invalid",E_INVALIDARG);
}
static HRESULT route_lock_commit(int frontend,const wchar_t *endpoint,unsigned hooks){
    if(frontend<0 || frontend>=7 || !endpoint[0] || wcslen(endpoint)>=512)return E_INVALIDARG;
    if(hooks&~route_lock_hooks(frontend))return E_INVALIDARG;
    wchar_t mask[16];swprintf(mask,16,L"%u",hooks);
    wchar_t previous_front[80],previous[3][16];
    const wchar_t *keys[]={L"PersistEnumerationHook",L"HookHeadphones",L"HookSpeakers"};
    GetPrivateProfileStringW(L"NSP",L"FrontendFormat",L"WindowsSonic",previous_front,80,ini);
    for(int i=0;i<3;i++)GetPrivateProfileStringW(L"NSP",keys[i],L"0",previous[i],16,ini);
    int ok=WritePrivateProfileStringW(L"NSP",L"LockedFrontend",pipeline_names[frontend],ini);
    ok=WritePrivateProfileStringW(L"NSP",L"LockEndpoint",endpoint,ini) && ok;
    ok=WritePrivateProfileStringW(L"NSP",L"LockFormatGuid",pipeline_guids[frontend],ini) && ok;
    ok=WritePrivateProfileStringW(L"NSP",L"LockHookMask",mask,ini) && ok;
    ok=WritePrivateProfileStringW(L"NSP",L"FrontendFormat",pipeline_names[frontend],ini) && ok;
    /* Enable missing requirements, preserving other user-selected groups. */
    if(hooks)ok=WritePrivateProfileStringW(L"NSP",keys[0],L"1",ini) && ok;
    if(hooks&2)ok=WritePrivateProfileStringW(L"NSP",keys[1],L"1",ini) && ok;
    if(hooks&4)ok=WritePrivateProfileStringW(L"NSP",keys[2],L"1",ini) && ok;
    if(ok)ok=WritePrivateProfileStringW(L"NSP",L"NSpLock",L"1",ini);
    if(!ok){
        WritePrivateProfileStringW(L"NSP",L"FrontendFormat",previous_front,ini);
        for(int i=0;i<3;i++)WritePrivateProfileStringW(L"NSP",keys[i],previous[i],ini);
        return E_FAIL;
    }
    wcscpy(route_lock_endpoint,endpoint);InterlockedExchange(&route_lock_hook_mask,(LONG)hooks);InterlockedExchange(&route_lock_index,frontend);
    if(hooks){
        int hp=GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini)!=0,sp=GetPrivateProfileIntW(L"NSP",L"HookSpeakers",0,ini)!=0;
        int same=hp==pipeline_live_flags(endpoint,1,2) && sp==pipeline_live_flags(endpoint,2,2) && pipeline_live_flags(endpoint,frontend,2);
        InterlockedExchange(&pipeline_requested,same?1:3);
    }
    return S_OK;
}
/* Worker reads the live service state; UI never guesses from its cached tick. */
static HRESULT route_lock_change(SpatialPolicy *p,LONG action){
    if(action==2){
        if(!WritePrivateProfileStringW(L"NSP",L"NSpLock",L"0",ini))return E_FAIL;
        InterlockedExchange(&route_lock_index,-1);InterlockedExchange(&route_lock_hook_mask,0);return S_OK;
    }
    AL_Snapshot actual,preferred;SpatialSettings settings;int mode=-1;
    HRESULT hr=read_actual(p,&actual,&settings,&mode);
    if(SUCCEEDED(hr))hr=al_capture(NULL,&preferred);
    if(FAILED(hr))return hr;
    int frontend=pipeline_format_for_mode(mode);
    if(frontend<0 || !settings.words[0] || wcscmp(preferred.endpoint,actual.endpoint))return HRESULT_FROM_WIN32(ERROR_NOT_SUPPORTED);
    unsigned hooks=route_lock_hooks(frontend);
    int requested=hooks==2?GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini):hooks==4?GetPrivateProfileIntW(L"NSP",L"HookSpeakers",0,ini):0;
    if(hooks && !requested && !pipeline_live(actual.endpoint,frontend)){
        WAVEFORMATEX *f=NULL;SpatialSettings *s=NULL;BYTE *d=NULL;UINT32 count=0;GUID expected;
        if(SUCCEEDED(CLSIDFromString(pipeline_guids[frontend],&expected)) && SUCCEEDED(p->v->get(p,actual.endpoint,FALSE,&f,&s,&count,&d)) && d && count<=128)
            for(UINT32 i=0;i<count;i++)if(d[i*0x342+0x310] && !memcmp(d+i*0x342+0x300,&expected,16))hooks=0;
        CoTaskMemFree(f);CoTaskMemFree(s);CoTaskMemFree(d);
    }
    return route_lock_commit(frontend,actual.endpoint,hooks);
}
#endif
