// Experimental Windows x64 SPSC transport. Audio capture never waits for space.
// Slot ownership is published/released through interlocked operations.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sddl.h>
#include <stdint.h>
#include <string.h>
#define API __declspec(dllexport)
#define CAP 16
#define HEADER 8192
#define PAYLOAD (160*(32+4096*4))
#define MAGIC 0x3150534e
typedef struct { volatile LONG ready; uint32_t nh,nd; char header[HEADER]; unsigned char data[PAYLOAD]; } Slot;
typedef struct { uint32_t magic,version,capacity,slot_bytes; Slot slots[CAP]; } Ring;
typedef struct { HANDLE mapping,event; Ring *ring; uint32_t next; } Link;
static DWORD open_error;
API uint32_t nsp_link_open_error(void){return open_error;}
API void nsp_link_close(Link *p) {
 if(!p)return;if(p->ring)UnmapViewOfFile(p->ring);if(p->event)CloseHandle(p->event);if(p->mapping)CloseHandle(p->mapping);HeapFree(GetProcessHeap(),0,p);
}
API Link *nsp_link_open(const wchar_t *name,const wchar_t *event_name,int create) {
 open_error=0;
 Link *p=HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,sizeof(Link));if(!p)return NULL;
 PSECURITY_DESCRIPTOR sd=NULL; SECURITY_ATTRIBUTES sa={sizeof(sa),NULL,FALSE};
 if(create) {
  // Explicit creator SID, SYSTEM and LocalService; never Everyone.
  HANDLE token=NULL;BYTE user[256];DWORD needed=0;LPWSTR sid=NULL;wchar_t acl[256];
  if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token))goto fail;
  BOOL ok=GetTokenInformation(token,TokenUser,user,sizeof(user),&needed);CloseHandle(token);if(!ok)goto fail;
  if(!ConvertSidToStringSidW(((TOKEN_USER*)user)->User.Sid,&sid))goto fail;
  if(lstrlenW(sid)>180){LocalFree(sid);goto fail;}
  lstrcpyW(acl,L"D:P(A;;GA;;;SY)(A;;GA;;;LS)(A;;GA;;;");lstrcatW(acl,sid);lstrcatW(acl,L")");LocalFree(sid);
  if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(acl,SDDL_REVISION_1,&sd,NULL))goto fail;
  sa.lpSecurityDescriptor=sd;
  p->mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,&sa,PAGE_READWRITE,0,sizeof(Ring),name);
  if(!p->mapping || GetLastError()==ERROR_ALREADY_EXISTS)goto fail;
  p->event=CreateEventW(&sa,FALSE,FALSE,event_name);
  if(!p->event || GetLastError()==ERROR_ALREADY_EXISTS)goto fail;
 } else {
  p->mapping=OpenFileMappingW(FILE_MAP_READ|FILE_MAP_WRITE,FALSE,name);
  if(!p->mapping){open_error=GetLastError();goto fail;}
  p->event=OpenEventW(EVENT_MODIFY_STATE|SYNCHRONIZE,FALSE,event_name);
 }
 if(sd){LocalFree(sd);sd=NULL;}
 if(!p->mapping||!p->event)goto fail;
 p->ring=MapViewOfFile(p->mapping,FILE_MAP_READ|FILE_MAP_WRITE,0,0,sizeof(Ring));if(!p->ring)goto fail;
 if(create){memset(p->ring,0,sizeof(Ring));p->ring->version=1;p->ring->capacity=CAP;p->ring->slot_bytes=sizeof(Slot);p->ring->magic=MAGIC;}
 if(p->ring->magic!=MAGIC||p->ring->version!=1||p->ring->capacity!=CAP||p->ring->slot_bytes!=sizeof(Slot)){open_error=ERROR_REVISION_MISMATCH;goto fail;}
 return p;
fail: if(!open_error)open_error=GetLastError();if(sd)LocalFree(sd);nsp_link_close(p);return NULL;
}
API int nsp_link_send(Link *p,const char *header,uint32_t nh,const void *data,uint32_t nd) {
 if(!p||!header||!data||!nh||nh>HEADER||!nd||nd>PAYLOAD)return -1;
 Slot *s=&p->ring->slots[p->next];if(InterlockedCompareExchange(&s->ready,0,0)!=0)return -2;
 memcpy(s->header,header,nh);memcpy(s->data,data,nd);s->nh=nh;s->nd=nd;
 InterlockedExchange(&s->ready,1);p->next=(p->next+1)%CAP;
 return SetEvent(p->event)?0:-3;
}
API int nsp_link_read(Link *p,char *header,uint32_t hc,void *data,uint32_t dc,uint32_t *sizes) {
 if(!p||!header||!data||!sizes)return -1;
 Slot *s=&p->ring->slots[p->next];if(InterlockedCompareExchange(&s->ready,0,0)!=1)return 0;
 if(!s->nh||s->nh>hc||s->nh>HEADER||!s->nd||s->nd>dc||s->nd>PAYLOAD)return -2;
 memcpy(header,s->header,s->nh);memcpy(data,s->data,s->nd);sizes[0]=s->nh;sizes[1]=s->nd;
 InterlockedExchange(&s->ready,0);p->next=(p->next+1)%CAP;return 1;
}
API int nsp_link_wait(Link *p,uint32_t timeout) { if(!p)return -1;DWORD r=WaitForSingleObject(p->event,timeout);return r==WAIT_OBJECT_0?1:(r==WAIT_TIMEOUT?0:-1); }
API int nsp_link_wake(Link *p) { return p&&SetEvent(p->event); }
