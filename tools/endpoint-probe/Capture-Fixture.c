// Isolated owned-process fixture. Mimics only the verified capture descriptor.
#include <windows.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#ifdef NSP_TEST_BOUNDARY_DELAY
__declspec(dllexport) volatile LONG fixture_inside=0;
#endif
#ifdef NSP_TEST_HOOK_ABI
__declspec(dllexport) __declspec(noinline) uintptr_t boundary(void *block,uintptr_t second,uintptr_t third) {
#else
__declspec(dllexport) __declspec(noinline) void boundary(void *block) {
#endif
#ifdef NSP_TEST_BOUNDARY_DELAY
    InterlockedExchange(&fixture_inside,1);Sleep(500);InterlockedExchange(&fixture_inside,0);
#endif
    volatile uintptr_t observed=(uintptr_t)block;(void)observed;
#ifdef NSP_TEST_HOOK_ABI
    SetLastError(887);return (uintptr_t)block^second^third;
#endif
}
__declspec(dllexport) int feed(const uint8_t *properties,const uint8_t *pcm,
    uint32_t count,uint32_t stride,uint32_t iterations,uint32_t period_ms) {
    if(count<1||count>160||!stride||stride>16384)return -1;
    uint8_t block[0x600]={0},desc[0x70]={0};
    HMODULE module=GetModuleHandleW(L"nsp_fixture.dll");
    *(uintptr_t*)block=(uintptr_t)module+0x9f4f8;
    *(void**)(block+0x510)=desc;
    *(uint32_t*)(block+0x5d4)=count;block[0x5d8]=1;
    *(uint32_t*)(desc+12)=32;*(uint32_t*)(desc+20)=stride;
    for(int i=0;i<2;i++){
        *(const void**)(desc+0x28+i*24)=properties;
        *(const void**)(desc+0x38+i*24)=pcm;
    }
    LARGE_INTEGER begin,end,hz;QueryPerformanceFrequency(&hz);
    double total=0;
    for(uint32_t i=0;i<iterations;i++){
        *(uint32_t*)(block+0x5d0)=i%2;
        QueryPerformanceCounter(&begin);
#ifdef NSP_TEST_HOOK_ABI
        uintptr_t result=boundary(block+0x590,0x123456789abcdef0ULL,0xfedcba9876543210ULL);
        if(result!=((uintptr_t)(block+0x590)^0x123456789abcdef0ULL^0xfedcba9876543210ULL)||GetLastError()!=887)return -1;
#else
        boundary(block+0x590);
#endif
        QueryPerformanceCounter(&end);
        total+=(double)(end.QuadPart-begin.QuadPart)/hz.QuadPart;
        if(period_ms)Sleep(period_ms);
    }
    return (int)(total*1000000); // Total instrumented call time, microseconds.
}
