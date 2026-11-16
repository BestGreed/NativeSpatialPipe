#include "audio_layout.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mmreg.h>

static int save(const wchar_t *path,const AL_Snapshot *s) {
    HANDLE f=CreateFileW(path,GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);
    if(f==INVALID_HANDLE_VALUE) return 0;
    DWORD written=0;
    int ok=WriteFile(f,s,sizeof(*s),&written,NULL) && written==sizeof(*s) && FlushFileBuffers(f);
    CloseHandle(f); return ok;
}
static int load(const wchar_t *path,AL_Snapshot *s) {
    HANDLE f=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    if(f==INVALID_HANDLE_VALUE) return 0;
    DWORD read=0;
    int ok=GetFileSize(f,NULL)==sizeof(*s) && ReadFile(f,s,sizeof(*s),&read,NULL) && read==sizeof(*s);
    CloseHandle(f); return ok && al_valid(s);
}
static uint32_t layout(const wchar_t *name) {
    if(!wcscmp(name,L"stereo")) return 3;
    if(!wcscmp(name,L"5.1-back")) return 0x3f;
    if(!wcscmp(name,L"5.1-side")) return 0x60f;
    if(!wcscmp(name,L"7.1")) return 0x63f;
    return 0;
}
int wmain(int argc,wchar_t **argv) {
    if(argc<2) {
        puts("audio-layout read [endpoint-id]\n"
             "audio-layout capture <new-snapshot-file> [endpoint-id]\n"
             "audio-layout plan <stereo|5.1-back|5.1-side|7.1> [endpoint-id]\n"
             "audio-layout set <layout> <new-backup-file> [endpoint-id]\n"
             "audio-layout set-hires <layout> <new-backup-file> [endpoint-id] (24bit/192kHz, all large)\n"
             "audio-layout restore <snapshot-file>\n"
             "audio-layout all-large <new-backup-file> [endpoint-id]\n"
             "audio-layout cycle <new-backup-file> [endpoint-id]\n"
             "cycle tests standard layouts and always attempts original-state restoration.");
        return 2;
    }
    HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);
    if(FAILED(hr)) return 1;
    AL_Snapshot before,target,after;
    HRESULT rollback=S_FALSE;
    int exit_code=1;
    int is_read=!wcscmp(argv[1],L"read");
    int is_capture=!wcscmp(argv[1],L"capture");
    int is_plan=!wcscmp(argv[1],L"plan");
    int hires=!wcscmp(argv[1],L"set-hires");
    int is_set=hires || !wcscmp(argv[1],L"set");
    int is_restore=!wcscmp(argv[1],L"restore");
    int is_cycle=!wcscmp(argv[1],L"cycle");
    if(!wcscmp(argv[1],L"all-large") && (argc==3 || argc==4)) {
        hr=al_capture(argc==4?argv[3]:NULL,&before);
        if(FAILED(hr)) goto error;
        if(!al_valid(&before) || !before.physical_present || !before.physical) {
            puts("Requires a standard PCM endpoint with a known speaker layout"); goto done;
        }
        target=before;
        target.fullrange_present=1;
        /* LFE is a dedicated effects channel, not a main full-range speaker. */
        target.fullrange=before.physical & ~0x8u;
        if(!save(argv[2],&before)) { puts("Cannot save recovery snapshot; no changes made"); goto done; }
        hr=al_apply(&target,&rollback);
        printf("All main speakers large: 0x%08lx rollback: 0x%08lx\n",(unsigned long)hr,(unsigned long)rollback);
        if(FAILED(hr)) goto error;
        hr=al_capture(before.endpoint,&after);
        if(FAILED(hr)) goto error;
        al_print(&after); exit_code=0; goto done;
    }
    if(is_restore && argc==3) {
        if(!load(argv[2],&target)) { puts("Invalid snapshot"); goto done; }
        hr=al_apply(&target,&rollback);
        printf("Restore: 0x%08lx rollback: 0x%08lx\n",(unsigned long)hr,(unsigned long)rollback);
        if(SUCCEEDED(hr)) al_print(&target);
        exit_code=FAILED(hr); goto done;
    }
    int id_arg=is_read?2:is_set?4:3;
    if ((!is_read && !is_capture && !is_plan && !is_set && !is_cycle) ||
        argc<id_arg || argc>id_arg+1) { puts("Invalid arguments"); goto done; }
    hr=al_capture(argc>id_arg?argv[id_arg]:NULL,&before);
    if(FAILED(hr)) goto error;
    al_print(&before);
    if(is_read) { exit_code=0; goto done; }
    if(!al_valid(&before)) { puts("Only uncompressed standard PCM/float snapshots are supported"); goto done; }
    if(is_capture) {
        exit_code=!save(argv[2],&before);
        puts(exit_code?"Snapshot save failed (existing files are never overwritten)":"Snapshot saved"); goto done;
    }
    if(is_plan || is_set) {
        hr=al_plan(&before,layout(argv[2]),&target); if(FAILED(hr)) goto error;
        if(hires) {
            const GUID pcm={1,0,0x10,{0x80,0,0,0xaa,0,0x38,0x9b,0x71}};
            const GUID floating={3,0,0x10,{0x80,0,0,0xaa,0,0x38,0x9b,0x71}};
            WAVEFORMATEXTENSIBLE *device=(WAVEFORMATEXTENSIBLE*)target.device;
            WAVEFORMATEXTENSIBLE *mix=(WAVEFORMATEXTENSIBLE*)target.mix;
            /* This HDMI endpoint carries 24 valid bits in a 32-bit container. */
            device->SubFormat=pcm; device->Format.wBitsPerSample=32;
            device->Samples.wValidBitsPerSample=24;
            mix->SubFormat=floating; mix->Format.wBitsPerSample=32;
            mix->Samples.wValidBitsPerSample=32;
            WAVEFORMATEXTENSIBLE *formats[]={device,mix};
            for(unsigned i=0;i<2;i++) {
                formats[i]->Format.nSamplesPerSec=192000;
                formats[i]->Format.nBlockAlign=formats[i]->Format.nChannels*(formats[i]->Format.wBitsPerSample/8);
                formats[i]->Format.nAvgBytesPerSec=192000*formats[i]->Format.nBlockAlign;
            }
            target.fullrange_present=1; target.fullrange=target.physical & ~0x8u;
        }
        puts("Target:"); al_print(&target);
        hr=al_supported(&target); if(FAILED(hr)) goto error;
        if(is_plan) { puts(hr==S_FALSE?"Preflight unavailable (exclusive mode disabled); no changes made":"Format preflight passed; no changes made"); exit_code=0; goto done; }
        if(!save(argv[3],&before)) { puts("Cannot save recovery snapshot; no changes made"); goto done; }
        hr=al_apply(&target,&rollback);
        printf("Apply: 0x%08lx rollback: 0x%08lx\n",(unsigned long)hr,(unsigned long)rollback);
        if(FAILED(hr)) goto error;
        hr=al_capture(before.endpoint,&after); if(FAILED(hr)) goto error;
        al_print(&after); exit_code=0; goto done;
    }
    if(!save(argv[2],&before)) { puts("Cannot save recovery snapshot; no changes made"); goto done; }
    uint32_t masks[]={3,0x3f,0x63f};
    int failures=0;
    for(unsigned i=0;i<3;i++) {
        hr=al_plan(&before,masks[i],&target);
        ULONGLONG start=GetTickCount64();
        if(SUCCEEDED(hr)) hr=al_apply(&target,&rollback);
        printf("Layout 0x%x: result=0x%08lx rollback=0x%08lx elapsed_ms=%llu\n",masks[i],(unsigned long)hr,(unsigned long)rollback,GetTickCount64()-start);
        if(FAILED(hr)) { failures++; break; }
        hr=al_capture(before.endpoint,&after);
        if(FAILED(hr)) { failures++; break; }
        al_print(&after);
    }
    hr=al_apply(&before,&rollback);
    printf("Original restoration: 0x%08lx\n",(unsigned long)hr);
    if(SUCCEEDED(hr)) {
        hr=al_capture(before.endpoint,&after);
        if(SUCCEEDED(hr) && al_equal(&before,&after)) puts("PASS exact original state restored");
        else { puts("FAIL original state mismatch"); failures++; }
    } else failures++;
    exit_code=failures?1:0;
    goto done;
error:
    fprintf(stderr,"Error HRESULT=0x%08lx\n",(unsigned long)hr);
done:
    CoUninitialize(); return exit_code;
}
