#include <windows.h>
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include "web_host.h"
int main(void) {
    FSA_TrayClicks clicks={0};
    assert(!fsa_tray_click(&clicks,1000,500));
    assert(!fsa_tray_click(&clicks,1200,500));
    assert(fsa_tray_click(&clicks,1400,500));
    assert(clicks.count==0);
    assert(!fsa_tray_click(&clicks,1600,500));
    assert(!fsa_tray_click(&clicks,2300,500) && clicks.count==1);
    clicks.count=0; /* Timer/right-click resets the pending menu sequence. */
    assert(!fsa_tray_click(&clicks,2400,500) && clicks.count==1);
    assert(offsetof(FSA_WebSnapshot,observedMs)==40);
    assert(offsetof(FSA_WebSnapshot,endpoint)==48);
    assert(sizeof(FSA_WebSnapshot)==2352);
    puts("PASS triple-click discrimination, timeout/reset, pointer-free management snapshot ABI");
    return 0;
}
