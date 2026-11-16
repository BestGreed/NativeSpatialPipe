/* Native ABI fixture: same four Windows x64 parameters as each AudioSrv target. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <string.h>
static volatile LONG calls;
__declspec(dllexport) __declspec(noinline) LONG license_status(void *self,const wchar_t *guid,const wchar_t *ep,void *out){
 (void)guid;(void)ep;InterlockedIncrement(&calls);memset(out,0xa5,32);SetLastError(1234);return self?(LONG)0x80004005:0;
}
__declspec(dllexport) __declspec(noinline) LONG subtype(void *self,const wchar_t *ep,int index,void *out){
 (void)ep;(void)index;InterlockedIncrement(&calls);memset(out,0xa5,32);SetLastError(1234);return self?(LONG)0x80004005:0;
}
__declspec(dllexport) __declspec(noinline) LONG extended(void *self,const wchar_t *ep,unsigned count,void *out){
 (void)ep;InterlockedIncrement(&calls);memset(out,0xa5,count*0x130);SetLastError(1234);return self?(LONG)0x80004005:0;
}
