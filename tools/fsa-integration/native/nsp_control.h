/* Worker-only bridge. Files are observations, never proof that Hook is off. */
static LONG nsp_requested;
static LONG nsp_ui_locked;
static LONG nsp_next_mode=-1;
/* Native scheduler is opt-in until live migration has been explicitly chosen.
   Its interface matches the existing adapter and correlated disk markers. */
static int nsp_program(wchar_t *program,wchar_t *prefix,size_t capacity) {
    wchar_t script[MAX_PATH];
    GetPrivateProfileStringW(L"NSP",L"ControlExecutable",L"",program,MAX_PATH,ini);
    if(program[0]){prefix[0]=0;return !wcschr(program,L'"');}
    GetPrivateProfileStringW(L"NSP",L"Python",L"",program,MAX_PATH,ini);
    GetPrivateProfileStringW(L"NSP",L"ControlScript",L"",script,MAX_PATH,ini);
    if(!program[0] || !script[0] || wcschr(program,L'"') || wcschr(script,L'"'))return 0;
    swprintf(prefix,capacity,L"\"%ls\" ",script);return 1;
}
/* Emergency dispatch bypasses the potentially blocked arbitration worker. The
   short-lived adapter verifies the target instance before calling native stop. */
static int nsp_emergency_dispatch(void) {
    wchar_t python[MAX_PATH],prefix[2*MAX_PATH],command[2048];
    if(!nsp_program(python,prefix,2*MAX_PATH))return 0;
    swprintf(command,2048,L"\"%ls\" %lsemergency-stop",python,prefix);
    STARTUPINFOW startup={.cb=sizeof(startup)};PROCESS_INFORMATION child={0};
    if(!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&startup,&child))return 0;
    CloseHandle(child.hThread);CloseHandle(child.hProcess);return 1;
}
static int nsp_configured(void) {
    wchar_t value[MAX_PATH];
    GetPrivateProfileStringW(L"NSP",L"ControlExecutable",L"",value,MAX_PATH,ini);
    if(value[0])return GetFileAttributesW(value)!=INVALID_FILE_ATTRIBUTES;
    GetPrivateProfileStringW(L"NSP",L"ControlScript",L"",value,MAX_PATH,ini);
    return value[0] && GetFileAttributesW(value)!=INVALID_FILE_ATTRIBUTES;
}
static int nsp_control(const wchar_t *action) {
    wchar_t python[MAX_PATH],prefix[2*MAX_PATH],command[4096],local[MAX_PATH],marker[MAX_PATH],request_id[80],reply_id[80];
    if(!nsp_program(python,prefix,2*MAX_PATH))return 20;
    if(!GetEnvironmentVariableW(L"LOCALAPPDATA",local,MAX_PATH))return 20;
    swprintf(marker,MAX_PATH,L"%ls\\NativeSpatialPipe\\hook-status.ini",local);
    swprintf(request_id,80,L"%lu-%llu",GetCurrentProcessId(),GetTickCount64());
    swprintf(command,4096,L"\"%ls\" %ls%ls --owner-pid %lu --endpoint \"%ls\" --request-id %ls",python,prefix,action,GetCurrentProcessId(),profiles[0].endpoint,request_id);
    if(!prefix[0]){
        wchar_t telemetry_python[MAX_PATH];
        GetPrivateProfileStringW(L"NSP",L"Python",L"",telemetry_python,MAX_PATH,ini);
        if(telemetry_python[0] && !wcschr(telemetry_python,L'"')){
            size_t used=wcslen(command);
            swprintf(command+used,4096-used,L" --python \"%ls\"",telemetry_python);
        }
    }
    STARTUPINFOW startup={.cb=sizeof(startup)};PROCESS_INFORMATION child={0};
    if(!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&startup,&child))return 20;
    DWORD wait=WaitForSingleObject(child.hProcess,45000),code=20;
    if(wait==WAIT_OBJECT_0)GetExitCodeProcess(child.hProcess,&code);
    CloseHandle(child.hThread);CloseHandle(child.hProcess);
    if(wait!=WAIT_OBJECT_0)return 20;
    int disk=(int)GetPrivateProfileIntW(L"NSP",L"Code",20,marker);
    GetPrivateProfileStringW(L"NSP",L"RequestId",L"",reply_id,80,marker);
    if(wcscmp(request_id,reply_id))return 20;
    ULONGLONG checked=(ULONGLONG)GetPrivateProfileIntW(L"NSP",L"CheckedAt",0,marker);
    FILETIME ft;GetSystemTimeAsFileTime(&ft);ULARGE_INTEGER now;now.LowPart=ft.dwLowDateTime;now.HighPart=ft.dwHighDateTime;
    ULONGLONG seconds=now.QuadPart/10000000ULL-11644473600ULL;
    if(seconds<checked || seconds-checked>5 || disk!=(int)code)return 20;
    if(wcscmp(action,L"status")){wchar_t message[128];swprintf(message,128,L"Sonic NSp %ls result=%lu",action,code);log_line(message,code==20?E_FAIL:S_OK);}
    return (int)code;
}
