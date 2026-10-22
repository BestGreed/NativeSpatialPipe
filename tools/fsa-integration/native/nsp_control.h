/* Worker-only bridge. Files are observations, never proof that Hook is off. */
#include "nsp_playback.h"
static LONG nsp_requested;
static LONG nsp_ui_locked;
static LONG nsp_next_mode=-1;
static HANDLE nsp_emergency_process;
static HANDLE nsp_pending_control;
static void nsp_queue_request(LONG request) {
    LONG previous=InterlockedCompareExchange(&nsp_requested,0,0);
    while(previous<request){
        LONG observed=InterlockedCompareExchange(&nsp_requested,request,previous);
        if(observed==previous)return;
        previous=observed;
    }
}
/* Zig is the default. Python orchestration requires Scheduler=Python; a
   missing native executable must never silently select the legacy host. */
static int nsp_program(wchar_t *program,wchar_t *prefix,size_t capacity) {
    wchar_t script[MAX_PATH],scheduler[32];
    GetPrivateProfileStringW(L"NSP",L"Scheduler",L"Zig",scheduler,32,ini);
    if(!_wcsicmp(scheduler,L"Python")){
        GetPrivateProfileStringW(L"NSP",L"Python",L"",program,MAX_PATH,ini);
        GetPrivateProfileStringW(L"NSP",L"ControlScript",L"",script,MAX_PATH,ini);
        if(!program[0] || !script[0] || wcschr(program,L'"') || wcschr(script,L'"'))return 0;
        swprintf(prefix,capacity,L"\"%ls\" ",script);return 1;
    }
    if(_wcsicmp(scheduler,L"Zig"))return 0;
    prefix[0]=0;
    GetPrivateProfileStringW(L"NSP",L"ControlExecutable",L"",program,MAX_PATH,ini);
    if(program[0])return !wcschr(program,L'"');
    wcscpy(script,ini);
    wchar_t *slash=wcsrchr(script,L'\\');if(!slash)slash=wcsrchr(script,L'/');
    if(!slash)return 0;*slash=0;
    wchar_t relative[2*MAX_PATH];
    swprintf(relative,2*MAX_PATH,L"%ls\\..\\zig-scheduler\\nsp-scheduler.exe",script);
    DWORD length=GetFullPathNameW(relative,MAX_PATH,program,NULL);
    return length>0 && length<MAX_PATH && !wcschr(program,L'"');
}
/* Emergency dispatch bypasses the potentially blocked arbitration worker. The
   short-lived adapter verifies the target instance before calling native stop. */
static int nsp_emergency_dispatch(void) {
    if(nsp_emergency_process){
        if(WaitForSingleObject(nsp_emergency_process,0)==WAIT_TIMEOUT)return 1;
        CloseHandle(nsp_emergency_process);nsp_emergency_process=NULL;
    }
    wchar_t python[MAX_PATH],prefix[2*MAX_PATH],command[2048];
    if(!nsp_program(python,prefix,2*MAX_PATH))return 0;
    swprintf(command,2048,L"\"%ls\" %lsemergency-stop",python,prefix);
    STARTUPINFOW startup={.cb=sizeof(startup)};PROCESS_INFORMATION child={0};
    if(!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&startup,&child))return 0;
    CloseHandle(child.hThread);nsp_emergency_process=child.hProcess;return 1;
}
static int nsp_configured(void) {
    wchar_t program[MAX_PATH],prefix[2*MAX_PATH];
    return nsp_program(program,prefix,2*MAX_PATH) && GetFileAttributesW(program)!=INVALID_FILE_ATTRIBUTES;
}
static int nsp_control_process(const wchar_t *action) {
    /* A timed-out adapter may still be unwinding native teardown. Keep its
       handle and refuse duplicate children instead of accumulating processes. */
    if(nsp_pending_control){
        if(WaitForSingleObject(nsp_pending_control,0)==WAIT_TIMEOUT)return NSP_UNKNOWN;
        CloseHandle(nsp_pending_control);nsp_pending_control=NULL;
    }
    wchar_t python[MAX_PATH],prefix[2*MAX_PATH],command[4096],local[MAX_PATH],marker[MAX_PATH],request_id[80],reply_id[80];
    if(!nsp_program(python,prefix,2*MAX_PATH))return 20;
    if(!GetEnvironmentVariableW(L"LOCALAPPDATA",local,MAX_PATH))return 20;
    swprintf(marker,MAX_PATH,L"%ls\\NativeSpatialPipe\\hook-status.ini",local);
    swprintf(request_id,80,L"%lu-%llu",GetCurrentProcessId(),GetTickCount64());
    swprintf(command,4096,L"\"%ls\" %ls%ls --owner-pid %lu --endpoint \"%ls\" --request-id %ls",python,prefix,action,GetCurrentProcessId(),profiles[0].endpoint,request_id);
    if(!wcscmp(action,L"start")){
        wchar_t format[80];GetPrivateProfileStringW(L"NSP",L"FrontendFormat",L"WindowsSonic",format,80,ini);
        if(wcschr(format,L'"') || wcschr(format,L' '))return NSP_UNKNOWN;
        size_t used=wcslen(command);swprintf(command+used,4096-used,L" --spatial-format %ls",format);
        if(!nsp_append_playback(command,4096,ini,prefix[0]!=0))return NSP_UNKNOWN;
    }
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
    CloseHandle(child.hThread);
    if(wait!=WAIT_OBJECT_0){nsp_pending_control=child.hProcess;return 20;}
    CloseHandle(child.hProcess);
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
#ifdef FSA_CONTROL_TEST
static int (*nsp_control)(const wchar_t *)=nsp_control_process;
#else
#define nsp_control nsp_control_process
#endif
static int nsp_shutdown(void) {
    wchar_t program[MAX_PATH],prefix[2*MAX_PATH];
    if(!nsp_program(program,prefix,2*MAX_PATH))return NSP_UNKNOWN;
    /* The explicit legacy adapter predates the shutdown action. Its stop
       still performs the same live Hook-off verification. */
    return nsp_control(prefix[0]?L"stop":L"shutdown");
}
