/* FSA worker control for the optional player adapter. */
#ifndef FSA_FILMS_TV_H
#define FSA_FILMS_TV_H
static HANDLE films_tv_prepare;
static ULONGLONG films_tv_due;

static void films_tv_launch(int disabling) {
    wchar_t controller[MAX_PATH], program[MAX_PATH], command[4096];
    DWORD length=GetModuleFileNameW(NULL,controller,MAX_PATH);
    if(!length||length>=MAX_PATH)return;
    wchar_t *slash=wcsrchr(controller,L'\\');
    if(!slash||wcslen(L"NspMftRouteControl.exe")+1>MAX_PATH-(size_t)(slash+1-controller))return;
    wcscpy(slash+1,L"NspMftRouteControl.exe");
    if(!nsp_program(program))return;
    int count=swprintf(command,4096,L"\"%ls\" media-foundation --mft-controller \"%ls\"%ls",
                       program,controller,disabling?L" --disable":L"");
    if(count<0||(!disabling&&!nsp_append_playback(command,4096,ini)))return;
    STARTUPINFOW startup={.cb=sizeof(startup)};PROCESS_INFORMATION child={0};
    if(CreateProcessW(program,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&startup,&child)){
        CloseHandle(child.hThread);films_tv_prepare=child.hProcess;
    }else log_line(L"Media Foundation controller",HRESULT_FROM_WIN32(GetLastError()));
}

static int films_tv_collect(DWORD timeout) {
    if(!films_tv_prepare)return 1;
    if(WaitForSingleObject(films_tv_prepare,timeout)!=WAIT_OBJECT_0)return 0;
    DWORD code=1;
    GetExitCodeProcess(films_tv_prepare,&code);
    CloseHandle(films_tv_prepare);films_tv_prepare=NULL;
    if(code)log_line(L"Media Foundation controller",HRESULT_FROM_WIN32(code));
    return 1;
}

static void films_tv_tick(void) {
    if(quiet_start||!films_tv_collect(0)||GetTickCount64()<films_tv_due)return;
    films_tv_due=GetTickCount64()+30000;
    films_tv_launch(0);
}

static void films_tv_shutdown(void) {
    if(!quiet_start&&films_tv_due&&films_tv_collect(20000)){
        films_tv_launch(1);
        if(!films_tv_collect(20000))log_line(L"Media Foundation shutdown pending",HRESULT_FROM_WIN32(WAIT_TIMEOUT));
    }
    if(films_tv_prepare)CloseHandle(films_tv_prepare);
}
#endif
