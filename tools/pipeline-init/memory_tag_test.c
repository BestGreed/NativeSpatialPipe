#define SPATIAL_POLICY_NO_MAIN
#include "../fsa-integration/native/spatial_policy.c"
static wchar_t ini[MAX_PATH];
static AL_Snapshot profiles[3];
#include "../fsa-integration/native/resolver.h"
#include "../fsa-integration/native/pipeline_init.h"
int wmain(int argc,wchar_t **argv){
    if(argc!=3)return 2;
    int live=pipeline_live(argv[1],_wtoi(argv[2]));
    printf("{\"live\":%s}\n",live?"true":"false");
    return live?0:1;
}
