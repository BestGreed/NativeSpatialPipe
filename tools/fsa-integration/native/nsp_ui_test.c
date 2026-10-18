#include "final_spatial_audio.c"
#include <assert.h>
int wmain(int argc,wchar_t **argv) {
    assert(!wcscmp(names[MODE_NSP],L"Sonic NSp"));
    assert(!wcscmp(fsa_text(FSA_EN,TXT_NSP),L"Sonic NSp"));
    assert(!wcscmp(fsa_text(FSA_ZH,TXT_NSP),L"Sonic NSp"));
    assert(mode_icon_ids[MODE_NSP]==IDI_FSA_APP);
    assert(sizeof(spatial_profiles)/sizeof(spatial_profiles[0])==3);
    assert(sizeof(encoders)/sizeof(encoders[0])==3);
    AL_Snapshot state={0};SpatialSettings sonic={0};sonic.words[0]=1;
    memcpy(&sonic.words[3],&encoders[2],16);
    assert(classify(&state,&sonic)==MODE_SONIC);
    assert(classify(&state,&sonic)!=MODE_NSP);
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
        puts("PASS native FSA -> Python adapter -> correlated user-state disk read (idle/stop/emergency)");
    }
    puts("PASS Sonic NSp display, FSA application icon, Sonic snapshot slot and separate Hook qualification");
    return 0;
}
