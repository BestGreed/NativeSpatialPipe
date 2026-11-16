#include <windows.h>
#include <wchar.h>
#include <stdio.h>
#include <assert.h>
#include "nsp_playback.h"
static void file(const wchar_t *path){HANDLE h=CreateFileW(path,GENERIC_WRITE,0,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);assert(h!=INVALID_HANDLE_VALUE);CloseHandle(h);}
int wmain(int argc,wchar_t **argv){
 assert(argc==2);wchar_t dir[1024],ini[1024],old[1024],user[1024],custom[1024],command[4096];
 swprintf(dir,1024,L"%ls\\NativeSpatialPipe",argv[1]);CreateDirectoryW(dir,NULL);
 swprintf(ini,1024,L"%ls\\fsa.ini",argv[1]);swprintf(old,1024,L"%ls\\playback.json",argv[1]);
 swprintf(user,1024,L"%ls\\playback.json",dir);swprintf(custom,1024,L"%ls\\custom.json",argv[1]);
 assert(SetEnvironmentVariableW(L"LOCALAPPDATA",argv[1]));
 wcscpy(command,L"host");assert(nsp_append_playback(command,4096,ini,0));assert(!wcscmp(command,L"host"));
 file(old);wcscpy(command,L"host");assert(nsp_append_playback(command,4096,ini,0));assert(wcsstr(command,old));
 file(user);wcscpy(command,L"host");assert(nsp_append_playback(command,4096,ini,0));assert(wcsstr(command,user));
 WritePrivateProfileStringW(L"NSP",L"PlaybackConfig",old,ini);
 wcscpy(command,L"host");assert(nsp_append_playback(command,4096,ini,0));assert(wcsstr(command,user));
 file(custom);WritePrivateProfileStringW(L"NSP",L"PlaybackConfig",custom,ini);
 wcscpy(command,L"host");assert(nsp_append_playback(command,4096,ini,0));assert(wcsstr(command,custom));
 wcscpy(command,L"host");assert(!nsp_append_playback(command,4096,ini,1));
 puts("PASS capture-only, legacy fallback, user default, old pointer, custom override and legacy rejection");return 0;
}
