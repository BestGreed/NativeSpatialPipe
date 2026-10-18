#include "fluent_menu.h"
#include <windowsx.h>
#include <dwmapi.h>
#include <uxtheme.h>
#include <stdio.h>
#include <stdlib.h>
static const FSA_Text main_labels[]={TXT_STEREO,TXT_51,TXT_71,TXT_ATMOS,TXT_DTS,TXT_NSP,TXT_AUTO,TXT_ADVANCED,TXT_EXIT};
static const UINT main_commands[]={100,101,102,103,104,106,200,300,205};
static const FSA_Text advanced_labels[]={TXT_CAPTURE,TXT_EDIT,TXT_RELOAD,TXT_LOG,TXT_STARTUP,TXT_NSP_STOP,TXT_NSP_EMERGENCY};
static const UINT advanced_commands[]={206,202,203,204,207,208,209};
typedef struct {HMONITOR monitor;RECT rect;HWND hwnd;} Taskbar;
static BOOL CALLBACK find_taskbar(HWND hwnd,LPARAM context) {
    Taskbar *bar=(Taskbar*)context;wchar_t name[64];GetClassNameW(hwnd,name,64);
    if((!wcscmp(name,L"Shell_TrayWnd") || !wcscmp(name,L"Shell_SecondaryTrayWnd")) && MonitorFromWindow(hwnd,MONITOR_DEFAULTTONULL)==bar->monitor) {
        bar->hwnd=hwnd;GetWindowRect(hwnd,&bar->rect);return FALSE;
    }
    return TRUE;
}
typedef struct Popup {
    struct Popup *parent,*child;
    RECT final; POINT aim_origin; ULONGLONG animation_start; int animating;
    HWND hwnd; HFONT font,icon_font; HTHEME theme;
    int hover,dpi,dark,glass,advanced;
    FSA_MenuState state;
    int anchor_x,anchor_bottom; RECT work;
    UINT command; BOOL done;
} Popup;
static int px(Popup *p,int value) { return MulDiv(value,p->dpi,96); }
static int count(Popup *p) { return p->advanced?7:9; }
static int top(Popup *p,int i) { return 8+i*34+(!p->advanced && i>=6?9:0); }
static UINT command_at(Popup *p,int i) { return p->advanced?advanced_commands[i]:main_commands[i]; }
static int enabled(Popup *p,int i) { UINT c=command_at(p,i);if(p->state.nsp_locked && (c==106 || c==200 || c==206))return 0;return c<100 || c>=107 || (p->state.available&(1u<<(c-100))); }
static void position(Popup *p) {
    int height=px(p,top(p,count(p)-1)+40),width=px(p,200);
    int x=p->anchor_x-width,y=p->anchor_bottom-height;
    if(x+width>p->work.right)x=p->work.right-width;
    if(x<p->work.left)x=p->work.left;
    if(y<p->work.top)y=p->work.top;
    p->final=(RECT){x,y,x+width,y+height};
    SetWindowPos(p->hwnd,NULL,x,y,width,height,SWP_NOZORDER|SWP_NOACTIVATE);
}
/* Isolate the optional user32 composition ABI. Unlike the system backdrop,
   this material does not become opaque when an owned popup is inactive.
   No SDK/runtime dependency; missing or rejected support falls back to DWM. */
