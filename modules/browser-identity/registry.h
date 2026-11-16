// Bounded local sideband contract; contains identities only, never PCM.
#pragma once
#include <stdint.h>
constexpr uint32_t BROWSER_IDENTITY_MAGIC=0x3149424e, BROWSER_IDENTITY_VERSION=1;
struct BrowserIdentityTag {uint64_t sourceKeyHi=0,sourceKeyLo=0;uint32_t browserPid=0,tabId=0,windowId=0,generation=0;};
struct BrowserIdentityHeader {uint32_t magic,version,count,recordSize;uint64_t publishedQpcNs,reserved;};
struct BrowserIdentityRecord {uint32_t hostPid,sourcePid;uint64_t sourceStarted,queue,mapping,control,validatedQpcNs;BrowserIdentityTag tag;uint64_t browserStarted,reserved;};
static_assert(sizeof(BrowserIdentityTag)==32&&sizeof(BrowserIdentityHeader)==32&&sizeof(BrowserIdentityRecord)==96,"browser identity ABI");
