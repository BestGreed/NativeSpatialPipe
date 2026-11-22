/* Read-only capture-layout state for the tray menu.
 *
 * The menu must answer "does this Windows build have a capture layout?" without
 * starting a helper process, so this header reads three files directly: the
 * on-disk audiodg.exe RSDS, the cached single profile and the shipped table's
 * member index. Explicit menu actions are the only things that spawn the
 * scheduler, which owns the selector and the status sidecar.
 *
 * Everything here is best-effort: an unreadable file means "unknown", never an
 * error dialog or a startup delay. All helpers are inline so the header can be
 * included by tests without unused-function warnings.
 */
#ifndef FSA_CAPTURE_PROFILE_H
#define FSA_CAPTURE_PROFILE_H
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FSA_CP_READABLE 1u   /* the disk image RSDS could be read */
#define FSA_CP_AVAILABLE 2u  /* table member exists or the cache already matches */
#define FSA_CP_READY 4u      /* cached profile identity equals the disk image */
#define FSA_CP_BUSY 8u       /* an explicit menu action is running */

#define FSA_CP_KEY_CHARS 40
#define FSA_CP_LAYOUT_CHARS 64

typedef struct {
    unsigned state;
    wchar_t disk[FSA_CP_KEY_CHARS];
    wchar_t cache[FSA_CP_KEY_CHARS];
    wchar_t layout[FSA_CP_LAYOUT_CHARS];
    wchar_t table[MAX_PATH];
} FSA_CaptureProfile;

static inline int fsa_cp_read_file(const wchar_t *path, BYTE **data, DWORD *size) {
    *data = NULL;
    *size = 0;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    LARGE_INTEGER length;
    if (!GetFileSizeEx(file, &length) || length.QuadPart <= 0 || length.QuadPart > 64 * 1024 * 1024) {
        CloseHandle(file);
        return 0;
    }
    BYTE *buffer = (BYTE *)malloc((size_t)length.QuadPart);
    if (!buffer) {
        CloseHandle(file);
        return 0;
    }
    DWORD read = 0;
    int ok = ReadFile(file, buffer, (DWORD)length.QuadPart, &read, NULL) && read == (DWORD)length.QuadPart;
    CloseHandle(file);
    if (!ok) {
        free(buffer);
        return 0;
    }
    *data = buffer;
    *size = read;
    return 1;
}

static inline DWORD fsa_cp_rva_offset(const IMAGE_NT_HEADERS64 *nt, DWORD rva) {
    const IMAGE_SECTION_HEADER *section = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections; i++, section++) {
        DWORD span = section->Misc.VirtualSize > section->SizeOfRawData ? section->Misc.VirtualSize : section->SizeOfRawData;
        if (rva >= section->VirtualAddress && rva < section->VirtualAddress + span)
            return section->PointerToRawData + (rva - section->VirtualAddress);
    }
    return 0;
}

/* Read the CodeView RSDS record of a PE image on disk as GUID+age, the symbol
 * key used by the table. The image is not mapped, so RVAs go through the section
 * table instead of being added to the module base. */
static inline int fsa_cp_image_key(const wchar_t *image, wchar_t out[FSA_CP_KEY_CHARS]) {
    BYTE *data = NULL;
    DWORD size = 0;
    out[0] = 0;
    if (!fsa_cp_read_file(image, &data, &size)) return 0;
    int ok = 0;
    if (size >= sizeof(IMAGE_DOS_HEADER)) {
        const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)data;
        if (dos->e_magic == IMAGE_DOS_SIGNATURE && dos->e_lfanew > 0 &&
            (DWORD)dos->e_lfanew + sizeof(IMAGE_NT_HEADERS64) <= size) {
            const IMAGE_NT_HEADERS64 *nt = (const IMAGE_NT_HEADERS64 *)(data + dos->e_lfanew);
            if (nt->Signature == IMAGE_NT_SIGNATURE && nt->OptionalHeader.Magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
                IMAGE_DATA_DIRECTORY debug = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG];
                DWORD at = debug.VirtualAddress ? fsa_cp_rva_offset(nt, debug.VirtualAddress) : 0;
                if (at && at + sizeof(IMAGE_DEBUG_DIRECTORY) <= size) {
                    const IMAGE_DEBUG_DIRECTORY *entry = (const IMAGE_DEBUG_DIRECTORY *)(data + at);
                    if (entry->Type == IMAGE_DEBUG_TYPE_CODEVIEW && entry->SizeOfData >= 0x18 &&
                        entry->PointerToRawData + entry->SizeOfData <= size) {
                        const BYTE *rsds = data + entry->PointerToRawData;
                        if (!memcmp(rsds, "RSDS", 4)) {
                            DWORD d1, age;
                            WORD d2, d3;
                            memcpy(&d1, rsds + 4, 4);
                            memcpy(&d2, rsds + 8, 2);
                            memcpy(&d3, rsds + 10, 2);
                            memcpy(&age, rsds + 20, 4);
                            swprintf(out, FSA_CP_KEY_CHARS, L"%08lX%04X%04X", (unsigned long)d1,
                                     (unsigned)d2, (unsigned)d3);
                            for (int i = 0; i < 8; i++)
                                swprintf(out + 16 + i * 2, FSA_CP_KEY_CHARS - 16 - i * 2, L"%02X", rsds[12 + i]);
                            swprintf(out + 32, 8, L"%lX", (unsigned long)age);
                            ok = out[0] != 0;
                        }
                    }
                }
            }
        }
    }
    free(data);
    return ok;
}

