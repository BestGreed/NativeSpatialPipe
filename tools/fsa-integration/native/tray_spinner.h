#ifndef FSA_TRAY_SPINNER_H
#define FSA_TRAY_SPINNER_H
#include <windows.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#define FSA_SPIN_FRAMES 24
#define FSA_SPIN_INTERVAL_MS 60
static void free_spinner(HICON *frames) {
    for(int i=0;i<FSA_SPIN_FRAMES;i++){if(frames[i])DestroyIcon(frames[i]);frames[i]=NULL;}
}
/* Build once per DPI change. Frames retain premultiplied alpha; the UI timer
   only selects a cached icon, with no rendering or allocation on each tick. */
static int make_spinner(HICON source,int width,int height,HICON *frames) {
    BITMAPINFO bi={0};bi.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth=width;bi.bmiHeader.biHeight=-height;
    bi.bmiHeader.biPlanes=1;bi.bmiHeader.biBitCount=32;bi.bmiHeader.biCompression=BI_RGB;
    BYTE *pixels=NULL;HDC dc=CreateCompatibleDC(NULL);
    HBITMAP bitmap=CreateDIBSection(dc,&bi,DIB_RGB_COLORS,(void**)&pixels,NULL,0);
    if(!dc || !bitmap){if(bitmap)DeleteObject(bitmap);if(dc)DeleteDC(dc);return 0;}
    HGDIOBJ old=SelectObject(dc,bitmap);memset(pixels,0,(size_t)width*height*4);
    int ok=DrawIconEx(dc,0,0,source,width,height,0,NULL,DI_NORMAL)!=FALSE;
    GdiFlush();
    double cx=(width-1)*.5,cy=(height-1)*.5,radius=0;
    for(int y=0;y<height;y++)for(int x=0;x<width;x++)if(pixels[(y*width+x)*4+3]){
        double dx=x-cx,dy=y-cy,r=sqrt(dx*dx+dy*dy);if(r>radius)radius=r;
    }
    double edge=(width<height?width:height)*.5-.5;
    double scale=radius>edge?edge/radius:1.;
    if(radius==0 || scale<=0)ok=0;
    for(int frame=0;ok && frame<FSA_SPIN_FRAMES;frame++){
        BYTE *dest=NULL;HBITMAP color=CreateDIBSection(dc,&bi,DIB_RGB_COLORS,(void**)&dest,NULL,0);
        BYTE *mask_bits=calloc((size_t)((width+15)/16)*2,height);
        HBITMAP mask=mask_bits?CreateBitmap(width,height,1,1,mask_bits):NULL;
        free(mask_bits);
        if(!color || !mask){if(color)DeleteObject(color);if(mask)DeleteObject(mask);ok=0;break;}
        double angle=frame*6.283185307179586/FSA_SPIN_FRAMES,c=cos(angle),s=sin(angle);
        for(int y=0;y<height;y++)for(int x=0;x<width;x++){
            double dx=(x-cx)/scale,dy=(y-cy)/scale;
            double sx=c*dx+s*dy+cx,sy=-s*dx+c*dy+cy;
            int ix=(int)floor(sx),iy=(int)floor(sy);double fx=sx-ix,fy=sy-iy;
            for(int channel=0;channel<4;channel++){
                double value=0;
                for(int oy=0;oy<2;oy++)for(int ox=0;ox<2;ox++){
                    int px=ix+ox,py=iy+oy;
                    if(px>=0 && px<width && py>=0 && py<height)
                        value+=pixels[(py*width+px)*4+channel]*(ox?fx:1-fx)*(oy?fy:1-fy);
                }
                dest[(y*width+x)*4+channel]=(BYTE)(value+.5);
            }
        }
        ICONINFO info={0};info.fIcon=TRUE;info.hbmColor=color;info.hbmMask=mask;
        frames[frame]=CreateIconIndirect(&info);ok=frames[frame]!=NULL;
        DeleteObject(mask);DeleteObject(color);
    }
    SelectObject(dc,old);DeleteObject(bitmap);DeleteDC(dc);
    if(!ok)free_spinner(frames);
    return ok;
}
#endif
