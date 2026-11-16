#ifndef FSA_FLUENT_MATERIAL_H
#define FSA_FLUENT_MATERIAL_H

#include <windows.h>
#include <dwmapi.h>
#include <uxtheme.h>

/* The optional user32 composition ABI keeps the acrylic on inactive popups.
   Use the system backdrop as the fallback when the ABI is unavailable. */
typedef struct { int state; DWORD flags,color,animation; } FSA_Accent;
typedef struct { int attribute; void *data; SIZE_T size; } FSA_CompositionData;
typedef BOOL (WINAPI *FSA_SetComposition)(HWND,const FSA_CompositionData*);

static inline BOOL fsa_fluent_accent(HWND hwnd,BOOL enabled,COLORREF tint,BYTE opacity) {
    FSA_SetComposition set=(FSA_SetComposition)(void*)GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetWindowCompositionAttribute");
    if(!set)return FALSE;
    DWORD color=((DWORD)opacity<<24)|tint;
    FSA_Accent accent={enabled?4:0,0,color,0};
    FSA_CompositionData data={19,&accent,sizeof(accent)};
    return set(hwnd,&data);
}

static inline BOOL fsa_fluent_configure(HWND hwnd,BOOL *dark) {
    const wchar_t *personalize=L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize";
    DWORD light=1,size=sizeof(light),transparent=1;
    RegGetValueW(HKEY_CURRENT_USER,personalize,L"SystemUsesLightTheme",RRF_RT_REG_DWORD,NULL,&light,&size);
    size=sizeof(transparent);
    RegGetValueW(HKEY_CURRENT_USER,personalize,L"EnableTransparency",RRF_RT_REG_DWORD,NULL,&transparent,&size);
    *dark=!light;
    int corner=2,backdrop=1;
    COLORREF border=0xfffffffeu;
    DwmSetWindowAttribute(hwnd,34,&border,sizeof(border));
    DwmSetWindowAttribute(hwnd,20,dark,sizeof(*dark));
    DwmSetWindowAttribute(hwnd,33,&corner,sizeof(corner));
    DwmSetWindowAttribute(hwnd,38,&backdrop,sizeof(backdrop));
    return transparent!=0;
}

static inline BOOL fsa_fluent_appearance(HWND hwnd,BOOL *dark,BYTE tint_opacity,COLORREF dark_tint,COLORREF light_tint) {
    BOOL transparent=fsa_fluent_configure(hwnd,dark);
    BOOL glass=transparent && fsa_fluent_accent(hwnd,TRUE,*dark?dark_tint:light_tint,tint_opacity);
    if(!glass) {
        fsa_fluent_accent(hwnd,FALSE,0,0);
        int backdrop=transparent?3:1;
        glass=transparent && SUCCEEDED(DwmSetWindowAttribute(hwnd,38,&backdrop,sizeof(backdrop)));
    }
    int extend=glass?-1:0;
    MARGINS margins={extend,extend,extend,extend};
    DwmExtendFrameIntoClientArea(hwnd,&margins);
    return glass;
}

static inline void fsa_fluent_background(HPAINTBUFFER buffer,HDC dc,const RECT *bounds,BOOL glass,BOOL dark,BYTE scrim_opacity) {
    HBRUSH bg=CreateSolidBrush(glass?RGB(0,0,0):dark?RGB(22,22,22):RGB(224,224,224));
    FillRect(dc,bounds,bg);
    DeleteObject(bg);
    if(buffer)BufferedPaintSetAlpha(buffer,bounds,glass?0:255);
    if(buffer && glass) {
        RGBQUAD *bits=NULL;
        int stride=0;
        if(SUCCEEDED(GetBufferedPaintBits(buffer,&bits,&stride))) {
            RGBQUAD tint={0,0,0,scrim_opacity};
            for(int y=0;y<bounds->bottom;y++)for(int x=0;x<bounds->right;x++)bits[y*stride+x]=tint;
        }
    }
}

#endif