/* Find "<key>": "value" in the small generated JSON files and copy the value.
 * The selector only writes ASCII hex, layout ids and numbers here; a plain
 * backslash escape is all the decoding this needs. */
static inline int fsa_cp_json_value(const char *text, size_t size, const char *key,
                                    wchar_t *out, size_t capacity) {
    char pattern[64];
    int used = snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    if (used <= 0 || (size_t)used >= sizeof(pattern) || capacity == 0) return 0;
    out[0] = 0;
    const char *found = NULL;
    for (size_t i = 0; i + (size_t)used <= size; i++)
        if (!memcmp(text + i, pattern, (size_t)used)) {
            found = text + i + used;
            break;
        }
    if (!found) return 0;
    const char *end = text + size;
    while (found < end && (*found == ' ' || *found == '\t' || *found == '\r' || *found == '\n')) found++;
    if (found >= end || *found != ':') return 0;
    found++;
    while (found < end && (*found == ' ' || *found == '\t' || *found == '\r' || *found == '\n')) found++;
    if (found >= end || *found != '"') return 0;
    found++;
    size_t count = 0;
    while (found < end && *found != '"') {
        char ch = *found++;
        if (ch == '\\') {
            if (found >= end) return 0;
            ch = *found++;
        }
        if (count + 1 >= capacity) return 0;
        out[count++] = (wchar_t)(unsigned char)ch;
    }
    out[count] = 0;
    return count > 0;
}

static inline int fsa_cp_widen(const wchar_t *source, char *out, size_t capacity) {
    int used = WideCharToMultiByte(CP_UTF8, 0, source, -1, out, (int)capacity, NULL, NULL);
    return used > 0 && (size_t)used <= capacity;
}

static inline int fsa_cp_cache_identity(const wchar_t *path, wchar_t key[FSA_CP_KEY_CHARS],
                                        wchar_t layout[FSA_CP_LAYOUT_CHARS]) {
    BYTE *data = NULL;
    DWORD size = 0;
    key[0] = 0;
    layout[0] = 0;
    if (!fsa_cp_read_file(path, &data, &size)) return 0;
    int ok = fsa_cp_json_value((const char *)data, size, "symbolKey", key, FSA_CP_KEY_CHARS) &&
             fsa_cp_json_value((const char *)data, size, "layout", layout, FSA_CP_LAYOUT_CHARS);
    free(data);
    return ok;
}

static inline int fsa_cp_table_member(const wchar_t *path, const wchar_t *key,
                                      wchar_t layout[FSA_CP_LAYOUT_CHARS]) {
    BYTE *data = NULL;
    DWORD size = 0;
    layout[0] = 0;
    char ascii[FSA_CP_KEY_CHARS];
    if (!fsa_cp_widen(key, ascii, sizeof(ascii))) return 0;
    if (!fsa_cp_read_file(path, &data, &size)) return 0;
    int ok = fsa_cp_json_value((const char *)data, size, ascii, layout, FSA_CP_LAYOUT_CHARS);
    free(data);
    return ok;
}

/* Fill the menu state from the image, cache and ordered table candidates. */
static inline void fsa_cp_compute(FSA_CaptureProfile *profile, const wchar_t *image,
                                  const wchar_t *cache, const wchar_t *const *tables, int table_count) {
    memset(profile, 0, sizeof(*profile));
    if (!fsa_cp_image_key(image, profile->disk)) return;
    profile->state |= FSA_CP_READABLE;
    wchar_t key[FSA_CP_KEY_CHARS], layout[FSA_CP_LAYOUT_CHARS];
    if (fsa_cp_cache_identity(cache, key, layout)) {
        wcscpy(profile->cache, key);
        if (!wcscmp(profile->disk, key)) {
            /* The cache is already about the running build: nothing to resolve. */
            profile->state |= FSA_CP_READY | FSA_CP_AVAILABLE;
            wcscpy(profile->layout, layout);
            return;
        }
    }
    for (int i = 0; i < table_count; i++) {
        if (tables[i] && fsa_cp_table_member(tables[i], profile->disk, layout)) {
            profile->state |= FSA_CP_AVAILABLE;
            wcscpy(profile->layout, layout);
            wcscpy(profile->table, tables[i]);
            return;
        }
    }
}
#endif
