#define INITGUID
#include <windows.h>
#include <shellapi.h>
static HICON last_tray_icon;
static BOOL WINAPI record_tray(DWORD message,PNOTIFYICONDATAW data) {
    (void)message;last_tray_icon=data->hIcon;return TRUE;
}
#define Shell_NotifyIconW record_tray
#include "final_spatial_audio.c"
#include <assert.h>
static unsigned long long icon_hash(HICON icon,int size) {
    ICONINFO info={0};assert(GetIconInfo(icon,&info));
    BITMAPINFO bi={0};bi.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth=size;bi.bmiHeader.biHeight=-size;
    bi.bmiHeader.biPlanes=1;bi.bmiHeader.biBitCount=32;
    BYTE *bits=calloc((size_t)size*size,4);assert(bits);
    HDC dc=CreateCompatibleDC(NULL);assert(GetDIBits(dc,info.hbmColor,0,size,bits,&bi,DIB_RGB_COLORS));
    unsigned long long hash=1469598103934665603ULL;int visible=0;
    for(int i=0;i<size*size;i++){
        visible+=bits[i*4+3]!=0;
        for(int c=0;c<3;c++)assert(bits[i*4+c]<=bits[i*4+3]);
        for(int c=0;c<4;c++){hash^=bits[i*4+c];hash*=1099511628211ULL;}
    }
    assert(visible>0);assert(bits[3]==0 && bits[((size-1)*size+size-1)*4+3]==0);
    free(bits);DeleteDC(dc);DeleteObject(info.hbmColor);DeleteObject(info.hbmMask);
    return hash;
}
int wmain(void) {
    HINSTANCE instance=GetModuleHandleW(NULL);
    const int sizes[]={16,20,24,32,40,48,64};
    DWORD before=0,user_before=0;
    for(int pass=0;pass<2;pass++){
    for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++){
        int size=sizes[i];HICON frames[FSA_SPIN_FRAMES]={0};
        HICON source=(HICON)LoadImageW(instance,MAKEINTRESOURCEW(IDI_FSA_STEREO),IMAGE_ICON,size,size,0);assert(source);
        assert(make_spinner(source,size,size,frames));
        unsigned long long first=icon_hash(frames[0],size),rotated=icon_hash(frames[4],size);assert(first!=rotated);
        for(int f=0;f<FSA_SPIN_FRAMES;f++)icon_hash(frames[f],size);
        free_spinner(frames);DestroyIcon(source);
    }
    DWORD after=GetGuiResources(GetCurrentProcess(),GR_GDIOBJECTS);
    DWORD user_after=GetGuiResources(GetCurrentProcess(),GR_USEROBJECTS);
    if(pass){assert(after==before);assert(user_after==user_before);}
    before=after;user_before=user_after;
    }
    assert(load_mode_icons(instance));
    WNDCLASSW wc={0};wc.lpfnWndProc=wndproc;wc.hInstance=instance;wc.lpszClassName=L"FSATrayAnimationTest";
    assert(RegisterClassW(&wc));window=CreateWindowW(wc.lpszClassName,L"Test",0,0,0,0,0,NULL,NULL,instance,NULL);assert(window);
    stop=CreateEventW(NULL,TRUE,FALSE,NULL);assert(stop);
    current=-1;nsp_starting=1;update_tray();assert(spin_timer && last_tray_icon==starting_icons[0]);
    spin_started-=FSA_SPIN_INTERVAL_MS*4;wndproc(window,WM_TIMER,TIMER_NSP_SPIN,0);
    assert(IsWindow(window) && last_tray_icon!=starting_icons[0]);
    nsp_starting=0;current=MODE_NSP;update_tray();assert(!spin_timer && last_tray_icon==mode_icons[MODE_NSP]);
    nsp_starting=1;current=-1;update_tray();assert(spin_timer);
    nsp_starting=0;update_tray();assert(spin_timer); /* Unknown remains animated after initialization fails. */
    spin_started-=FSA_SPIN_INTERVAL_MS*4;wndproc(window,WM_TIMER,TIMER_NSP_SPIN,0);
    assert(last_tray_icon==starting_icons[(GetTickCount64()-spin_started)/FSA_SPIN_INTERVAL_MS%FSA_SPIN_FRAMES]);
    current=MODE_COUNT;update_tray();assert(spin_timer); /* Invalid modes are unknown too. */
    for(int mode=0;mode<MODE_COUNT;mode++){
        current=mode;update_tray();assert(!spin_timer && last_tray_icon==mode_icons[mode]);
    }
    DestroyWindow(window);CloseHandle(stop);release_icons();
    puts("PASS 168 rotating stereo frames: alpha, rotation, DPI, GDI cleanup; unknown and initializing states animate; known modes stop timer");
    return 0;
}
