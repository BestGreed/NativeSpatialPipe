#pragma once
/* Capture layout contract shared by the injected hook, the capture library and
   the controller. Every value here is supplied by a resolved profile; nothing in
   the capture path may carry a build-specific constant any more. A missing or
   unknown layout is a hard refusal: the hook never guesses. */
#include <stdint.h>

#define NSP_LAYOUT_VERSION 1u
#define NSP_LAYOUT_VTABLES 4u
#define NSP_LAYOUT_PROLOGUE 32u

/* Refusal codes surfaced through HookConfig.error. Kept out of the historical
   1..203 range so existing diagnostics stay meaningful. */
#define NSP_HOOK_ERR_PROFILE_MISSING 40u  /* layout.version != NSP_LAYOUT_VERSION */
#define NSP_HOOK_ERR_PROFILE_INVALID 41u  /* structurally implausible layout */
#define NSP_HOOK_ERR_ANCHOR_MISMATCH 42u  /* prologue at entryRva does not match */
#define NSP_HOOK_ERR_BLOCK_NOT_FOUND 43u  /* no vtable-bearing object near the endpoint */
#define NSP_HOOK_ERR_SYMBOL_KEY 44u      /* running image RSDS differs from the profile */

typedef struct {
    uint32_t version;                 /* NSP_LAYOUT_VERSION */
    uint32_t entryRva;                /* GetInputDataPointer RVA in audiodg */
    uint32_t prologueSize;            /* bytes meaningful in prologue[] (<= 32) */
    uint32_t vtableCount;             /* entries meaningful in vtableRva[] (<= 4) */
    uint8_t  prologue[NSP_LAYOUT_PROLOGUE];
    char     symbolKey[34];  /* GUID+age of the build this layout was resolved for */
    uint32_t reserved0;
    uint64_t vtableRva[NSP_LAYOUT_VTABLES];  /* vtable RVAs, not function pointers */

    /* Block discovery: scan [blockSearchStart, blockSearchEnd] around the endpoint
       for the first qword equal to one of vtableRva[]. Ends are signed offsets. */
    int32_t  blockSearchStart;
    int32_t  blockSearchEnd;
    uint32_t blockSearchStep;
    uint32_t maxBufferIndex;          /* reject index > this */

    /* Descriptor field offsets, relative to the block pointer. */
    uint32_t flagOffset;              /* read-window flag byte */
    uint32_t countOffset;             /* object count, uint32 */
    uint32_t indexOffset;             /* current buffer index, uint32 */
    uint32_t descPtrOffset;           /* descriptor pointer, uintptr */
    uint32_t descPropSizeOffset;      /* descriptor property size, uint32 */
    uint32_t descStrideOffset;        /* descriptor sample stride, uint32 */
    uint32_t propsBase;               /* descriptor -> properties array base */
    uint32_t pcmBase;                 /* descriptor -> pcm array base */
    uint32_t propStride;              /* per-buffer stride inside those arrays */
    uint32_t frameCountOffset;        /* frame count inside one property entry */
} NspCaptureLayout;

static inline int nsp_layout_usable(const NspCaptureLayout *l) {
    if (!l || l->version != NSP_LAYOUT_VERSION) return 0;
    /* prologueSize == 0 selects bootstrap: the anchor is unknown for this build,
       so the first-packet canary becomes the only semantic gate. A profile that
       carries a prologue must still match it exactly (error 42). */
    if (l->prologueSize > NSP_LAYOUT_PROLOGUE) return 0;
    if (l->vtableCount == 0 || l->vtableCount > NSP_LAYOUT_VTABLES) return 0;
    if (l->blockSearchStep != 4 && l->blockSearchStep != 8) return 0;
    if (l->blockSearchStart >= l->blockSearchEnd) return 0;
    if (l->blockSearchEnd - l->blockSearchStart > 0x400) return 0;
    if (l->countOffset == l->indexOffset || l->propsBase == l->pcmBase) return 0;
    if (l->propStride == 0 || l->propStride > 0x80) return 0;
    return 1;
}
