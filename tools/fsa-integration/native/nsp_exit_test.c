#define FSA_CONTROL_TEST
#include "final_spatial_audio.c"
#include <assert.h>
static LONG fake_code=NSP_UNKNOWN,calls;
static int fake_control(const wchar_t *action) {
    assert(!wcscmp(action,L"shutdown"));
    InterlockedIncrement(&calls);
    return InterlockedCompareExchange(&fake_code,0,0);
}
static unsigned __stdcall test_worker(void *context) {(void)context;return exit_only_worker();}
static void pump(void) {
    MSG msg;
    while(PeekMessageW(&msg,NULL,0,0,PM_REMOVE)){
        if(msg.message!=WM_QUIT){TranslateMessage(&msg);DispatchMessageW(&msg);}
    }
}
int wmain(void) {
    HANDLE mutex=CreateMutexW(NULL,FALSE,L"Local\\FSA-NSp-Exit-Regression");
    assert(mutex && GetLastError()!=ERROR_ALREADY_EXISTS);
    wchar_t path[MAX_PATH];GetModuleFileNameW(NULL,path,MAX_PATH);
    assert(GetTempFileNameW(L".",L"fsa",0,ini));
    assert(WritePrivateProfileStringW(L"NSP",L"ControlExecutable",path,ini));
    assert(WritePrivateProfileStringW(L"NSP",L"Scheduler",L"Zig",ini));
    nsp_control=fake_control;language=FSA_EN;
    wake=CreateEventW(NULL,FALSE,FALSE,NULL);stop=CreateEventW(NULL,TRUE,FALSE,NULL);
    retry_start_after_stop(1,NSP_STOPPED,1);assert(nsp_requested==1);nsp_requested=0;
    retry_start_after_stop(1,NSP_BUSY,1);assert(nsp_requested==0);
    retry_start_after_stop(1,NSP_UNKNOWN,1);assert(nsp_requested==0);
    retry_start_after_stop(2,NSP_STOPPED,1);assert(nsp_requested==0);
    exit_requested=1;retry_start_after_stop(1,NSP_STOPPED,1);assert(nsp_requested==0);exit_requested=0;
    WNDCLASSW wc={0};wc.lpfnWndProc=wndproc;wc.hInstance=GetModuleHandleW(NULL);wc.lpszClassName=L"FSAExitRegressionOnly";
    assert(RegisterClassW(&wc));
    int observations[]={NSP_RUNNING,NSP_STOPPED,NSP_BUSY,NSP_PREFLIGHT,NSP_UNKNOWN};
    unsigned combinations=0;
    for(int locked=0;locked<=1;locked++)for(int target=MODE_STEREO;target<MODE_COUNT;target++){
        if(target==MODE_NSP)continue;
        for(unsigned i=0;i<sizeof(observations)/sizeof(*observations);i++){
            exit_requested=exit_verified=0;nsp_ui_locked=locked;nsp_next_mode=-1;nsp_requested=0;
            automatic=1;manual=-1;available_modes=(1u<<MODE_COUNT)-1;
            window=CreateWindowExW(0,wc.lpszClassName,L"Regression",0,0,0,0,0,NULL,NULL,wc.hInstance,NULL);
            assert(window);
            request_manual_mode(target);
            if(locked)assert(nsp_next_mode==target && manual==-1);
            else assert(manual==target);
            nsp_queue_request(3);nsp_queue_request(2);nsp_queue_request(1);
            assert(nsp_requested==3); /* Stops cannot be overwritten by start. */
            SendMessageW(window,WM_CLOSE,0,0);SendMessageW(window,WM_CLOSE,0,0);
            assert(IsWindow(window) && exit_requested && !exit_verified);
            assert(!automatic && manual==-1 && nsp_next_mode==-1 && nsp_requested==0);
            SendMessageW(window,WM_MODE_COMMAND,target,0);
            SendMessageW(window,WM_NSP_COMMAND,1,0);
            assert(manual==-1 && nsp_next_mode==-1 && nsp_requested==0);
            fake_code=observations[i];
            assert(exit_ready()==(observations[i]==NSP_STOPPED));
            SendMessageW(window,WM_EXIT_READY,0,0);assert(IsWindow(window));
            exit_verified=1;SendMessageW(window,WM_EXIT_READY,0,0);assert(!IsWindow(window));
            pump();ResetEvent(stop);combinations++;
        }
    }
    /* Exercise the real worker/message loop: unknown retains the window;
       verified stop releases the worker and then destroys it. No audio API. */
    exit_requested=exit_verified=0;fake_code=NSP_UNKNOWN;calls=0;
    window=CreateWindowExW(0,wc.lpszClassName,L"Regression",0,0,0,0,0,NULL,NULL,wc.hInstance,NULL);assert(window);
    thread=(HANDLE)_beginthreadex(NULL,0,test_worker,NULL,0,NULL);assert(thread);
    SendMessageW(window,WM_CLOSE,0,0);
    ULONGLONG deadline=GetTickCount64()+3000;
    while(!InterlockedCompareExchange(&calls,0,0) && GetTickCount64()<deadline){pump();Sleep(5);}
    assert(calls && IsWindow(window) && !exit_verified);
    fake_code=NSP_STOPPED;SetEvent(wake);deadline=GetTickCount64()+3000;
    while(IsWindow(window) && GetTickCount64()<deadline){pump();Sleep(5);}
    assert(!IsWindow(window));assert(WaitForSingleObject(thread,1000)==WAIT_OBJECT_0);
    CloseHandle(thread);CloseHandle(wake);CloseHandle(stop);CloseHandle(mutex);
    assert(DeleteFileW(ini));
    printf("PASS %u mode/lock/observation combinations; repeated close, priority, stale requests, live worker exit and mutex release\n",combinations);
    return 0;
}
