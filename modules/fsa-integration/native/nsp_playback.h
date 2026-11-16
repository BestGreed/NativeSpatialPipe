/* Optional immutable playback profile. No audio/device operations in FSA. */
#ifndef FSA_NSP_PLAYBACK_H
#define FSA_NSP_PLAYBACK_H
static int nsp_append_playback(wchar_t *command,size_t capacity,const wchar_t *configuration,int legacy_scheduler) {
    wchar_t path[1024];
    DWORD n=GetPrivateProfileStringW(L"NSP",L"PlaybackConfig",L"",path,1024,configuration);
    wchar_t legacy[1024],user[1024];
    DWORD full=GetFullPathNameW(configuration,1024,legacy,NULL);
    if(!full || full>=1024)return 0;
    wchar_t *slash=wcsrchr(legacy,L'\\'),*other=wcsrchr(legacy,L'/');
    if(other && (!slash || other>slash))slash=other;
    if(!slash || (size_t)(slash-legacy)+1+wcslen(L"playback.json")>=1024)return 0;
    wcscpy(slash+1,L"playback.json");
    wchar_t normalized[1024];DWORD resolved=n?GetFullPathNameW(path,1024,normalized,NULL):0;
    int old_default=n && resolved && resolved<1024 && !_wcsicmp(normalized,legacy);
    if(!n || old_default){
        DWORD local=GetEnvironmentVariableW(L"LOCALAPPDATA",user,1024);
        if(!local || local>=1024 || local+wcslen(L"\\NativeSpatialPipe\\playback.json")>=1024)return 0;
        wcscat(user,L"\\NativeSpatialPipe\\playback.json");
        DWORD attrs=GetFileAttributesW(user);
        if(attrs!=INVALID_FILE_ATTRIBUTES && !(attrs&FILE_ATTRIBUTE_DIRECTORY))wcscpy(path,user);
        else if(!n){
            attrs=GetFileAttributesW(legacy);
            if(attrs==INVALID_FILE_ATTRIBUTES || (attrs&FILE_ATTRIBUTE_DIRECTORY))return 1;
            wcscpy(path,legacy); /* Read-only fallback until Web migration. */
        }
        n=(DWORD)wcslen(path);
    }
    if(n>=1023 || legacy_scheduler || wcspbrk(path,L"\"\r\n"))return 0;
    int absolute=(path[0] && path[1]==L':' && (path[2]==L'\\'||path[2]==L'/')) || (path[0]==L'\\'&&path[1]==L'\\');
    DWORD attributes=GetFileAttributesW(path);
    if(!absolute || attributes==INVALID_FILE_ATTRIBUTES || (attributes&FILE_ATTRIBUTE_DIRECTORY))return 0;
    size_t used=wcslen(command),needed=wcslen(L" --playback-config \"\"")+n;
    if(used>=capacity || needed>=capacity-used)return 0;
    return swprintf(command+used,capacity-used,L" --playback-config \"%ls\"",path)>0;
}
#endif
