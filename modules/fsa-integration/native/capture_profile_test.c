/* State-only checks for the capture-layout menu: synthetic PE/JSON fixtures next
 * to the test binary, no windows, no audio endpoints, no helper process. */
#include "capture_profile.h"
#undef NDEBUG
#include <assert.h>
#include <stdio.h>

static wchar_t work[MAX_PATH];
static wchar_t prefix[64];

static int setup(void) {
    wchar_t self[MAX_PATH];
    if (!GetModuleFileNameW(NULL, self, MAX_PATH)) {
        fprintf(stderr, "SKIP capture-layout state: no module path (err=%lu)\n",
                (unsigned long)GetLastError());
        return 0;
    }
    wchar_t *slash = wcsrchr(self, L'\\');
    if (!slash) return 0;
    *slash = 0;
    wcscpy(work, self);
    swprintf(prefix, 64, L"cap-%lu-%llu-", (unsigned long)GetCurrentProcessId(), GetTickCount64());
    return 1;
}

static void path_of(const wchar_t *name, wchar_t *out) {
    swprintf(out, MAX_PATH, L"%ls\\%ls%ls", work, prefix, name);
}

static int write_file(const wchar_t *path, const void *data, DWORD size) {
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD written = 0;
    int ok = WriteFile(file, data, size, &written, NULL) && written == size;
    CloseHandle(file);
    return ok;
}

static size_t build_pe(BYTE *pe, size_t capacity) {
    memset(pe, 0, capacity);
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)pe;
    dos->e_magic = IMAGE_DOS_SIGNATURE;
    dos->e_lfanew = 0x80;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(pe + 0x80);
    nt->Signature = IMAGE_NT_SIGNATURE;
    nt->FileHeader.Machine = IMAGE_FILE_MACHINE_AMD64;
    nt->FileHeader.NumberOfSections = 1;
    nt->FileHeader.SizeOfOptionalHeader = sizeof(IMAGE_OPTIONAL_HEADER64);
    nt->OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR64_MAGIC;
    nt->OptionalHeader.NumberOfRvaAndSizes = 16;
    IMAGE_SECTION_HEADER *section = IMAGE_FIRST_SECTION(nt);
    memcpy(section->Name, ".rdata", 6);
    section->VirtualAddress = 0x1000;
    section->Misc.VirtualSize = 0x400;
    section->PointerToRawData = 0x400;
    section->SizeOfRawData = 0x400;
    nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG].VirtualAddress = 0x1000;
    nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG].Size = sizeof(IMAGE_DEBUG_DIRECTORY);
    IMAGE_DEBUG_DIRECTORY *debug = (IMAGE_DEBUG_DIRECTORY *)(pe + 0x400);
    debug->Type = IMAGE_DEBUG_TYPE_CODEVIEW;
    debug->SizeOfData = 0x20;
    debug->PointerToRawData = 0x420;
    BYTE *rsds = pe + 0x420;
    memcpy(rsds, "RSDS", 4);
    const BYTE guid[16] = {0xC4, 0xC2, 0x2D, 0x88, 0x1A, 0x10, 0x7A, 0x0C,
                           0x60, 0x08, 0x52, 0x8B, 0x0B, 0xB7, 0x3A, 0xED};
    memcpy(rsds + 4, guid, 16);
    const DWORD age = 1;
    memcpy(rsds + 20, &age, 4);
    return 0x440;
}

static const char *table_json =
    "{\n"
    "  \"layouts\": {\"L31930_9F4F8\": {\"capture\": {\"entryRva\": 203056}}},\n"
    "  \"members\": {\"882DC2C4101A0C7A6008528B0BB73AED1\": \"L31930_9F4F8\"}\n"
    "}\n";
static const char *cache_json =
    "{\n  \"layout\": \"L31930_9F4F8\",\n  "
    "\"symbolKey\": \"882DC2C4101A0C7A6008528B0BB73AED1\",\n  \"schema\": 1\n}\n";
static const char *stale_cache_json =
    "{\n  \"layout\": \"L6D650_76870\",\n  "
    "\"symbolKey\": \"D08CEAC0E33AD1959564D4192BE43BA51\",\n  \"schema\": 1\n}\n";

int main(void) {
    assert(setup());
    BYTE pe[0x500];
    size_t pe_size = build_pe(pe, sizeof(pe));
    wchar_t image[MAX_PATH], table[MAX_PATH], cache[MAX_PATH], stale[MAX_PATH];
    path_of(L"audiodg.exe", image);
    path_of(L"capture-profiles.json", table);
    path_of(L"capture-profile.json", cache);
    path_of(L"stale.json", stale);
    assert(write_file(image, pe, (DWORD)pe_size));
    assert(write_file(table, table_json, (DWORD)strlen(table_json)));
    assert(write_file(cache, cache_json, (DWORD)strlen(cache_json)));
    assert(write_file(stale, stale_cache_json, (DWORD)strlen(stale_cache_json)));

    wchar_t key[FSA_CP_KEY_CHARS];
    assert(fsa_cp_image_key(image, key));
    assert(!wcscmp(key, L"882DC2C4101A0C7A6008528B0BB73AED1"));
    wchar_t layout[FSA_CP_LAYOUT_CHARS];
    assert(fsa_cp_table_member(table, key, layout));
    assert(!wcscmp(layout, L"L31930_9F4F8"));
    assert(!fsa_cp_table_member(table, L"F531DD3F03E3E55E2F2C422E353AFECC1", layout));

    wchar_t cache_key[FSA_CP_KEY_CHARS], cache_layout[FSA_CP_LAYOUT_CHARS];
    assert(fsa_cp_cache_identity(cache, cache_key, cache_layout));
    assert(!wcscmp(cache_key, key) && !wcscmp(cache_layout, L"L31930_9F4F8"));
    assert(fsa_cp_cache_identity(stale, cache_key, cache_layout) &&
           wcscmp(cache_key, key));

    const wchar_t *tables[] = {table};
    FSA_CaptureProfile profile;
    fsa_cp_compute(&profile, image, cache, tables, 1);
    assert((profile.state & (FSA_CP_READABLE | FSA_CP_READY | FSA_CP_AVAILABLE)) ==
           (FSA_CP_READABLE | FSA_CP_READY | FSA_CP_AVAILABLE));
    assert(!wcscmp(profile.layout, L"L31930_9F4F8"));

    fsa_cp_compute(&profile, image, stale, tables, 1);
    assert((profile.state & (FSA_CP_READABLE | FSA_CP_AVAILABLE)) ==
           (FSA_CP_READABLE | FSA_CP_AVAILABLE));
    assert(!(profile.state & FSA_CP_READY));
    assert(!wcscmp(profile.layout, L"L31930_9F4F8"));
    assert(!wcscmp(profile.table, table));

    fsa_cp_compute(&profile, image, stale, NULL, 0);
    assert(profile.state == FSA_CP_READABLE);

    wchar_t missing[MAX_PATH];
    path_of(L"missing.exe", missing);
    fsa_cp_compute(&profile, missing, cache, tables, 1);
    assert(profile.state == 0);

    DeleteFileW(image);
    DeleteFileW(table);
    DeleteFileW(cache);
    DeleteFileW(stale);
    puts("PASS capture-layout state: disk RSDS, table member, cache identity, ready/available/unknown");
    return 0;
}
