#include "final_spatial_audio.c"
#include <assert.h>
int wmain(int argc,wchar_t **argv){
    assert(argc==2 || argc==3);
    assert(wcslen(argv[1])<MAX_PATH);wcscpy(ini,argv[1]);
    wchar_t command[4096]=L"nsp start";
    assert(nsp_append_playback(command,4096,ini,0));
    assert(wcsstr(command,L" --playback-config \"")!=NULL);
    wchar_t legacy[4096]=L"legacy start";
    assert(!nsp_append_playback(legacy,4096,ini,1));
    wchar_t small[12]=L"nsp start";
    assert(!nsp_append_playback(small,12,ini,0));
    if(argc==3){
        wcscpy(profiles[0].endpoint,L"fixture-endpoint");
        assert(nsp_control(L"start")==11);
        puts("PASS native FSA worker dispatch -> Zig -> correlated status/profile");
    }
    puts("PASS optional profile quoting, legacy rejection and command bound");
    return 0;
}
