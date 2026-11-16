/* Reader-only contract. A disk preference never grants a validation bypass. */
#ifndef NSP_PIPELINE_INIT_H
#define NSP_PIPELINE_INIT_H
typedef struct {
    DWORD seq,magic,version,state,pid,flags;
    ULONGLONG heartbeat,created;
    DWORD service_pid,mask;
    WCHAR endpoint[512];
} NSP_PipelineTag;
static const wchar_t *pipeline_names[]={L"WindowsSonic",L"DolbyAtmosForHeadphones",L"DolbyAtmosForSpeakers",L"DolbyAtmosForHomeTheater",L"DTSHeadphoneX",L"DTSXUltra",L"DTSXForHomeTheater"};
static const int pipeline_modes[]={MODE_SONIC,MODE_DOLBY_HP,MODE_DOLBY_SP,MODE_ATMOS,MODE_DTS_HP,MODE_DTS_SP,MODE_DTS};
static int pipeline_format_for_mode(int mode){for(int i=0;i<7;i++)if(pipeline_modes[i]==mode)return i;return -1;}
static const wchar_t *pipeline_guids[]={L"{B53D940C-B846-4831-9F76-D102B9B725A0}",L"{1459AC38-3875-49BF-BB59-0FE80F4D395D}",L"{4C81E564-C8EF-4AD9-9F2C-9EF995533790}",L"{A289735D-FA3E-4E35-9D7D-B6F896ACB2E7}",L"{4444ACB0-8DC0-4C2C-A0D8-2C76DB470F86}",L"{ADAFD3C6-AC4C-404A-836A-9615E9060564}",L"{10201B4A-3322-4967-BF40-2CAA9BAFCA44}"};
static int pipeline_index(void) {
    wchar_t name[80];GetPrivateProfileStringW(L"NSP",L"FrontendFormat",L"WindowsSonic",name,80,ini);
    for(int i=0;i<7;i++)if(!wcscmp(name,pipeline_names[i]))return i;
    return -1;
}
static int pipeline_live_flags(const wchar_t *endpoint,int index,unsigned required_flags) {
    if(index<0 || index>=7)return 0;
    HANDLE map=OpenFileMappingW(FILE_MAP_READ,FALSE,L"Local\\NSp-PipelineInit-v1");if(!map)return 0;
    const volatile NSP_PipelineTag *v=(const volatile NSP_PipelineTag*)MapViewOfFile(map,FILE_MAP_READ,0,0,2048);
    NSP_PipelineTag tag={0};DWORD seq=0;int valid=0;
    if(v){seq=v->seq;MemoryBarrier();memcpy(&tag,(const void*)v,sizeof(tag));MemoryBarrier();valid=!(seq&1) && seq==v->seq;UnmapViewOfFile((const void*)v);}
    CloseHandle(map);
    ULONGLONG now=GetTickCount64();
    if(!valid || tag.magic!=0x4950534e || tag.version!=1 || tag.state!=2 || (tag.flags&(1u|required_flags))!=(1u|required_flags) || !(tag.mask&(1u<<index)) || now<tag.heartbeat || now-tag.heartbeat>3000 || tag.endpoint[511] || _wcsicmp(tag.endpoint,endpoint))return 0;
    HANDLE process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,tag.pid);
    if(!process)return 0;
    FILETIME created={0},exit,kernel,user;ULARGE_INTEGER t;
    valid=WaitForSingleObject(process,0)==WAIT_TIMEOUT && GetProcessTimes(process,&created,&exit,&kernel,&user);
    t.LowPart=created.dwLowDateTime;t.HighPart=created.dwHighDateTime;
    valid=valid && t.QuadPart==tag.created;CloseHandle(process);
    return valid;
}
static int pipeline_live(const wchar_t *endpoint,int index){return pipeline_live_flags(endpoint,index,0);}
/* Endpoint preparation owns spatial-format discovery. Read-only: never prepares,
   selects a format, changes PCM profiles or injects EDID. */
