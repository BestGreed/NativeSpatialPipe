// Generated PCM profiles: RSDS lookup, level gating and anchor values.
#include "pcm_profile.h"
#undef NDEBUG
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static unsigned hex_value(const char *text, int digits) {
    unsigned value = 0;
    for (int i = 0; i < digits; ++i) {
        char ch = text[i];
        value <<= 4;
        value |= ch >= '0' && ch <= '9' ? (unsigned)(ch - '0') : (unsigned)((ch | 32) - 'a' + 10);
    }
    return value;
}

// Build an in-memory PE image whose CodeView RSDS carries the profile's key.
static std::vector<BYTE> image_for_key(const char *key) {
    std::vector<BYTE> image(0x400, 0);
    image[0] = 'M';
    image[1] = 'Z';
    uint32_t pe_offset = 0x80;
    memcpy(image.data() + 0x3C, &pe_offset, sizeof(pe_offset));
    memcpy(image.data() + pe_offset, "PE\0\0", 4);
    auto *nt = reinterpret_cast<IMAGE_NT_HEADERS64 *>(image.data() + pe_offset);
    nt->Signature = IMAGE_NT_SIGNATURE;
    nt->FileHeader.Machine = IMAGE_FILE_MACHINE_AMD64;
    nt->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG].VirtualAddress = 0x200;
    nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG].Size = sizeof(IMAGE_DEBUG_DIRECTORY);
    auto *debug = reinterpret_cast<IMAGE_DEBUG_DIRECTORY *>(image.data() + 0x200);
    debug->Type = IMAGE_DEBUG_TYPE_CODEVIEW;
    debug->SizeOfData = 0x20;
    debug->AddressOfRawData = 0x240;
    BYTE *rsds = image.data() + 0x240;
    memcpy(rsds, "RSDS", 4);
    uint32_t d1 = hex_value(key, 8);
    uint16_t d2 = (uint16_t)hex_value(key + 8, 4);
    uint16_t d3 = (uint16_t)hex_value(key + 12, 4);
    memcpy(rsds + 4, &d1, 4);
    memcpy(rsds + 8, &d2, 2);
    memcpy(rsds + 10, &d3, 2);
    for (int i = 0; i < 8; ++i) rsds[12 + i] = (BYTE)hex_value(key + 16 + i * 2, 2);
    uint32_t age = strtoul(key + 32, nullptr, 16);
    memcpy(rsds + 20, &age, 4);
    return image;
}

int main() {
    const NspPcmProfile *anchor = nullptr, *disabled = nullptr;
    for (const auto &profile : NSP_PCM_PROFILES) {
        if (!anchor && profile.level == NSP_PCM_ANCHOR && profile.baseOffset == -0x1e0) anchor = &profile;
        if (!disabled && profile.level != NSP_PCM_ANCHOR) disabled = &profile;
    }
    assert(anchor);
    auto image = image_for_key(anchor->symbolKey);
    image.resize(anchor->getInputRva + sizeof(anchor->prologue));
    memcpy(image.data() + anchor->getInputRva, anchor->prologue, sizeof(anchor->prologue));
    auto read = [&](uint64_t offset, void *out, size_t n) {
        if (offset + n > image.size()) return false;
        memcpy(out, image.data() + offset, n);
        return true;
    };
    const NspPcmProfile *found = pcm_profile_select(read);
    assert(found == anchor);
    assert(found->getInputRva == anchor->getInputRva && found->inputVtableRva == anchor->inputVtableRva);
    assert(!memcmp(found->prologue, anchor->prologue, sizeof(found->prologue)));

    if (disabled) {
        auto disabled_image = image_for_key(disabled->symbolKey);
        auto read_disabled = [&](uint64_t offset, void *out, size_t n) {
            if (offset + n > disabled_image.size()) return false;
            memcpy(out, disabled_image.data() + offset, n);
            return true;
        };
        assert(!pcm_profile_select(read_disabled));
        const NspPcmProfile *known = pcm_profile_find(read_disabled);
        assert(known && known->level == disabled->level);
    }

    auto unknown = image_for_key("000000000000000000000000000000000");
    auto read_unknown = [&](uint64_t offset, void *out, size_t n) {
        if (offset + n > unknown.size()) return false;
        memcpy(out, unknown.data() + offset, n);
        return true;
    };
    assert(!pcm_profile_find(read_unknown));
    assert(!pcm_profile_fingerprint(read_unknown));

    const NspPcmProfile* fingerprint_anchor = nullptr;
    for (const auto& row : NSP_PCM_PROFILES) {
        if (row.level == NSP_PCM_ANCHOR && row.baseOffset == -0x1e0) {
            fingerprint_anchor = &row;
            break;
        }
    }
    if (fingerprint_anchor) {
        const auto offset = fingerprint_anchor->getInputRva;
        const auto size = sizeof(fingerprint_anchor->prologue);
        unknown.resize(offset + size);
        memcpy(unknown.data() + offset, fingerprint_anchor->prologue, size);
        const auto* fallback = pcm_profile_fingerprint(read_unknown);
        assert(fallback && !strcmp(fallback->layout, fingerprint_anchor->layout));
        assert(pcm_profile_matches(*fallback, read_unknown));
        PcmSymbolKey unknown_key{};
        assert(pcm_symbol_key(read_unknown, unknown_key));
        bool fingerprint = false;
        const auto miss = [](const char*) -> const NspPcmProfile* { return nullptr; };
        const auto hit = [&](const char*) { return fallback; };
        assert(pcm_profile_resolve(unknown_key, read_unknown, miss, fingerprint) == fallback && fingerprint);
        assert(pcm_profile_resolve(unknown_key, read_unknown, hit, fingerprint) == fallback && !fingerprint);
        if (disabled) {
            PcmSymbolKey disabled_key{};
            strcpy(disabled_key, disabled->symbolKey);
            assert(!pcm_profile_resolve(disabled_key, read_unknown, hit, fingerprint) && !fingerprint);
        }
        unknown[offset] ^= 1;
        assert(!pcm_profile_resolve(unknown_key, read_unknown, hit, fingerprint) && !fingerprint);
    }

    std::vector<BYTE> truncated(0x10, 0);
    auto read_truncated = [&](uint64_t offset, void *out, size_t n) {
        if (offset + n > truncated.size()) return false;
        memcpy(out, truncated.data() + offset, n);
        return true;
    };
    assert(!pcm_profile_find(read_truncated));
    puts("PASS PCM profile: exact lookup, fingerprint fallback and rejection");
    return 0;
}
