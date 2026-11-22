// Select a generated PCM profile against the running audiodg image.
#pragma once
#include "profile.generated.h"
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>

static_assert(NSP_PCM_PROFILE_COUNT > 0, "generated PCM table is empty");
using PcmSymbolKey = char[41];

// `read(offset, out, size)` reads `size` bytes at `offset` from the image base.
template <class Read>
static bool pcm_symbol_key(Read&& read, PcmSymbolKey& key) {
    IMAGE_DOS_HEADER dos{};
    if (!read(0, &dos, sizeof(dos)) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0)
        return false;
    IMAGE_NT_HEADERS64 nt{};
    if (!read(static_cast<uint64_t>(dos.e_lfanew), &nt, sizeof(nt)) ||
        nt.Signature != IMAGE_NT_SIGNATURE ||
        nt.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC)
        return false;
    IMAGE_DATA_DIRECTORY dir = nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG];
    if (!dir.VirtualAddress || dir.Size < sizeof(IMAGE_DEBUG_DIRECTORY)) return false;
    IMAGE_DEBUG_DIRECTORY entry{};
    if (!read(dir.VirtualAddress, &entry, sizeof(entry)) ||
        entry.Type != IMAGE_DEBUG_TYPE_CODEVIEW || entry.SizeOfData < 0x18 ||
        !entry.AddressOfRawData)
        return false;
    BYTE rsds[0x18]{};
    if (!read(entry.AddressOfRawData, rsds, sizeof(rsds)) || memcmp(rsds, "RSDS", 4)) return false;
    DWORD d1 = 0, age = 0;
    WORD d2 = 0, d3 = 0;
    memcpy(&d1, rsds + 4, 4);
    memcpy(&d2, rsds + 8, 2);
    memcpy(&d3, rsds + 10, 2);
    memcpy(&age, rsds + 20, 4);
    int used = snprintf(key, sizeof(key), "%08lX%04X%04X", (unsigned long)d1,
                        (unsigned)d2, (unsigned)d3);
    if (used != 16) return false;
    for (int i = 0; i < 8; ++i) snprintf(key + 16 + i * 2, 3, "%02X", rsds[12 + i]);
    snprintf(key + 32, sizeof(key) - 32, "%lX", (unsigned long)age);
    return true;
}

static inline const NspPcmProfile* pcm_profile_for_key(const char* key) {
    for (const auto& profile : NSP_PCM_PROFILES)
        if (!strcmp(profile.symbolKey, key)) return &profile;
    return nullptr;
}

template <class Read>
static const NspPcmProfile* pcm_profile_find(Read&& read) {
    PcmSymbolKey key{};
    return pcm_symbol_key(read, key) ? pcm_profile_for_key(key) : nullptr;
}

template <class Read>
static bool pcm_profile_matches(const NspPcmProfile& row, Read&& read) {
    BYTE bytes[sizeof(row.prologue)]{};
    // QueueProbe currently implements the 26100 object contract.
    return row.level == NSP_PCM_ANCHOR && row.baseOffset == -0x1e0 &&
        read(row.getInputRva, bytes, sizeof(bytes)) && !memcmp(bytes, row.prologue, sizeof(bytes));
}

// Several RSDS members can share one layout. Distinct matching layouts are
// ambiguous even when their entrance bytes happen to look identical.
template <class Read>
static const NspPcmProfile* pcm_profile_fingerprint(Read&& read) {
    const NspPcmProfile* selected = nullptr;
    for (const auto& row : NSP_PCM_PROFILES) {
        if (!pcm_profile_matches(row, read)) continue;
        if (selected && strcmp(selected->layout, row.layout)) return nullptr;
        selected = &row;
    }
    return selected;
}

template <class Read, class CacheLookup>
static const NspPcmProfile* pcm_profile_resolve(const PcmSymbolKey& key, Read&& read,
                                               CacheLookup&& lookup, bool& fingerprint) {
    fingerprint = false;
    if (const auto* exact = pcm_profile_for_key(key))
        return pcm_profile_matches(*exact, read) ? exact : nullptr;
    if (const auto* cached = lookup(key); cached && pcm_profile_matches(*cached, read)) return cached;
    const auto* inferred = pcm_profile_fingerprint(read);
    fingerprint = inferred != nullptr;
    return inferred;
}

// Only `anchor` rows may drive the PCM path; `address` (no prologue) and
// `unavailable` rows are kept for diagnostics but refuse the path.
template <class Read>
static const NspPcmProfile* pcm_profile_select(Read&& read) {
    const NspPcmProfile* profile = pcm_profile_find(read);
    return profile && pcm_profile_matches(*profile, read) ? profile : nullptr;
}