static unsigned pipeline_query_capabilities(SpatialPolicy *p,const wchar_t *endpoint){
    unsigned flags=0;
    if(p){
        WAVEFORMATEX *f=NULL;SpatialSettings *s=NULL;BYTE *d=NULL;UINT32 n=0;
        HRESULT hr=p->v->get(p,endpoint,FALSE,&f,&s,&n,&d);
        if(SUCCEEDED(hr) && n<=128 && d)for(UINT32 i=0;i<n;i++){
            DWORD supported=0;memcpy(&supported,d+i*0x342+0x310,4);
            for(int j=0;j<7;j++){GUID g;if(supported && SUCCEEDED(CLSIDFromString(pipeline_guids[j],&g)) && !memcmp(d+i*0x342+0x300,&g,16))flags|=1u<<pipeline_modes[j];}
        }
        CoTaskMemFree(f);CoTaskMemFree(s);CoTaskMemFree(d);
    }
    for(int i=0;i<7;i++)if(pipeline_live(endpoint,i))flags|=1u<<pipeline_modes[i];
    return flags;
}
static int pipeline_selected(const SpatialSettings *settings) {
    int i=pipeline_index();GUID expected;
    if(i<0 || !settings->words[0] || FAILED(CLSIDFromString(pipeline_guids[i],&expected)) || memcmp(&settings->words[3],&expected,16))return 0;
    int requested=(i==1 || i==4)?GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini):
                  (i==2 || i==5)?GetPrivateProfileIntW(L"NSP",L"HookSpeakers",0,ini):0;
    return !requested || pipeline_live(profiles[0].endpoint,i);
}
static int pipeline_initialization_requested(int i) {
    wchar_t script[MAX_PATH];GetPrivateProfileStringW(L"NSP",L"PipelineInitScript",L"",script,MAX_PATH,ini);
    if(!script[0])return 0;
    return (i==1 || i==4)?GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini)!=0:
           (i==2 || i==5)?GetPrivateProfileIntW(L"NSP",L"HookSpeakers",0,ini)!=0:0;
}
/* Worker only: execute explicit parameterized initializer; no hidden UAC. */
static HRESULT pipeline_command_index(const wchar_t *action,int i) {
    wchar_t python[MAX_PATH],script[MAX_PATH],command[4096];
    GetPrivateProfileStringW(L"NSP",L"Python",L"",python,MAX_PATH,ini);
    GetPrivateProfileStringW(L"NSP",L"PipelineInitScript",L"",script,MAX_PATH,ini);
    if(i<0 || i>=7 || !python[0] || !script[0] || wcschr(python,L'"') || wcschr(script,L'"'))return E_INVALIDARG;
    int persist=GetPrivateProfileIntW(L"NSP",L"PersistEnumerationHook",0,ini)!=0;
    int hp=GetPrivateProfileIntW(L"NSP",L"HookHeadphones",0,ini)!=0;
    int sp=GetPrivateProfileIntW(L"NSP",L"HookSpeakers",0,ini)!=0;
    if(!wcscmp(action,L"recover"))swprintf(command,4096,L"\"%ls\" \"%ls\" recover",python,script);
    else swprintf(command,4096,L"\"%ls\" \"%ls\" %ls --endpoint \"%ls\" --format %ls --owner-pid %lu %ls %ls %ls %ls",python,script,action,profiles[0].endpoint,pipeline_names[i],GetCurrentProcessId(),persist?L"--persist":L"",hp||sp?L"--groups":L"",hp?L"headphones":L"",sp?L"speakers":L"");
    STARTUPINFOW si={.cb=sizeof(si)};PROCESS_INFORMATION child={0};
    if(!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&si,&child))return HRESULT_FROM_WIN32(GetLastError());
    CloseHandle(child.hThread);DWORD result=1,wait=WaitForSingleObject(child.hProcess,30000);
    if(wait==WAIT_OBJECT_0)GetExitCodeProcess(child.hProcess,&result);
    CloseHandle(child.hProcess);return wait==WAIT_OBJECT_0 && result==0?S_OK:E_FAIL;
}
static HRESULT pipeline_command(const wchar_t *action){return pipeline_command_index(action,pipeline_index());}
/* Explicit menu operation only. FSA owns the returned process handle and polls
   completion; initialization/reload work stays outside its coordination loop. */
static HRESULT pipeline_edid_begin(const wchar_t *endpoint,const wchar_t *output,HANDLE *process){
    wchar_t python[MAX_PATH],script[MAX_PATH],command[4096];*process=NULL;
    GetPrivateProfileStringW(L"NSP",L"Python",L"",python,MAX_PATH,ini);
    GetPrivateProfileStringW(L"NSP",L"PipelineInitScript",L"",script,MAX_PATH,ini);
    if(!python[0] || !script[0] || wcschr(python,L'"') || wcschr(script,L'"') || wcschr(endpoint,L'"') || wcschr(output,L'"'))return E_INVALIDARG;
    swprintf(command,4096,L"\"%ls\" \"%ls\" edid-enable --endpoint \"%ls\" --output \"%ls\"",python,script,endpoint,output);
    STARTUPINFOW si={.cb=sizeof(si)};PROCESS_INFORMATION child={0};
    if(!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_NO_WINDOW,NULL,NULL,&si,&child))return HRESULT_FROM_WIN32(GetLastError());
    CloseHandle(child.hThread);*process=child.hProcess;return S_OK;
}
#endif
