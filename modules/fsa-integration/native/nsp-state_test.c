#include "nsp_state.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    assert(nsp_verified(NSP_RUNNING,1,0));
    assert(!nsp_verified(NSP_STOPPED,1,0));
    assert(!nsp_verified(NSP_RUNNING,0,0));
    assert(!nsp_verified(NSP_RUNNING,1,1));
    assert(!nsp_verified(NSP_UNKNOWN,1,0));
    assert(nsp_blocks_rules(NSP_RUNNING));
    assert(nsp_blocks_rules(NSP_UNKNOWN));
    assert(nsp_blocks_rules(NSP_BUSY));
    assert(!nsp_blocks_rules(NSP_STOPPED));
    puts("PASS Sonic + Hook verification, fault lock, busy and stopped arbitration");
    return 0;
}
