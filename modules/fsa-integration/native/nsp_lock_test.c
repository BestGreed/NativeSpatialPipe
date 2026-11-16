#include "final_spatial_audio.c"
#include <assert.h>
int wmain(void){
    wchar_t directory[MAX_PATH],temporary[MAX_PATH];GetModuleFileNameW(NULL,directory,MAX_PATH);
    wchar_t *slash=wcsrchr(directory,L'\\');assert(slash);*slash=0;
    for(int front=0;front<7;front++){
        assert(GetTempFileNameW(directory,L"lck",0,temporary));wcscpy(ini,temporary);
        assert(WritePrivateProfileStringW(L"NSP",L"PersistEnumerationHook",L"0",ini));
        assert(WritePrivateProfileStringW(L"NSP",L"HookHeadphones",L"0",ini));
        assert(WritePrivateProfileStringW(L"NSP",L"HookSpeakers",L"0",ini));
        assert(SUCCEEDED(route_lock_commit(front,L"fixture-endpoint",route_lock_hooks(front))));
        unsigned hooks=route_lock_hooks(front);
        assert(GetPrivateProfileIntW(L"NSP",L"NSpLock",0,ini)==1);
        assert((unsigned)GetPrivateProfileIntW(L"NSP",L"LockHookMask",99,ini)==hooks);
        assert(GetPrivateProfileIntW(L"NSP",L"PersistEnumerationHook",0,ini)==(hooks!=0));
        assert(GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini)==((hooks&2)!=0));
        assert(GetPrivateProfileIntW(L"NSP",L"HookSpeakers",0,ini)==((hooks&4)!=0));
        assert(pipeline_index()==front);
        wchar_t guid[80];GetPrivateProfileStringW(L"NSP",L"LockFormatGuid",L"",guid,80,ini);assert(!wcscmp(guid,pipeline_guids[front]));
        InterlockedExchange(&route_lock_index,-1);assert(WritePrivateProfileStringW(L"NSP",L"FrontendFormat",L"WindowsSonic",ini));
        route_lock_load();assert(route_lock_index==front && pipeline_index()==front);
        wcscpy(profiles[0].endpoint,L"fixture-endpoint");assert(route_lock_bound());
        wcscpy(profiles[0].endpoint,L"other-endpoint");assert(!route_lock_bound());
        for(int target=-1;target<MODE_COUNT;target++)assert(route_lock_target(front,target)==(target>=MODE_ATMOS?MODE_NSP:target));
        Rule rs[]={{1,SCOPE_RUNNING,MODE_ATMOS,10},{1,SCOPE_RUNNING,MODE_DTS,20},{1,SCOPE_RUNNING,MODE_51,30}};
        unsigned char matches[]={1,1,0};assert(route_lock_target(front,resolve(rs,matches,3,MODE_SONIC))==MODE_NSP);
        matches[2]=1;assert(route_lock_target(front,resolve(rs,matches,3,MODE_SONIC))==MODE_51);
        assert(WritePrivateProfileStringW(L"Manager",L"Fallback",fsa_locked_nsp_label(front),ini));
        assert(WritePrivateProfileStringW(L"Rule:Locked",L"Enabled",L"1",ini));
        assert(WritePrivateProfileStringW(L"Rule:Locked",L"Scope",L"Running",ini));
        assert(WritePrivateProfileStringW(L"Rule:Locked",L"Exe",L"Fixture.exe",ini));
        assert(WritePrivateProfileStringW(L"Rule:Locked",L"Profile",fsa_locked_nsp_label(front),ini));
        load_rules();assert(fallback==MODE_NSP && rule_count==1 && rules[0].profile==MODE_NSP);
        assert(SUCCEEDED(route_lock_change(NULL,2)));assert(route_lock_index==-1);
        load_rules();assert(fallback==MODE_STEREO && rule_count==0);
        assert(GetPrivateProfileIntW(L"NSP",L"NSpLock",1,ini)==0);
        assert(GetPrivateProfileIntW(L"NSP",L"PersistEnumerationHook",0,ini)==(hooks!=0));
        assert(DeleteFileW(temporary));
    }
    assert(!wcscmp(fsa_locked_nsp_label(0),L"Sonic NSp"));
    assert(!wcscmp(fsa_locked_nsp_label(1),L"Dolby Atmos B NSp"));
    assert(!wcscmp(fsa_locked_nsp_label(2),L"Dolby Atmos L NSp"));
    assert(!wcscmp(fsa_locked_nsp_label(3),L"Dolby Atmos HT NSp"));
    assert(!wcscmp(fsa_locked_nsp_label(4),L"DTS:X B NSp"));
    assert(!wcscmp(fsa_locked_nsp_label(5),L"DTS:X L NSp"));
    assert(!wcscmp(fsa_locked_nsp_label(6),L"DTS:X HT NSp"));
    assert(FAILED(route_lock_commit(7,L"fixture",0)) && FAILED(route_lock_commit(5,L"",4)));
    assert(GetTempFileNameW(directory,L"nat",0,temporary));wcscpy(ini,temporary);
    assert(SUCCEEDED(route_lock_commit(1,L"native-headphones",0)));route_lock_load();
    assert(route_lock_index==1 && route_lock_hook_mask==0 && !GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini) && !GetPrivateProfileIntW(L"NSP",L"PersistEnumerationHook",0,ini));
    assert(FAILED(route_lock_commit(1,L"native-headphones",4)));
    assert(DeleteFileW(temporary));
    route_lock_index=-1;wake=CreateEventW(NULL,FALSE,FALSE,NULL);assert(wake);
    pipeline_menu_action(311);assert(route_lock_busy && route_lock_requested==1);
    pipeline_menu_action(311);assert(route_lock_requested==1);
    route_lock_busy=0;route_lock_requested=0;route_lock_index=5;
    pipeline_menu_action(311);assert(route_lock_requested==2);
    available_modes=1u<<MODE_NSP;nsp_requested=0;current=MODE_STEREO;automatic=1;
    request_manual_mode(MODE_ATMOS);assert(nsp_requested==1 && !automatic);
    nsp_requested=0;current=MODE_NSP;request_manual_mode(MODE_DTS);assert(nsp_requested==0);
    available_modes|=1u<<MODE_STEREO;nsp_ui_locked=1;request_manual_mode(MODE_STEREO);assert(nsp_requested==2 && nsp_next_mode==MODE_STEREO);
    CloseHandle(wake);ini[0]=0;
    puts("PASS seven lock snapshots, required hooks, endpoint binding, spatial rule remap, PCM preservation and live-session reuse");return 0;
}
