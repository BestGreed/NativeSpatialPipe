#include "resolver.h"
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
int main(void) {
    Rule r[]={{1,SCOPE_FOREGROUND,MODE_STEREO,100},{1,SCOPE_RUNNING,MODE_ATMOS,300},{1,SCOPE_RUNNING,MODE_DTS,300},{1,SCOPE_AUDIO_ACTIVE,MODE_71,999}};
    unsigned char m[]={1,1,1,1};
    assert(resolve(r,m,4,MODE_51)==MODE_ATMOS);
    m[1]=0; assert(resolve(r,m,4,MODE_51)==MODE_DTS);
    m[2]=0; assert(resolve(r,m,4,MODE_51)==MODE_STEREO);
    m[0]=0; assert(resolve(r,m,4,MODE_51)==MODE_51);
    m[1]=1; r[1].enabled=0; assert(resolve(r,m,4,MODE_51)==MODE_51);
    Debounce d={-1,0};
    assert(!settled(&d,MODE_ATMOS,100,500));
    assert(!settled(&d,MODE_STEREO,300,500));
    assert(!settled(&d,MODE_STEREO,799,500));
    assert(settled(&d,MODE_STEREO,800,500));
    assert(next_wait(1000,11000,0,0,0)==10000);
    assert(next_wait(1000,11000,1,0,0)==2000);
    assert(next_wait(1000,11000,1,1250,0)==250);
    assert(next_wait(1000,11000,0,0,1800)==800);
    assert(next_wait(1000,1000,0,0,0)==1);
    puts("PASS resolver priority, tie, exit, fallback, disabled, reserved scope and debounce tests");
}