typedef struct { int state; DWORD flags,color,animation; } FSA_Accent;
typedef struct { int attribute; void *data; SIZE_T size; } FSA_CompositionData;
typedef BOOL (WINAPI *FSA_SetComposition)(HWND,const FSA_CompositionData*);
static int popup_material(HWND hwnd,int enabled,int dark) {
    FSA_SetComposition set=(FSA_SetComposition)(void*)GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetWindowCompositionAttribute");
    if(!set)return 0;
    FSA_Accent accent={enabled?4:0,0,dark?0xa0202020u:0xa0e8e8e8u,0};
    FSA_CompositionData data={19,&accent,sizeof(accent)};
    return set(hwnd,&data)!=FALSE;
}
static void appearance(Popup *p);
static void animate_frame(Popup *p) {
    double t=(GetTickCount64()-p->animation_start)/180.0;
    if(t>=1) {
        KillTimer(p->hwnd,1);p->animating=0;
        SetWindowPos(p->hwnd,NULL,p->final.left,p->final.top,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE);
        return;
    }
    /* A custom window region disables the DWM material on some Windows builds.
       Keep the window rectangular and move the whole compositor surface instead. */
    double remain=1-t;int offset=(int)(px(p,24)*remain*remain*remain);
    SetWindowPos(p->hwnd,NULL,p->final.left,p->final.top+offset,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE);

}
static void reveal(Popup *p) {
    BOOL animate=TRUE;SystemParametersInfoW(SPI_GETMENUANIMATION,0,&animate,0);
    /* Move the actual composited window: backdrop and text have one geometry.
       Disable DWM's separate transition to prevent a second background animation. */
    BOOL disabled=TRUE;DwmSetWindowAttribute(p->hwnd,DWMWA_TRANSITIONS_FORCEDISABLED,&disabled,sizeof(disabled));
    if(animate){p->animating=1;p->animation_start=GetTickCount64();animate_frame(p);SetTimer(p->hwnd,1,16,NULL);}
    RedrawWindow(p->hwnd,NULL,NULL,RDW_INVALIDATE|RDW_UPDATENOW);
    ShowWindow(p->hwnd,p->parent?SW_SHOWNOACTIVATE:SW_SHOWNORMAL);UpdateWindow(p->hwnd);
}
static void open_advanced(Popup *p) {
    if(p->child)return;
    Popup *child=calloc(1,sizeof(*child));if(!child)return;
    child->parent=p;child->advanced=1;child->hover=-1;child->dpi=p->dpi;child->font=p->font;child->icon_font=p->icon_font;child->state=p->state;child->work=p->work;
    child->anchor_x=p->final.left-px(p,4);
    child->anchor_bottom=p->final.top+px(p,top(p,7)+32);
    /* Only flip to the other side when there is insufficient space on the left. */
    if(child->anchor_x-px(p,200)<p->work.left)child->anchor_x=p->final.right+px(p,204);
    HINSTANCE instance=(HINSTANCE)GetWindowLongPtrW(p->hwnd,GWLP_HINSTANCE);
    HWND hwnd=CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_TOPMOST|WS_EX_NOACTIVATE,L"FSAFluentPopup",L"Final Spatial Audio Advanced",WS_POPUP,0,0,0,0,p->hwnd,NULL,instance,child);
    if(!hwnd){free(child);return;}
    GetCursorPos(&p->aim_origin);
    RECT entry={p->final.left,p->final.top+px(p,top(p,7)),p->final.right,p->final.top+px(p,top(p,7)+32)};
    if(!PtInRect(&entry,p->aim_origin))p->aim_origin=(POINT){(entry.left+entry.right)/2,(entry.top+entry.bottom)/2};
    p->child=child;position(child);appearance(child);reveal(child);SetFocus(p->hwnd);
}
static LONGLONG triangle_side(POINT a,POINT b,POINT c) {
    return (LONGLONG)(b.x-a.x)*(c.y-a.y)-(LONGLONG)(b.y-a.y)*(c.x-a.x);
}
static int moving_to_submenu(Popup *p,POINT cursor) {
    if(!p->child)return 0;
    RECT child=p->child->final;
    LONG edge=child.right<=p->final.left?child.right:child.left;
    POINT upper={edge,child.top-px(p,12)},lower={edge,child.bottom+px(p,12)};
    LONGLONG a=triangle_side(p->aim_origin,upper,cursor),b=triangle_side(upper,lower,cursor),c=triangle_side(lower,p->aim_origin,cursor);
    return (a>=0 && b>=0 && c>=0)||(a<=0 && b<=0 && c<=0);
}
static void close_advanced(Popup *root) {
    KillTimer(root->hwnd,3);
    if(root->child)DestroyWindow(root->child->hwnd);
}
static void choose(Popup *p,int row) {
    if(row<0 || !enabled(p,row))return;
    UINT c=command_at(p,row);
    if(c==300) {
        if(p->parent){HWND parent=p->parent->hwnd;DestroyWindow(p->hwnd);SetFocus(parent);}
        else open_advanced(p);
    } else {Popup *root=p->parent?p->parent:p;root->command=c;DestroyWindow(root->hwnd);}
}
static int hit(Popup *p,int x,int y) {
    if(x<px(p,8) || x>=px(p,192)) return -1;
    for(int i=0;i<count(p);i++) if(y>=px(p,top(p,i)) && y<px(p,top(p,i)+32) && enabled(p,i)) return i;
    return -1;
}
static void text(Popup *p,HDC dc,const wchar_t *s,RECT r,COLORREF color,HFONT font) {
    HGDIOBJ old=SelectObject(dc,font);
    UINT flags=DT_SINGLELINE|DT_VCENTER|DT_NOPREFIX;
    if(font==p->icon_font)flags|=DT_CENTER;
    if(p->glass && p->theme) {
        DTTOPTS options={0}; options.dwSize=sizeof(options); options.dwFlags=DTT_COMPOSITED|DTT_TEXTCOLOR; options.crText=color;
        DrawThemeTextEx(p->theme,dc,0,0,s,-1,flags,&r,&options);
    } else { SetBkMode(dc,TRANSPARENT); SetTextColor(dc,color); DrawTextW(dc,s,-1,&r,flags); }
    SelectObject(dc,old);
}
static void paint(Popup *p,HDC supplied) {
    PAINTSTRUCT ps; HDC target=supplied?supplied:BeginPaint(p->hwnd,&ps),dc=target; RECT all; GetClientRect(p->hwnd,&all);
    BP_PAINTPARAMS params={0}; params.cbSize=sizeof(params); params.dwFlags=BPPF_ERASE;
    HPAINTBUFFER buffer=BeginBufferedPaint(target,&all,BPBF_TOPDOWNDIB,&params,&dc);
    int glass=p->glass; if(!buffer) { dc=target; p->glass=0; }
    COLORREF foreground=p->dark?RGB(243,243,243):RGB(25,25,25);
    HBRUSH bg=CreateSolidBrush(p->glass?RGB(0,0,0):p->dark?RGB(22,22,22):RGB(224,224,224));
    FillRect(dc,&all,bg); DeleteObject(bg);
    if(buffer) BufferedPaintSetAlpha(buffer,&all,p->glass?0:255);
    if(buffer && p->glass) {
        RGBQUAD *bits=NULL;int stride=0;
        if(SUCCEEDED(GetBufferedPaintBits(buffer,&bits,&stride))) {
            BYTE alpha=p->dark?96:64;
            for(int y=0;y<all.bottom;y++)for(int x=0;x<all.right;x++)bits[y*stride+x]=(RGBQUAD){0,0,0,alpha};
        }
    }
    for(int i=0;i<count(p);i++) {
        RECT row={px(p,8),px(p,top(p,i)),px(p,192),px(p,top(p,i)+32)};
        if(i==p->hover) {
            /* Paint premultiplied color AND alpha over the same rounded shape. */
            RGBQUAD *bits=NULL; int stride=0;
            if(buffer && SUCCEEDED(GetBufferedPaintBits(buffer,&bits,&stride))) {
                double radius=px(p,5);
                for(int y=row.top;y<row.bottom;y++) for(int x=row.left;x<row.right;x++) {
                    int coverage=0;
                    for(int sy=0;sy<2;sy++)for(int sx=0;sx<2;sx++) {
                        double xx=x+0.25+sx*0.5,yy=y+0.25+sy*0.5;
                        double cx=xx<row.left+radius?row.left+radius:xx>row.right-radius?row.right-radius:xx;
                        double cy=yy<row.top+radius?row.top+radius:yy>row.bottom-radius?row.bottom-radius:yy;
                        if((xx-cx)*(xx-cx)+(yy-cy)*(yy-cy)<=radius*radius)coverage++;
                    }
                    if(!coverage)continue;
                    BYTE alpha=(BYTE)(255*coverage/4);
                    COLORREF highlight=p->dark?RGB(62,62,62):RGB(192,192,192);
                    RGBQUAD *v=&bits[y*stride+x];
                    v->rgbBlue=(BYTE)((GetBValue(highlight)*alpha+v->rgbBlue*(255-alpha))/255);
                    v->rgbGreen=(BYTE)((GetGValue(highlight)*alpha+v->rgbGreen*(255-alpha))/255);
                    v->rgbRed=(BYTE)((GetRValue(highlight)*alpha+v->rgbRed*(255-alpha))/255);
                    v->rgbReserved=(BYTE)(alpha+v->rgbReserved*(255-alpha)/255);
                }
            } else {
                HBRUSH brush=CreateSolidBrush(p->dark?RGB(62,62,62):RGB(192,192,192));
                HGDIOBJ old=SelectObject(dc,brush),pen=SelectObject(dc,GetStockObject(NULL_PEN));
                RoundRect(dc,row.left,row.top,row.right,row.bottom,px(p,10),px(p,10));
                SelectObject(dc,pen);SelectObject(dc,old);DeleteObject(brush);
            }
        }
        UINT c=command_at(p,i);
        int checked=(c>=100 && c<107 && (int)c-100==p->state.selected)||(c==200 && p->state.automatic)||(c==207 && p->state.startup);
        COLORREF color=enabled(p,i)?foreground:p->dark?RGB(135,135,135):RGB(145,145,145);
        RECT check=row;check.left=px(p,16);check.right=px(p,40);
        if(checked) text(p,dc,L"\xE73E",check,foreground,p->icon_font); /* CheckMark */
        if(c==300 && !p->advanced)text(p,dc,L"\xE76B",check,foreground,p->icon_font); /* ChevronLeft */
        row.left=px(p,44); text(p,dc,fsa_text(p->state.language,p->advanced?advanced_labels[i]:main_labels[i]),row,color,p->font);
        if(!p->advanced && i==5) {
            RECT line={px(p,16),px(p,top(p,i)+36),px(p,184),px(p,top(p,i)+37)};
            HBRUSH brush=CreateSolidBrush(p->dark?RGB(70,70,70):RGB(210,210,210)); FillRect(dc,&line,brush);DeleteObject(brush);
            if(buffer) BufferedPaintSetAlpha(buffer,&line,255);
        }
    }
    if(buffer) EndBufferedPaint(buffer,TRUE);
    p->glass=glass;if(!supplied)EndPaint(p->hwnd,&ps);
}
static void appearance(Popup *p) {
    DWORD light=1,size=sizeof(light);
    RegGetValueW(HKEY_CURRENT_USER,L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",L"SystemUsesLightTheme",RRF_RT_REG_DWORD,NULL,&light,&size);
    p->dark=!light; BOOL dark=p->dark; int corner=2,backdrop=3; DWORD transparent=1;size=sizeof(transparent);
    RegGetValueW(HKEY_CURRENT_USER,L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",L"EnableTransparency",RRF_RT_REG_DWORD,NULL,&transparent,&size);
    /* Establish a stable non-client appearance before the first visible frame. */
    COLORREF border=0xfffffffeu;
    DwmSetWindowAttribute(p->hwnd,34,&border,sizeof(border));
    DwmSetWindowAttribute(p->hwnd,20,&dark,sizeof(dark));
    DwmSetWindowAttribute(p->hwnd,33,&corner,sizeof(corner));
    /* Do not layer two background providers on the same surface. */
    backdrop=1;DwmSetWindowAttribute(p->hwnd,38,&backdrop,sizeof(backdrop));
    p->glass=transparent && popup_material(p->hwnd,1,p->dark);
    if(!p->glass) {
        popup_material(p->hwnd,0,p->dark);
        backdrop=transparent?3:1;
        p->glass=transparent && SUCCEEDED(DwmSetWindowAttribute(p->hwnd,38,&backdrop,sizeof(backdrop)));
    }
    MARGINS margins=p->glass?(MARGINS){-1,-1,-1,-1}:(MARGINS){0,0,0,0};
    DwmExtendFrameIntoClientArea(p->hwnd,&margins);
    if(p->theme) CloseThemeData(p->theme);
    p->theme=OpenThemeData(p->hwnd,L"WINDOW");
    if(!p->theme) p->glass=0;
    InvalidateRect(p->hwnd,NULL,FALSE);
}
static LRESULT CALLBACK procedure(HWND hwnd,UINT message,WPARAM w,LPARAM l) {
    Popup *p=(Popup*)GetWindowLongPtrW(hwnd,GWLP_USERDATA);
    if(message==WM_NCCREATE) { p=((CREATESTRUCTW*)l)->lpCreateParams;p->hwnd=hwnd;SetWindowLongPtrW(hwnd,GWLP_USERDATA,(LONG_PTR)p); }
    if(!p)return DefWindowProcW(hwnd,message,w,l);
    switch(message) {
    case WM_NCCALCSIZE:return 0;
    case WM_NCHITTEST:return HTCLIENT;
    case WM_MOUSEACTIVATE:if(p->parent)return MA_NOACTIVATE;break;
    case WM_ERASEBKGND:return 1;
    case WM_PAINT:paint(p,NULL);return 0;
    case WM_PRINTCLIENT:paint(p,(HDC)w);return 0;
    case WM_TIMER:
        if(w==1 && p->animating)animate_frame(p);
        if(w==2) {
            KillTimer(hwnd,2);
            POINT cursor;GetCursorPos(&cursor);ScreenToClient(hwnd,&cursor);
            if(!p->advanced && p->hover==7 && hit(p,cursor.x,cursor.y)==7)open_advanced(p);
        }
        if(w==3) {
            KillTimer(hwnd,3);
            if(p->child) {
                POINT cursor,local;RECT child;GetCursorPos(&cursor);local=cursor;ScreenToClient(hwnd,&local);
                GetWindowRect(p->child->hwnd,&child);
                if(hit(p,local.x,local.y)!=7 && !PtInRect(&child,cursor))close_advanced(p);
            }
        }
        return 0;
    case WM_SETTINGCHANGE:case WM_THEMECHANGED:appearance(p);return 0;
    case WM_MOUSEMOVE: {
        Popup *root=p->parent?p->parent:p;
        KillTimer(root->hwnd,3);
        int hover=hit(p,GET_X_LPARAM(l),GET_Y_LPARAM(l));
        if(!p->advanced && p->child) {
            POINT cursor={GET_X_LPARAM(l),GET_Y_LPARAM(l)};ClientToScreen(hwnd,&cursor);
            if(hover==7)p->aim_origin=cursor;
            else if(hover<0 || moving_to_submenu(p,cursor))SetTimer(hwnd,3,600,NULL);
            else close_advanced(p);
        }
        if(hover!=p->hover) {
            KillTimer(hwnd,2);p->hover=hover;InvalidateRect(hwnd,NULL,FALSE);
            if(!p->advanced && hover==7 && !p->child) {
                UINT delay=400;SystemParametersInfoW(SPI_GETMENUSHOWDELAY,0,&delay,0);
                if(delay<300)delay=300;if(delay>1000)delay=1000;
                SetTimer(hwnd,2,delay,NULL);
            }
        }
        TRACKMOUSEEVENT track={sizeof(track),TME_LEAVE,hwnd,0};TrackMouseEvent(&track);return 0;
    }
    case WM_MOUSELEAVE: {
        KillTimer(hwnd,2);p->hover=-1;InvalidateRect(hwnd,NULL,FALSE);
        Popup *root=p->parent?p->parent:p;
        if(root->child)SetTimer(root->hwnd,3,600,NULL);
        return 0;
    }
    case WM_LBUTTONUP: {
        int row=hit(p,GET_X_LPARAM(l),GET_Y_LPARAM(l));choose(p,row);return 0;
    }
    case WM_KEYDOWN:
        if(p->child)return SendMessageW(p->child->hwnd,message,w,l);
        if(w==VK_ESCAPE || (p->advanced && (w==VK_LEFT || w==VK_RIGHT))) {if(p->parent){HWND parent=p->parent->hwnd;DestroyWindow(hwnd);SetFocus(parent);}else DestroyWindow(hwnd);}
        else if((w==VK_LEFT || w==VK_RIGHT) && !p->advanced) choose(p,7);
        else if(w==VK_RETURN || w==VK_SPACE) choose(p,p->hover);
        else {
            int n=count(p),step=w==VK_UP?-1:1;
            if(w==VK_HOME)p->hover=-1;else if(w==VK_END){p->hover=0;step=-1;}
            else if(w!=VK_UP && w!=VK_DOWN && w!=VK_TAB)return 0;
            for(int i=0;i<n;i++) {p->hover=(p->hover+step+n)%n;if(enabled(p,p->hover))break;}
            InvalidateRect(hwnd,NULL,FALSE);
        }
        return 0;
    case WM_ACTIVATE:
        if(LOWORD(w)==WA_INACTIVE) {
            Popup *root=p->parent?p->parent:p;HWND next=(HWND)l;
            if(next!=root->hwnd && (!root->child || next!=root->child->hwnd))DestroyWindow(root->hwnd);
        }
        return 0;
    case WM_CLOSE:DestroyWindow(hwnd);return 0;
    case WM_DESTROY:
        KillTimer(hwnd,1);KillTimer(hwnd,2);KillTimer(hwnd,3);
        if(p->child)DestroyWindow(p->child->hwnd);
        p->done=TRUE;return 0;
    case WM_NCDESTROY:
        if(p->parent){p->parent->child=NULL;if(p->theme)CloseThemeData(p->theme);SetWindowLongPtrW(hwnd,GWLP_USERDATA,0);free(p);}
        return DefWindowProcW(hwnd,message,w,l);
    }
    return DefWindowProcW(hwnd,message,w,l);
}
UINT fsa_popup(HWND owner,const FSA_MenuState *state) {
    HIGHCONTRASTW contrast={sizeof(contrast),0,NULL}; BOOL reader=FALSE;
    SystemParametersInfoW(SPI_GETHIGHCONTRAST,sizeof(contrast),&contrast,0);
    SystemParametersInfoW(SPI_GETSCREENREADER,0,&reader,0);
    if((contrast.dwFlags&HCF_HIGHCONTRASTON) || reader) {
        HMENU menu=CreatePopupMenu(),advanced=CreatePopupMenu();
        for(int i=0;i<7;i++)AppendMenuW(advanced,MF_STRING|(i==4 && state->startup?MF_CHECKED:0),advanced_commands[i],fsa_text(state->language,advanced_labels[i]));
        for(int i=0;i<9;i++) {
            if(i==6)AppendMenuW(menu,MF_SEPARATOR,0,NULL);
            if(i==7){AppendMenuW(menu,MF_POPUP,(UINT_PTR)advanced,fsa_text(state->language,TXT_ADVANCED));continue;}
            UINT c=main_commands[i];
            int checked=(c>=100 && c<107 && (int)c-100==state->selected)||(c==200 && state->automatic);
            int disabled=(c>=100 && c<107 && !(state->available&(1u<<(c-100)))) || (state->nsp_locked && ((c>=100 && c<107)||c==200));
            AppendMenuW(menu,MF_STRING|(checked?MF_CHECKED:0)|(disabled?MF_GRAYED:0),main_commands[i],fsa_text(state->language,main_labels[i]));
        }
        POINT pt;GetCursorPos(&pt);MONITORINFO mi={.cbSize=sizeof(mi)};GetMonitorInfoW(MonitorFromPoint(pt,MONITOR_DEFAULTTONEAREST),&mi);
        SetForegroundWindow(owner);
        UINT command=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_RIGHTBUTTON|TPM_BOTTOMALIGN|TPM_RIGHTALIGN,pt.x,mi.rcWork.bottom-6,0,owner,NULL);DestroyMenu(menu);return command;
    }
    HINSTANCE instance=(HINSTANCE)GetWindowLongPtrW(owner,GWLP_HINSTANCE);
    WNDCLASSW wc={0};wc.hInstance=instance;wc.lpfnWndProc=procedure;wc.lpszClassName=L"FSAFluentPopup";wc.hCursor=LoadCursorW(NULL,MAKEINTRESOURCEW(32512));
    RegisterClassW(&wc);
    Popup p={0};p.hover=-1;p.state=*state;p.dpi=GetDpiForWindow(owner);if(!p.dpi)p.dpi=96;
    p.font=CreateFontW(-px(&p,14),0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Segoe UI");
    POINT pt;GetCursorPos(&pt);MONITORINFO monitor={0};monitor.cbSize=sizeof(monitor);GetMonitorInfoW(MonitorFromPoint(pt,MONITOR_DEFAULTTONEAREST),&monitor);
    Taskbar bar={0};bar.monitor=MonitorFromPoint(pt,MONITOR_DEFAULTTONEAREST);EnumWindows(find_taskbar,(LPARAM)&bar);
    if(bar.hwnd) {UINT dpi=GetDpiForWindow(bar.hwnd);if(dpi)p.dpi=(int)dpi;}
    DeleteObject(p.font);p.font=CreateFontW(-px(&p,14),0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Segoe UI");
    p.icon_font=CreateFontW(-px(&p,14),0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,ANTIALIASED_QUALITY,0,L"Segoe Fluent Icons");
    if(!p.font || !p.icon_font){if(p.font)DeleteObject(p.font);if(p.icon_font)DeleteObject(p.icon_font);return 0;}
    p.work=monitor.rcWork;p.anchor_x=pt.x;p.anchor_bottom=monitor.rcWork.bottom-px(&p,6);
    if(bar.hwnd && bar.rect.right-bar.rect.left>bar.rect.bottom-bar.rect.top && bar.rect.top>monitor.rcMonitor.top)
        p.anchor_bottom=bar.rect.top-px(&p,6);
    BufferedPaintInit();
    HWND hwnd=CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_TOPMOST,wc.lpszClassName,L"Final Spatial Audio",WS_POPUP,0,0,0,0,owner,NULL,instance,&p);
    if(hwnd) {
        position(&p);appearance(&p);reveal(&p);
        SetForegroundWindow(hwnd);SetFocus(hwnd);
        MSG msg;while(!p.done) {int result=GetMessageW(&msg,NULL,0,0);if(result<=0){if(!result)PostQuitMessage((int)msg.wParam);break;}TranslateMessage(&msg);DispatchMessageW(&msg);}
        if(IsWindow(hwnd))DestroyWindow(hwnd);
    }
    if(p.theme)CloseThemeData(p.theme);DeleteObject(p.font);DeleteObject(p.icon_font);BufferedPaintUnInit();
    PostMessageW(owner,WM_NULL,0,0);return p.command;
}
