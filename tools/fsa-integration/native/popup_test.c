#include "fluent_menu.h"
#include <stdio.h>
#include <dwmapi.h>
static int test_case,ticks,geometry_failed,phase;
static POINT original_cursor;
static LRESULT CALLBACK owner_proc(HWND hwnd,UINT msg,WPARAM w,LPARAM l) {
    if(msg==WM_TIMER) {
        HWND popup=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio");
        if(!popup) {if(++ticks>25) PostQuitMessage(1);return 0;}
        KillTimer(hwnd,1);
        if(test_case>=16) {
            SendMessageW(popup,WM_KEYDOWN,VK_HOME,0);
            for(int i=0;i<(test_case==18?5:test_case==17?1:0);i++)SendMessageW(popup,WM_KEYDOWN,VK_DOWN,0);
            SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);
            return 0;
        }
        if(test_case>=13) {
            if(test_case==13){
                SendMessageW(popup,WM_KEYDOWN,VK_HOME,0);
                for(int i=0;i<6;i++)SendMessageW(popup,WM_KEYDOWN,VK_DOWN,0);
                SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);
            } else {
                SendMessageW(popup,WM_KEYDOWN,VK_LEFT,0);
                SendMessageW(popup,WM_KEYDOWN,VK_END,0);
                if(test_case==15)SendMessageW(popup,WM_KEYDOWN,VK_UP,0);
                SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);
            }
            return 0;
        }
        if(test_case==12) {
            if(phase==0) {
                SendMessageW(popup,WM_KEYDOWN,VK_LEFT,0);
                phase=1;SetTimer(hwnd,1,250,NULL);return 0;
            }
            HWND child=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced");
            if(!child){geometry_failed=1;SendMessageW(popup,WM_CLOSE,0,0);SetCursorPos(original_cursor.x,original_cursor.y);return 0;}
            UINT dpi=GetDpiForWindow(popup);
            if(phase==1) {
                POINT margin={MulDiv(3,dpi,96),MulDiv(303,dpi,96)},screen=margin;
                ClientToScreen(popup,&screen);SetCursorPos(screen.x,screen.y);
                SendMessageW(popup,WM_MOUSEMOVE,0,MAKELPARAM(margin.x,margin.y));
                if(!IsWindow(child))geometry_failed=1;
                phase=2;SetTimer(hwnd,1,200,NULL);return 0;
            }
            if(phase==2) {
                POINT gap={-MulDiv(2,dpi,96),MulDiv(303,dpi,96)};ClientToScreen(popup,&gap);SetCursorPos(gap.x,gap.y);
                SendMessageW(popup,WM_MOUSELEAVE,0,0);
                phase=3;SetTimer(hwnd,1,200,NULL);return 0;
            }
            POINT row={MulDiv(70,dpi,96),MulDiv(24,dpi,96)},screen=row;
            ClientToScreen(child,&screen);SetCursorPos(screen.x,screen.y);
            SendMessageW(child,WM_MOUSEMOVE,0,MAKELPARAM(row.x,row.y));
            SendMessageW(child,WM_LBUTTONUP,0,MAKELPARAM(row.x,row.y));
            SetCursorPos(original_cursor.x,original_cursor.y);return 0;
        }
        if(test_case>=10) {
            if(phase==0) {
                SendMessageW(popup,WM_KEYDOWN,VK_LEFT,0);
                HWND child=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced");
                DWORD border=0xfffffffeu;
                LRESULT activation=SendMessageW(child,WM_MOUSEACTIVATE,(WPARAM)popup,MAKELPARAM(HTCLIENT,WM_LBUTTONDOWN));
                if(!child || GetActiveWindow()!=popup || activation!=MA_NOACTIVATE){printf("Activation failure %d: child=%p active=%p root=%p result=%lld\n",test_case,(void*)child,(void*)GetActiveWindow(),(void*)popup,(long long)activation);geometry_failed=1;}
                /* Border color is a setter-only DWM attribute; querying it is invalid. */
                HRESULT border_hr=DwmSetWindowAttribute(popup,34,&border,sizeof(border));
                if(FAILED(border_hr)){printf("Border API failure: %lx\n",(unsigned long)border_hr);geometry_failed=1;}
                if(test_case==10) {
                    SendMessageW(popup,WM_MOUSEMOVE,0,MAKELPARAM(70,20));
                    if(IsWindow(child))geometry_failed=1;
                    SendMessageW(popup,WM_CLOSE,0,0);return 0;
                }
                SetCursorPos(0,0);SendMessageW(popup,WM_MOUSELEAVE,0,0);
                phase=1;SetTimer(hwnd,1,750,NULL);return 0;
            }
            if(FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced") || !IsWindowVisible(popup))geometry_failed=1;
            SendMessageW(popup,WM_CLOSE,0,0);SetCursorPos(original_cursor.x,original_cursor.y);return 0;
        }
        if(test_case>=8) {
            if(phase==0) {
                POINT at={70,303};UINT dpi=GetDpiForWindow(popup);at.x=MulDiv(at.x,dpi,96);at.y=MulDiv(at.y,dpi,96);
                POINT screen=at;ClientToScreen(popup,&screen);SetCursorPos(screen.x,screen.y);
                SendMessageW(popup,WM_MOUSEMOVE,0,MAKELPARAM(at.x,at.y));
                if(FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced"))geometry_failed=1;
                phase=1;SetTimer(hwnd,1,100,NULL);return 0;
            }
            if(phase==1) {
                if(FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced"))geometry_failed=1;
                if(test_case==9) {
                    POINT screen={70,20};ClientToScreen(popup,&screen);SetCursorPos(screen.x,screen.y);
                    SendMessageW(popup,WM_MOUSELEAVE,0,0);
                }
                phase=2;SetTimer(hwnd,1,1100,NULL);return 0;
            }
            HWND child=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced");
            if((test_case==8 && !child) || (test_case==9 && child))geometry_failed=1;
            SendMessageW(popup,WM_CLOSE,0,0);SetCursorPos(original_cursor.x,original_cursor.y);return 0;
        }
        if(test_case==0) {SendMessageW(popup,WM_KEYDOWN,VK_END,0);SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);}
        if(test_case==1) SendMessageW(popup,WM_KEYDOWN,VK_ESCAPE,0);
        if(test_case==2) {SendMessageW(popup,WM_KEYDOWN,VK_HOME,0);SendMessageW(popup,WM_KEYDOWN,VK_DOWN,0);SendMessageW(popup,WM_KEYDOWN,VK_SPACE,0);}
        if(test_case==4){
            RECT before,after,child_rect;GetWindowRect(popup,&before);
            SendMessageW(popup,WM_KEYDOWN,VK_LEFT,0);
            HWND child=FindWindowW(L"FSAFluentPopup",L"Final Spatial Audio Advanced");
            GetWindowRect(popup,&after);
            if(!child || !IsWindowVisible(popup) || !EqualRect(&before,&after))geometry_failed=1;
            if(child){GetWindowRect(child,&child_rect);if(child_rect.right>before.left && child_rect.left<before.right)geometry_failed=1;}
SendMessageW(popup,WM_KEYDOWN,VK_HOME,0);SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);}
        if(test_case==5){SendMessageW(popup,WM_KEYDOWN,VK_RIGHT,0);SendMessageW(popup,WM_KEYDOWN,VK_END,0);SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);}
        if(test_case==6){SendMessageW(popup,WM_KEYDOWN,VK_HOME,0);SendMessageW(popup,WM_KEYDOWN,VK_DOWN,0);SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);}
        if(test_case==7){SendMessageW(popup,WM_KEYDOWN,VK_RIGHT,0);SendMessageW(popup,WM_KEYDOWN,VK_ESCAPE,0);SendMessageW(popup,WM_KEYDOWN,VK_END,0);SendMessageW(popup,WM_KEYDOWN,VK_RETURN,0);}
        if(test_case==3) SendMessageW(popup,WM_ACTIVATE,WA_INACTIVE,0);
        return 0;
    }
    return DefWindowProcW(hwnd,msg,w,l);
}
int main(int argc,char **argv) {
    FSA_Language test_language=argc>1 && !strcmp(argv[1],"en")?FSA_EN:FSA_ZH;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    HINSTANCE instance=GetModuleHandleW(NULL);
    WNDCLASSW wc={0};wc.hInstance=instance;wc.lpfnWndProc=owner_proc;wc.lpszClassName=L"FSAPopupTestOwner";RegisterClassW(&wc);
    HWND owner=CreateWindowW(wc.lpszClassName,L"Popup test",0,0,0,0,0,NULL,NULL,instance,NULL);
    if(!owner)return 1;
    UINT expected[]={205,0,101,0,206,209,200,205,0,0,0,0,206,106,209,208,100,102,105};int failed=0;
    for(test_case=0;test_case<19;test_case++) {
        ticks=phase=0;GetCursorPos(&original_cursor);SetCursorPos(0,0);SetTimer(owner,1,250,NULL);
        FSA_MenuState state={2,1,0,test_case==6?1u:test_case==17?125u:127u,test_language,test_case>=14};
        UINT actual=fsa_popup(owner,&state);
        SetCursorPos(original_cursor.x,original_cursor.y);
        KillTimer(owner,1);
        printf("Popup case %d: got %u expected %u geometry=%d\n",test_case,actual,expected[test_case],geometry_failed);
        if(actual!=expected[test_case] || ticks>25) {failed=1;break;}
    }
    printf("Submenu geometry, nonactivation, border, hover delay, dismissal and pointer crossing/click: %s\n",geometry_failed?"FAIL":"PASS");
    DestroyWindow(owner);return failed || geometry_failed;
}
