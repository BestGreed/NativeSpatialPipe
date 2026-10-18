/* Desktop compositor regression: only app-owned windows, no audio operations. */
#include "fluent_menu.h"
#include <dwmapi.h>
#include <stdio.h>
static int phase,failed;
static COLORREF background=RGB(0,0,0),before[2];
static COLORREF sample(HWND hwnd) {
    RECT r;GetWindowRect(hwnd,&r);HDC dc=GetDC(NULL);
    COLORREF color=GetPixel(dc,(r.left+r.right)/2,r.top+4);ReleaseDC(NULL,dc);return color;
}
static LRESULT CALLBACK proc(HWND hwnd,UINT msg,WPARAM w,LPARAM l) {
    if(msg==WM_PAINT){PAINTSTRUCT ps;HDC dc=BeginPaint(hwnd,&ps);RECT r;GetClientRect(hwnd,&r);HBRUSH brush=CreateSolidBrush(background);FillRect(dc,&r,brush);DeleteObject(brush);EndPaint(hwnd,&ps);return 0;}
    if(msg==WM_TIMER) {
        HWND root=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio");
        HWND child=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced");
        if(!root){failed=1;KillTimer(hwnd,1);PostQuitMessage(1);return 0;}
        if(phase==0){SendMessageW(root,WM_KEYDOWN,VK_LEFT,0);phase=1;SetTimer(hwnd,1,400,NULL);return 0;}
        if(!child){failed=1;KillTimer(hwnd,1);SendMessageW(root,WM_CLOSE,0,0);return 0;}
        if(phase==1){
            before[0]=sample(root);before[1]=sample(child);
            background=RGB(255,255,255);RedrawWindow(hwnd,NULL,NULL,RDW_INVALIDATE|RDW_UPDATENOW);DwmFlush();
            phase=2;SetTimer(hwnd,1,400,NULL);return 0;
        }
        HWND menus[]={root,child};
        for(int i=0;i<2;i++){
            COLORREF after=sample(menus[i]);
            int change=abs((int)GetRValue(after)-(int)GetRValue(before[i]))+abs((int)GetGValue(after)-(int)GetGValue(before[i]))+abs((int)GetBValue(after)-(int)GetBValue(before[i]));
            printf("%s backdrop: black=%06lx white=%06lx delta=%d\n",i?"Inactive submenu":"Active main",(unsigned long)before[i],(unsigned long)after,change);
            if(after==CLR_INVALID || before[i]==CLR_INVALID || change<9)failed=1;
            if(GetWindowLongPtrW(menus[i],GWL_STYLE)&WS_THICKFRAME)failed=1;
        }
        if(GetActiveWindow()!=root)failed=1;
        KillTimer(hwnd,1);SendMessageW(root,WM_CLOSE,0,0);return 0;
    }
    return DefWindowProcW(hwnd,msg,w,l);
}
int main(void) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    HINSTANCE instance=GetModuleHandleW(NULL);WNDCLASSW wc={0};wc.hInstance=instance;wc.lpfnWndProc=proc;wc.lpszClassName=L"FSAMaterialTest";RegisterClassW(&wc);
    POINT cursor;GetCursorPos(&cursor);MONITORINFO mi={.cbSize=sizeof(mi)};GetMonitorInfoW(MonitorFromPoint(cursor,MONITOR_DEFAULTTONEAREST),&mi);
    HWND owner=CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_TOPMOST,wc.lpszClassName,L"FSA compositor test",WS_POPUP,mi.rcWork.left,mi.rcWork.bottom-500,800,500,NULL,NULL,instance,NULL);
    ShowWindow(owner,SW_SHOWNOACTIVATE);UpdateWindow(owner);SetCursorPos(mi.rcWork.left+600,mi.rcWork.bottom);
    SetTimer(owner,1,400,NULL);FSA_MenuState state={2,1,0,31,FSA_ZH,0};fsa_popup(owner,&state);
    if(phase!=2)failed=1;
    KillTimer(owner,1);DestroyWindow(owner);SetCursorPos(cursor.x,cursor.y);
    printf("Compositor transparency: %s\n",failed?"FAIL":"PASS");return failed;
}
