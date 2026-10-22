#include "fluent_menu.h"
#include <windowsx.h>
#include <dwmapi.h>
#include <uxtheme.h>
#include <stdio.h>
#include <stdlib.h>
static const UINT main_commands[]={100,101,102,105,107,108,103,109,110,104,106,200,300,205};
static const wchar_t *spatial_labels[]={L"Windows Sonic",L"Dolby Atmos (Headphones)",L"Dolby Atmos (Speakers)",L"Dolby Atmos (Home Theater)",L"DTS Headphone:X",L"DTS:X Ultra",L"DTS:X (Home Theater)"};
static const UINT spatial_commands[]={105,107,108,103,109,110,104};
static const wchar_t *extreme_labels[]={L"Expand Online",L"Enum OLR",L"Enum OBR",L"Rec Hook"};
static const UINT extreme_commands[]={410,411,412,413};
enum { MAIN_NSP_ROW=10, MAIN_ADVANCED_ROW=12, ADVANCED_EXTREME_ROW=8 };
static const FSA_Text advanced_labels[]={TXT_CAPTURE,TXT_EDIT,TXT_RELOAD,TXT_LOG,TXT_STARTUP,TXT_NSP_STOP,TXT_NSP_EMERGENCY};
static const UINT advanced_commands[]={206,202,203,204,207,208,209,311,310};
static const wchar_t *advanced_label(FSA_Language language,int i){return i==ADVANCED_EXTREME_ROW?L"NS Extreme":i==7?L"NSp Lock":fsa_text(language,advanced_labels[i]);}
static const wchar_t *main_label(const FSA_MenuState *state,int i){
    FSA_Language language=state->language;
    UINT c=main_commands[i];for(int j=0;j<7;j++)if(c==spatial_commands[j])return spatial_labels[j];
    if(c==106 && (state->extreme&FSA_MENU_ROUTE_LOCK))return fsa_locked_nsp_label(state->frontend);
    return c==310?L"NS Extreme":fsa_text(language,c==100?TXT_STEREO:c==101?TXT_51:c==102?TXT_71:c==106?TXT_NSP:c==200?TXT_AUTO:c==300?TXT_ADVANCED:TXT_EXIT);
}
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
    HWND hwnd,owner; HFONT font,icon_font; HTHEME theme;
    int hover,dpi,dark,glass,advanced,width,stride,child_row;
    FSA_MenuState state;
    int anchor_x,anchor_bottom; RECT work;
    UINT command; BOOL done;
    unsigned hidden,removing;ULONGLONG removal_start;
} Popup;
static Popup *active_popup;
static int px(Popup *p,int value) { return MulDiv(value,p->dpi,96); }
static Popup *root_of(Popup *p){while(p->parent)p=p->parent;return p;}
static int extra_visible(const FSA_MenuState *s,UINT c){
    if((s->extreme&FSA_MENU_ROUTE_LOCK) && c>=103 && c<=110 && c!=106)return 0;
    unsigned group=(c==107 || c==109)?2u:(c==108 || c==110)?4u:0u;
    return !group || ((s->extreme&group) && (s->available&(1u<<(c-100))));
}
static unsigned hidden_rows(const FSA_MenuState *s){unsigned mask=0;for(unsigned i=0;i<sizeof(main_commands)/sizeof(*main_commands);i++)if(!extra_visible(s,main_commands[i]))mask|=1u<<i;return mask;}
static double removal_progress(Popup *p){double t=(GetTickCount64()-p->removal_start)/160.0;if(t>1)t=1;double r=1-t;return 1-r*r*r;}
static int count(Popup *p) { return p->advanced==2?4:p->advanced?9:(int)(sizeof(main_commands)/sizeof(*main_commands)); }
static int top(Popup *p,int i) {
    double y=8,progress=p->removing?removal_progress(p):0;
    for(int j=0;j<i;j++)if(!(p->hidden&(1u<<j)))y+=p->stride*((p->removing&(1u<<j))?1-progress:1);
    return (int)(y+.5)+(!p->advanced && i>=MAIN_NSP_ROW?9:0);
}
static UINT command_at(Popup *p,int i) { return p->advanced==2?extreme_commands[i]:p->advanced?advanced_commands[i]:main_commands[i]; }
static const wchar_t *label_at(Popup *p,int i){return p->advanced==2?extreme_labels[i]:p->advanced?advanced_label(p->state.language,i):main_label(&p->state,i);}
static int submenu_row(Popup *p,int i){return i>=0 && i<count(p) && ((!p->advanced && i==MAIN_ADVANCED_ROW) || (p->advanced==1 && i==ADVANCED_EXTREME_ROW));}
static int enabled(Popup *p,int i) { if(i<0 || i>=count(p) || ((p->hidden|p->removing)&(1u<<i)))return 0;UINT c=command_at(p,i);if(c==311 && (p->state.extreme&FSA_MENU_LOCK_PENDING))return 0;if((p->state.extreme&FSA_MENU_ROUTE_LOCK) && c>=410 && c<=412)return 0;if(p->state.nsp_locked && ((c==106 && p->state.selected!=6) || (c==200 && !(p->state.extreme&FSA_MENU_ROUTE_LOCK)) || c==206 || (c>=410 && c<=412)))return 0;return c<100 || c>=111 || (p->state.available&(1u<<(c-100))); }
static void measure(Popup *p){
    p->width=200;p->stride=34;
    HDC dc=GetDC(p->hwnd);HGDIOBJ old=SelectObject(dc,p->font);
    for(int i=0;i<count(p);i++){SIZE s={0};const wchar_t *label=label_at(p,i);GetTextExtentPoint32W(dc,label,(int)wcslen(label),&s);int width=MulDiv(s.cx,96,p->dpi)+64;if(width>p->width)p->width=width;}
    SelectObject(dc,old);ReleaseDC(p->hwnd,dc);
    int room=MulDiv(p->work.bottom-p->work.top,96,p->dpi)-32;
    if(8+count(p)*34+9>room){p->stride=(room-17)/count(p);if(p->stride<20)p->stride=20;}
}
static void position(Popup *p) {
    int height=px(p,top(p,count(p)-1)+p->stride+6),width=px(p,p->width);
    int x=p->anchor_x-width,y=p->anchor_bottom-height;
    if(x+width>p->work.right)x=p->work.right-width;
    if(x<p->work.left)x=p->work.left;
    if(y<p->work.top)y=p->work.top;
    p->final=(RECT){x,y,x+width,y+height};
    SetWindowPos(p->hwnd,NULL,x,y,width,height,SWP_NOZORDER|SWP_NOACTIVATE);
}
void fsa_popup_update(const FSA_MenuState *state){
    Popup *root=active_popup;if(!root || root->done)return;
    for(Popup *p=root;p;p=p->child){p->state=*state;InvalidateRect(p->hwnd,NULL,FALSE);}
    unsigned desired=hidden_rows(state);
    if(desired==(root->hidden|root->removing))return;
    if(root->animating){KillTimer(root->hwnd,1);root->animating=0;}
    root->hidden|=root->removing;root->removing=desired&~root->hidden;root->hidden&=desired;root->hover=-1;
    BOOL animation=TRUE,client=TRUE;SystemParametersInfoW(SPI_GETMENUANIMATION,0,&animation,0);SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION,0,&client,0);
    if(root->removing && animation && client){root->removal_start=GetTickCount64();SetTimer(root->hwnd,4,16,NULL);}
    else{root->hidden=desired;root->removing=0;KillTimer(root->hwnd,4);position(root);}
    InvalidateRect(root->hwnd,NULL,FALSE);
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
static void open_submenu(Popup *p,int row) {
    if(!submenu_row(p,row))return;
    if(p->child && p->child_row==row)return;
    if(p->child)DestroyWindow(p->child->hwnd);
    Popup *child=calloc(1,sizeof(*child));if(!child)return;
    child->parent=p;child->owner=p->owner;child->advanced=p->advanced==1?2:1;child->hover=-1;child->dpi=p->dpi;child->font=p->font;child->icon_font=p->icon_font;child->state=p->state;child->work=p->work;p->child_row=row;
    child->anchor_x=p->final.left-px(p,4);
    child->anchor_bottom=p->final.top+px(p,top(p,row)+p->stride-2);
    /* Only flip to the other side when there is insufficient space on the left. */
    HINSTANCE instance=(HINSTANCE)GetWindowLongPtrW(p->hwnd,GWLP_HINSTANCE);
    HWND hwnd=CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_TOPMOST|WS_EX_NOACTIVATE,L"FSAFluentPopup",child->advanced==2?L"NS Extreme":L"Final Spatial Audio Advanced",WS_POPUP,0,0,0,0,p->hwnd,NULL,instance,child);
    if(!hwnd){free(child);return;}
    GetCursorPos(&p->aim_origin);
    measure(child);
    if(child->anchor_x-px(child,child->width)<p->work.left)child->anchor_x=p->final.right+px(child,child->width+4);
    RECT entry={p->final.left,p->final.top+px(p,top(p,row)),p->final.right,p->final.top+px(p,top(p,row)+p->stride-2)};
    if(!PtInRect(&entry,p->aim_origin))p->aim_origin=(POINT){(entry.left+entry.right)/2,(entry.top+entry.bottom)/2};
    p->child=child;position(child);appearance(child);reveal(child);SetFocus(root_of(p)->hwnd);
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
static int point_in_tree(Popup *p,POINT cursor){for(;p;p=p->child){RECT r;GetWindowRect(p->hwnd,&r);if(PtInRect(&r,cursor))return 1;}return 0;}
static int window_in_tree(Popup *p,HWND hwnd){for(;p;p=p->child)if(p->hwnd==hwnd)return 1;return 0;}
static void close_advanced(Popup *root) {
    KillTimer(root->hwnd,3);
    if(root->child)DestroyWindow(root->child->hwnd);
}
static void choose(Popup *p,int row) {
    if(row<0 || !enabled(p,row))return;
    UINT c=command_at(p,row);
    if(c==300 || c==310)open_submenu(p,row);
    else if((c==311 || (c>=410 && c<=413)) && p->owner)SendMessageW(p->owner,FSA_WM_MENU_ACTION,c,0);
    else {Popup *root=root_of(p);root->command=c;DestroyWindow(root->hwnd);}
}
static int hit(Popup *p,int x,int y) {
    if(x<px(p,8) || x>=px(p,p->width-8)) return -1;
    for(int i=0;i<count(p);i++) if(y>=px(p,top(p,i)) && y<px(p,top(p,i)+p->stride-2) && enabled(p,i)) return i;
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
        if(p->hidden&(1u<<i))continue;
        int saved_dc=SaveDC(dc);double progress=(p->removing&(1u<<i))?removal_progress(p):0;
        RECT row={px(p,8),px(p,top(p,i)),px(p,p->width-8),px(p,top(p,i)+p->stride-2)};
        if(progress){
            int band=(int)(px(p,p->stride)*(1-progress));
            IntersectClipRect(dc,0,row.top,all.right,row.top+band);
            OffsetRect(&row,(int)(px(p,24)*progress),0);
        }
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
        int checked=fsa_menu_checked(&p->state,c);
        COLORREF color=enabled(p,i)?foreground:p->dark?RGB(135,135,135):RGB(145,145,145);
        if(progress){COLORREF base=p->dark?RGB(22,22,22):RGB(224,224,224);color=RGB((int)(GetRValue(color)*(1-progress)+GetRValue(base)*progress),(int)(GetGValue(color)*(1-progress)+GetGValue(base)*progress),(int)(GetBValue(color)*(1-progress)+GetBValue(base)*progress));}
        RECT check=row;check.left=row.left+px(p,8);check.right=row.left+px(p,32);
        if(checked) text(p,dc,L"\xE73E",check,foreground,p->icon_font); /* CheckMark */
        if(submenu_row(p,i))text(p,dc,L"\xE76B",check,foreground,p->icon_font); /* ChevronLeft at every depth */
        row.left+=px(p,36); text(p,dc,label_at(p,i),row,color,p->font);
        if(!p->advanced && i==MAIN_NSP_ROW-1) {
            RECT line={px(p,16),px(p,top(p,i)+p->stride+2),px(p,p->width-16),px(p,top(p,i)+p->stride+3)};
            HBRUSH brush=CreateSolidBrush(p->dark?RGB(70,70,70):RGB(210,210,210)); FillRect(dc,&line,brush);DeleteObject(brush);
            if(buffer) BufferedPaintSetAlpha(buffer,&line,255);
        }
        RestoreDC(dc,saved_dc);
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
        if(w==4){
            if(removal_progress(p)>=1){p->hidden|=p->removing;p->removing=0;KillTimer(hwnd,4);}
            position(p);InvalidateRect(hwnd,NULL,FALSE);
        }
        if(w==2) {
            KillTimer(hwnd,2);
            POINT cursor;GetCursorPos(&cursor);ScreenToClient(hwnd,&cursor);
            if(submenu_row(p,p->hover) && hit(p,cursor.x,cursor.y)==p->hover)open_submenu(p,p->hover);
        }
        if(w==3) {
            KillTimer(hwnd,3);
            if(p->child) {
                POINT cursor,local;GetCursorPos(&cursor);local=cursor;ScreenToClient(hwnd,&local);
                if(hit(p,local.x,local.y)!=p->child_row && !point_in_tree(p->child,cursor))close_advanced(p);
            }
        }
        return 0;
    case WM_SETTINGCHANGE:case WM_THEMECHANGED:appearance(p);return 0;
    case WM_MOUSEMOVE: {
        for(Popup *ancestor=p;ancestor;ancestor=ancestor->parent)KillTimer(ancestor->hwnd,3);
        int hover=hit(p,GET_X_LPARAM(l),GET_Y_LPARAM(l));
        if(p->child) {
            POINT cursor={GET_X_LPARAM(l),GET_Y_LPARAM(l)};ClientToScreen(hwnd,&cursor);
            if(hover==p->child_row)p->aim_origin=cursor;
            else if(hover<0 || moving_to_submenu(p,cursor))SetTimer(hwnd,3,600,NULL);
            else close_advanced(p);
        }
        if(hover!=p->hover) {
            KillTimer(hwnd,2);p->hover=hover;InvalidateRect(hwnd,NULL,FALSE);
            if(submenu_row(p,hover) && !p->child) {
                UINT delay=400;SystemParametersInfoW(SPI_GETMENUSHOWDELAY,0,&delay,0);
                if(delay<300)delay=300;if(delay>1000)delay=1000;
                SetTimer(hwnd,2,delay,NULL);
            }
        }
        TRACKMOUSEEVENT track={sizeof(track),TME_LEAVE,hwnd,0};TrackMouseEvent(&track);return 0;
    }
    case WM_MOUSELEAVE: {
        KillTimer(hwnd,2);p->hover=-1;InvalidateRect(hwnd,NULL,FALSE);
        if(p->child)SetTimer(p->hwnd,3,600,NULL);
        if(p->parent)SetTimer(p->parent->hwnd,3,600,NULL);
        return 0;
    }
    case WM_LBUTTONUP: {
        int row=hit(p,GET_X_LPARAM(l),GET_Y_LPARAM(l));choose(p,row);return 0;
    }
    case WM_KEYDOWN:
        if(p->child)return SendMessageW(p->child->hwnd,message,w,l);
        if(w==VK_ESCAPE) {if(p->parent){HWND root=root_of(p)->hwnd;DestroyWindow(hwnd);SetFocus(root);}else DestroyWindow(hwnd);}
        else if(w==VK_LEFT || w==VK_RIGHT){
            if(submenu_row(p,p->hover))choose(p,p->hover);
            else if(p->parent){HWND root=root_of(p)->hwnd;DestroyWindow(hwnd);SetFocus(root);}
            else choose(p,MAIN_ADVANCED_ROW);
        }
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
            Popup *root=root_of(p);HWND next=(HWND)l;
            if(!window_in_tree(root,next))DestroyWindow(root->hwnd);
        }
        return 0;
    case WM_CLOSE:DestroyWindow(hwnd);return 0;
    case WM_DESTROY:
        KillTimer(hwnd,1);KillTimer(hwnd,2);KillTimer(hwnd,3);KillTimer(hwnd,4);
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
        HMENU menu=CreatePopupMenu(),advanced=CreatePopupMenu(),extreme=CreatePopupMenu();
        Popup access={0};access.state=*state;
        for(int i=0;i<4;i++)AppendMenuW(extreme,MF_STRING|(fsa_menu_checked(state,extreme_commands[i])?MF_CHECKED:0)|(((state->nsp_locked || (state->extreme&FSA_MENU_ROUTE_LOCK)) && i<3)?MF_GRAYED:0),extreme_commands[i],extreme_labels[i]);
        for(int i=0;i<9;i++){
            if(i==ADVANCED_EXTREME_ROW)AppendMenuW(advanced,MF_POPUP,(UINT_PTR)extreme,L"NS Extreme");
            else AppendMenuW(advanced,MF_STRING|(fsa_menu_checked(state,advanced_commands[i])?MF_CHECKED:0)|(((state->nsp_locked && i==0) || (i==7 && (state->extreme&FSA_MENU_LOCK_PENDING)))?MF_GRAYED:0),advanced_commands[i],advanced_label(state->language,i));
        }
        for(unsigned i=0;i<sizeof(main_commands)/sizeof(*main_commands);i++) {
            if(i==MAIN_NSP_ROW)AppendMenuW(menu,MF_SEPARATOR,0,NULL);
            if(i==MAIN_ADVANCED_ROW){AppendMenuW(menu,MF_POPUP,(UINT_PTR)advanced,fsa_text(state->language,TXT_ADVANCED));continue;}
            UINT c=main_commands[i];
            if(!extra_visible(state,c))continue;
            int checked=fsa_menu_checked(state,c);
            int disabled=!enabled(&access,(int)i);
            AppendMenuW(menu,MF_STRING|(checked?MF_CHECKED:0)|(disabled?MF_GRAYED:0),main_commands[i],main_label(state,(int)i));
        }
        POINT pt;GetCursorPos(&pt);MONITORINFO mi={.cbSize=sizeof(mi)};GetMonitorInfoW(MonitorFromPoint(pt,MONITOR_DEFAULTTONEAREST),&mi);
        SetForegroundWindow(owner);
        UINT command=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_RIGHTBUTTON|TPM_BOTTOMALIGN|TPM_RIGHTALIGN,pt.x,mi.rcWork.bottom-6,0,owner,NULL);DestroyMenu(menu);return command;
    }
    HINSTANCE instance=(HINSTANCE)GetWindowLongPtrW(owner,GWLP_HINSTANCE);
    WNDCLASSW wc={0};wc.hInstance=instance;wc.lpfnWndProc=procedure;wc.lpszClassName=L"FSAFluentPopup";wc.hCursor=LoadCursorW(NULL,MAKEINTRESOURCEW(32512));
    RegisterClassW(&wc);
    Popup p={0};p.owner=owner;p.hover=-1;p.state=*state;p.hidden=hidden_rows(state);p.dpi=GetDpiForWindow(owner);if(!p.dpi)p.dpi=96;
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
        active_popup=&p;
        measure(&p);position(&p);appearance(&p);reveal(&p);
        SetForegroundWindow(hwnd);SetFocus(hwnd);
        MSG msg;while(!p.done) {int result=GetMessageW(&msg,NULL,0,0);if(result<=0){if(!result)PostQuitMessage((int)msg.wParam);break;}TranslateMessage(&msg);DispatchMessageW(&msg);}
        if(IsWindow(hwnd))DestroyWindow(hwnd);
        active_popup=NULL;
    }
    if(p.theme)CloseThemeData(p.theme);DeleteObject(p.font);DeleteObject(p.icon_font);BufferedPaintUnInit();
    PostMessageW(owner,WM_NULL,0,0);return p.command;
}
