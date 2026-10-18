#ifndef FSA_RESOLVER_H
#define FSA_RESOLVER_H
#include <stdint.h>
enum { MODE_STEREO, MODE_51, MODE_71, MODE_ATMOS, MODE_DTS, MODE_SONIC, MODE_NSP, MODE_COUNT };
enum { SCOPE_FOREGROUND, SCOPE_RUNNING, SCOPE_AUDIO_ACTIVE };
typedef struct { int enabled, scope, profile, priority; } Rule;
/* Inputs are a complete observation, never an individual event's desired mode.
   AudioActive is reserved and cannot match until a session provider exists. */
static int resolve(const Rule *rules, const unsigned char *matches, int count, int fallback) {
    int winner=-1;
    for(int i=0;i<count;i++) {
        if(!rules[i].enabled || rules[i].scope==SCOPE_AUDIO_ACTIVE || !matches[i]) continue;
        if(winner<0 || rules[i].priority>rules[winner].priority) winner=i;
    }
    return winner<0?fallback:rules[winner].profile;
}
typedef struct { int pending; uint64_t since; } Debounce;
/* Sleep to the earliest necessary deadline. Never postpone audit, retry,
   debounce, or the existing two-second process observation bound. */
static unsigned next_wait(uint64_t now,uint64_t audit,int poll_processes,
                          uint64_t debounce_due,uint64_t retry_due) {
    uint64_t due=audit;
    if(poll_processes && now+2000<due) due=now+2000;
    if(debounce_due && debounce_due<due) due=debounce_due;
    if(retry_due && retry_due<due) due=retry_due;
    return due<=now?1:(unsigned)(due-now);
}
static int settled(Debounce *d,int candidate,uint64_t now,unsigned delay) {
    if(d->pending!=candidate) { d->pending=candidate; d->since=now; }
    return now-d->since>=delay;
}
#endif
