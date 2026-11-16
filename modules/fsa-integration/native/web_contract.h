#ifndef NSP_FSA_WEB_CONTRACT_H
#define NSP_FSA_WEB_CONTRACT_H
#include <windows.h>
/* Read-only management observations. No pointers, audio buffers or commands.
   One writer (FSA worker), readers retry a bounded seqlock snapshot. */
#define FSA_WEB_MAPPING L"Local\\NSP-FSA-Web-v1"
#define FSA_WEB_MAGIC 0x46535731u
typedef struct FSA_WebSnapshot {
    LONG sequence;
    DWORD magic,version,size,pid;
    LONG mode,available,locked,starting,nspCode;
    ULONGLONG observedMs;
    WCHAR endpoint[1024];
    WCHAR status[128];
} FSA_WebSnapshot;
#endif
