#define INITGUID
#include <windows.h>
#include <mmreg.h>
#include <ks.h>
#include <ksmedia.h>
#include <stdio.h>
#include <string.h>
#include "audio_layout.h"
static unsigned passed;
#define CHECK(x) do { if(!(x)) { printf("FAIL line %d: %s\n",__LINE__,#x); return 1; } ++passed; } while(0)
int main(int argc, char **argv) {
    AL_Snapshot s={0},t,u;
    s.version=AL_VERSION; wcscpy(s.endpoint,L"synthetic-endpoint");
    s.physical_present=s.fullrange_present=1;
    s.physical=0x3f; s.fullrange=3;
    WAVEFORMATEXTENSIBLE f={0};
    f.Format.wFormatTag=WAVE_FORMAT_EXTENSIBLE; f.Format.nChannels=6;
    f.Format.nSamplesPerSec=48000; f.Format.wBitsPerSample=24;
    f.Format.nBlockAlign=18; f.Format.nAvgBytesPerSec=864000; f.Format.cbSize=22;
    f.Samples.wValidBitsPerSample=24; f.dwChannelMask=0x3f; f.SubFormat=KSDATAFORMAT_SUBTYPE_PCM;
    memcpy(s.device,&f,sizeof(f)); s.device_size=sizeof(f);
    f.Format.wBitsPerSample=32; f.Format.nBlockAlign=24; f.Format.nAvgBytesPerSec=1152000;
    f.Samples.wValidBitsPerSample=32; f.SubFormat=KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
    memcpy(s.mix,&f,sizeof(f)); s.mix_size=sizeof(f);
    CHECK(al_valid(&s));
    CHECK(al_plan(&s,0x63f,&t)==S_OK);
    CHECK(((WAVEFORMATEXTENSIBLE*)t.device)->Format.nChannels==8);
    CHECK(((WAVEFORMATEXTENSIBLE*)t.device)->Format.nBlockAlign==24);
    CHECK(((WAVEFORMATEXTENSIBLE*)t.device)->Format.nAvgBytesPerSec==1152000);
    CHECK(((WAVEFORMATEXTENSIBLE*)t.device)->Format.wBitsPerSample==24);
    CHECK(t.fullrange==3); /* Never infer new speakers are full-range. */
    CHECK(((WAVEFORMATEXTENSIBLE*)t.mix)->Format.nBlockAlign==32);
    CHECK(al_valid(&t));
    CHECK(al_plan(&s,0x60f,&t)==S_OK);
    CHECK(((WAVEFORMATEXTENSIBLE*)t.device)->dwChannelMask==0x60f);
    CHECK(al_plan(&s,0x3f,&t)==S_OK && al_equal(&s,&t));
    CHECK(al_plan(&s,3,&t)==S_OK && t.physical==3);
    CHECK(!al_equal(&s,&t));
    CHECK(FAILED(al_plan(&s,0xffff,&t)));
    CHECK(FAILED(al_plan(NULL,3,&t)));
    u=s; u.device_size=AL_FORMAT_CAP+1; CHECK(!al_valid(&u));
    u=s; ((WAVEFORMATEX*)u.device)->cbSize=0xffff; CHECK(!al_valid(&u));
    u=s; ((WAVEFORMATEX*)u.mix)->nBlockAlign=1; CHECK(!al_valid(&u));
    u=s; ((WAVEFORMATEXTENSIBLE*)u.device)->dwChannelMask=3; CHECK(!al_valid(&u));
    u=s; u.version=999; CHECK(!al_valid(&u));
    u=s; for(unsigned i=0;i<1024;i++) u.endpoint[i]=L'x'; CHECK(!al_valid(&u));
    u=s; u.fullrange_present=0; u.fullrange=0;
    CHECK(al_plan(&u,3,&t)==S_OK && !t.fullrange_present);
    if(argc==2) {
        CHECK(SUCCEEDED(CoInitializeEx(NULL,COINIT_MULTITHREADED)));
        HMODULE dll=LoadLibraryA(argv[1]); CHECK(dll!=NULL);
        HRESULT (*capture)(const wchar_t*,AL_Snapshot*)=(void*)GetProcAddress(dll,"al_capture");
        CHECK(capture!=NULL);
        CHECK(SUCCEEDED(capture(NULL,&s)));
        CHECK(SUCCEEDED(al_capture(s.endpoint,&t)));
        CHECK(al_equal(&s,&t));
        FreeLibrary(dll); CoUninitialize();
    }
    printf("PASS %u native assertions (no hardware writes)\n",passed);
    return 0;
}
