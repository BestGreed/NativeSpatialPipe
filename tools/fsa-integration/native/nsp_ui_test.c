#include "final_spatial_audio.c"
#include <assert.h>
int wmain(int argc,wchar_t **argv) {
    assert(!wcscmp(names[MODE_NSP],L"NSp"));
    assert(!wcscmp(fsa_text(FSA_EN,TXT_NSP),L"NSp"));
    assert(!wcscmp(fsa_text(FSA_ZH,TXT_NSP),L"NSp"));
    FSA_MenuState menu_state={0};menu_state.selected=MODE_SONIC;
    assert(fsa_menu_checked(&menu_state,105) && !fsa_menu_checked(&menu_state,106));
    assert(fsa_nsp_toggle_request(&menu_state)==1);
    menu_state.selected=MODE_NSP;
    assert(fsa_menu_checked(&menu_state,105) && fsa_menu_checked(&menu_state,106));
    menu_state.frontend=5;assert(fsa_menu_checked(&menu_state,110) && !fsa_menu_checked(&menu_state,105));
    assert(fsa_nsp_toggle_request(&menu_state)==2);
    menu_state.selected=-1;
    for(UINT command=100;command<=106;command++)assert(!fsa_menu_checked(&menu_state,command));
    puts("PASS independent Sonic endpoint and NSp feature checks, stop/start toggle, unknown state unselected");
    assert(mode_icon_ids[MODE_NSP]==IDI_FSA_APP);
    {
        wchar_t directory[MAX_PATH],temporary[MAX_PATH],program[MAX_PATH],prefix[2*MAX_PATH];
        GetModuleFileNameW(NULL,directory,MAX_PATH);
        wchar_t *slash=wcsrchr(directory,L'\\');assert(slash);*slash=0;
        assert(GetTempFileNameW(directory,L"nsp",0,temporary));
        wcscpy(ini,temporary);
        assert(WritePrivateProfileStringW(L"NSP",L"Python",L"python.exe",ini));
        assert(WritePrivateProfileStringW(L"NSP",L"ControlScript",L"legacy.py",ini));
        assert(nsp_program(program,prefix,2*MAX_PATH));
        assert(!prefix[0] && wcsstr(program,L"zig-scheduler\\nsp-scheduler.exe"));
        assert(WritePrivateProfileStringW(L"NSP",L"Scheduler",L"Invalid",ini));
        assert(!nsp_program(program,prefix,2*MAX_PATH));
        assert(WritePrivateProfileStringW(L"NSP",L"Scheduler",L"Python",ini));
        assert(nsp_program(program,prefix,2*MAX_PATH));
        assert(!wcscmp(program,L"python.exe") && !wcscmp(prefix,L"\"legacy.py\" "));
        assert(DeleteFileW(temporary));ini[0]=0;
        puts("PASS default Zig resolution, no implicit Python fallback, explicit legacy selection");
    }
    assert(sizeof(spatial_profiles)/sizeof(spatial_profiles[0])==3);
    assert(sizeof(encoders)/sizeof(encoders[0])==3);
    AL_Snapshot state={0};SpatialSettings sonic={0};sonic.words[0]=1;
    memcpy(&sonic.words[3],&encoders[2],16);
    assert(classify(&state,&sonic)==MODE_SONIC);
    assert(classify(&state,&sonic)!=MODE_NSP);
    for(int i=0;i<7;i++){
        GUID g;assert(SUCCEEDED(CLSIDFromString(pipeline_guids[i],&g)));memcpy(&sonic.words[3],&g,16);
        assert(classify(&state,&sonic)==pipeline_modes[i]);
        assert(pipeline_format_for_mode(pipeline_modes[i])==i);
        menu_state.selected=pipeline_modes[i];assert(fsa_menu_checked(&menu_state,100+pipeline_modes[i]));
    }
    wake=CreateEventW(NULL,FALSE,FALSE,NULL);
    available_modes=1u<<MODE_STEREO;
    nsp_ui_locked=1;manual=-1;
    request_manual_mode(MODE_STEREO);
    assert(nsp_next_mode==MODE_STEREO && nsp_requested==2 && manual==-1);
    request_manual_mode(MODE_DTS);
    assert(nsp_next_mode==MODE_STEREO && manual==-1);
    nsp_ui_locked=0;nsp_next_mode=-1;nsp_requested=0;
    request_manual_mode(MODE_STEREO);
    assert(manual==MODE_STEREO && nsp_requested==0);
    manual=-1;CloseHandle(wake);wake=NULL;
    if(argc==2){
        assert(wcslen(argv[1])<MAX_PATH);wcscpy(ini,argv[1]);
        assert(nsp_configured());
        assert(nsp_control(L"status")==NSP_STOPPED);
        assert(nsp_control(L"stop")==NSP_STOPPED);
        assert(nsp_control(L"emergency-stop")==NSP_STOPPED);
        puts("PASS native FSA -> selected scheduler -> correlated user-state disk read (idle/stop/emergency)");
    }
    puts("PASS seven spatial identities, active checkmarks, NSp frontend mapping and separate Hook qualification");
    return 0;
}
