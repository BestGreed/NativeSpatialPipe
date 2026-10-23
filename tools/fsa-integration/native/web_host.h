#ifndef FSA_WEB_HOST_H
#define FSA_WEB_HOST_H
#include "web_contract.h"
typedef struct FSA_TrayClicks { unsigned count; ULONGLONG last; } FSA_TrayClicks;
/* Return 1 only on the third release; ignore shell double-click down events. */
static inline int fsa_tray_click(FSA_TrayClicks *clicks,ULONGLONG now,UINT interval) {
    if(!clicks->count || now-clicks->last>interval)clicks->count=0;
    clicks->last=now;
    if(++clicks->count==3){clicks->count=0;return 1;}
    return 0;
}
static inline BOOL fsa_web_spawn(const wchar_t *exe,wchar_t *command,PROCESS_INFORMATION *child,DWORD *stage) {
    *stage=1;
    STARTUPINFOW startup={.cb=sizeof(startup)};
    startup.lpDesktop=L"winsta0\\default";
    HANDLE own=NULL;TOKEN_ELEVATION elevation={0};DWORD length=0;
    if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&own))return FALSE;
    *stage=2;BOOL queried=GetTokenInformation(own,TokenElevation,&elevation,sizeof(elevation),&length);CloseHandle(own);
    if(!queried)return FALSE;
    if(!elevation.TokenIsElevated){*stage=3;return CreateProcessW(exe,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&startup,child);}
    /* The HTTP service must not inherit FSA's administrator token. */
    DWORD pid=0;GetWindowThreadProcessId(GetShellWindow(),&pid);
    *stage=4;HANDLE shell=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid),token=NULL,primary=NULL;
    BOOL result=FALSE;
    if(shell){
        *stage=5;
        if(OpenProcessToken(shell,TOKEN_QUERY|TOKEN_DUPLICATE,&token)){
            *stage=6;
            if(DuplicateTokenEx(token,MAXIMUM_ALLOWED,NULL,SecurityImpersonation,TokenPrimary,&primary)){
                *stage=7;result=CreateProcessWithTokenW(primary,0,exe,command,CREATE_NO_WINDOW,NULL,NULL,&startup,child);
            }
        }
    }
    DWORD error=GetLastError();
    if(primary)CloseHandle(primary);if(token)CloseHandle(token);if(shell)CloseHandle(shell);
    SetLastError(error);
    return result;
}
#endif
