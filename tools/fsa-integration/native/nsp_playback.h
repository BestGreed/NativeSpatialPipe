/* Optional immutable playback profile. No audio/device operations in FSA. */
#ifndef FSA_NSP_PLAYBACK_H
#define FSA_NSP_PLAYBACK_H
static int nsp_append_playback(wchar_t *command,size_t capacity,const wchar_t *configuration,int legacy) {
    wchar_t path[1024];
    DWORD n=GetPrivateProfileStringW(L"NSP",L"PlaybackConfig",L"",path,1024,configuration);
    if(!n)return 1; /* Existing configurations retain capture-only behavior. */
    if(n>=1023 || legacy || wcspbrk(path,L"\"\r\n"))return 0;
    int absolute=(path[0] && path[1]==L':' && (path[2]==L'\\'||path[2]==L'/')) || (path[0]==L'\\'&&path[1]==L'\\');
    DWORD attributes=GetFileAttributesW(path);
    if(!absolute || attributes==INVALID_FILE_ATTRIBUTES || (attributes&FILE_ATTRIBUTE_DIRECTORY))return 0;
    size_t used=wcslen(command),needed=wcslen(L" --playback-config \"\"")+n;
    if(used>=capacity || needed>=capacity-used)return 0;
    return swprintf(command+used,capacity-used,L" --playback-config \"%ls\"",path)>0;
}
#endif
