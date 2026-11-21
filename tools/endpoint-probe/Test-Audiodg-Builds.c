/* Contract tests for the generated audiodg build table. The native controller
   compiles against this same header, so these cases pin its selection and
   fail-closed behaviour: known full-file SHA-256 -> runtime RVAs, unknown SHA
   -> message with actual digest and supported list, wrong memory -> reject. */
#include "Audiodg-Builds.h"
#include <stdio.h>
#include <string.h>

static int failures;

static void check(int condition, const char *name) {
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", name);
        failures++;
    } else {
        printf("ok: %s\n", name);
    }
}

static int digest_from_hex(const char *hex, unsigned char digest[32]) {
    size_t index;
    if (strlen(hex) != 64) return 0;
    for (index = 0; index < 32; index++) {
        unsigned int value;
        if (sscanf(hex + index * 2, "%2x", &value) != 1) return 0;
        digest[index] = (unsigned char)value;
    }
    return 1;
}

int main(void) {
    static const struct { const char *sha256; const char *id; uint32_t target_rva, vt1_rva, vt2_rva; } known[] = {
        { "3dd24fe6faf0b56f99d5b00f24687b5f3b3e76e15b219e23f0963114a0dce9a5", "win-26100-9444",  0x31930u, 0x9f4f8u, 0xa2d58u },
        { "017628fc0ace806163950bb3019709ca3820531d7f19c1103429e09b4c54f6ca", "win-26100-9974",  0x32c40u, 0x9e4e8u, 0xa1be0u },
        { "e55b5365056273c133954ca7c44b8d4f1c3139a4b3fa766a94ffe58c2621a8b3", "win-26100-10068", 0x32c40u, 0x9e4e8u, 0xa1c20u },
    };
    unsigned char digest[32], actual[16];
    char message[1400];
    const NspAudiodgBuild *profile;
    size_t index;

    for (index = 0; index < sizeof(known) / sizeof(known[0]); index++) {
        profile = NULL;
        check(digest_from_hex(known[index].sha256, digest), "known digest parses");
        profile = nsp_audiodg_match(digest);
        check(profile != NULL, known[index].id);
        if (!profile) continue;
        check(!strcmp(profile->id, known[index].id), "profile id matches selected SHA-256");
        check(profile->target_rva == known[index].target_rva, "target RVA matches table");
        check(profile->target_size == 474u, "target size matches table");
        check(profile->vt1_rva == known[index].vt1_rva, "primary vtable matches table");
        check(profile->vt2_rva == known[index].vt2_rva, "secondary vtable matches table");
    }

    memset(digest, 0xab, sizeof(digest));
    check(nsp_audiodg_match(digest) == NULL, "unknown SHA-256 is rejected");
    nsp_audiodg_unsupported_message(digest, message, sizeof(message));
    check(strstr(message, "Unsupported audiodg build") != NULL, "unsupported message names the failure");
    check(strstr(message, "abababab") != NULL, "unsupported message carries the actual SHA-256");
    check(strstr(message, "3dd24fe6") != NULL, "unsupported message lists the 9444 build");
    check(strstr(message, "017628fc") != NULL, "unsupported message lists the 9974 build");
    check(strstr(message, "e55b5365") != NULL, "unsupported message lists the 10068 build");

    digest_from_hex(known[1].sha256, digest);
    profile = nsp_audiodg_match(digest);
    memcpy(actual, profile->prologue, 16);
    check(nsp_audiodg_prologue_ok(actual, profile->prologue) == 1, "matching prologue is accepted");
    actual[15] ^= 0xff;
    check(nsp_audiodg_prologue_ok(actual, profile->prologue) == 0, "different prologue is rejected");
    check(nsp_audiodg_prologue_ok(NULL, profile->prologue) == 0, "missing target memory is rejected");

    check(nsp_audiodg_config_matches(profile, digest, profile->target_rva, profile->target_size,
                                     profile->vt1_rva, profile->vt2_rva, profile->prologue) == 1,
          "matching HookConfig profile is accepted");
    check(nsp_audiodg_config_matches(profile, digest, profile->target_rva + 8, profile->target_size,
                                     profile->vt1_rva, profile->vt2_rva, profile->prologue) == 0,
          "wrong target RVA is rejected");
    check(nsp_audiodg_config_matches(profile, digest, profile->target_rva, profile->target_size,
                                     profile->vt1_rva, profile->vt2_rva + 0x40, profile->prologue) == 0,
          "wrong secondary vtable is rejected");
    memcpy(actual, profile->prologue, 16);
    actual[0] ^= 0xff;
    check(nsp_audiodg_config_matches(profile, digest, profile->target_rva, profile->target_size,
                                     profile->vt1_rva, profile->vt2_rva, actual) == 0,
          "wrong prologue is rejected");
    memset(actual, 0, 16);
    check(nsp_audiodg_config_matches(NULL, digest, profile->target_rva, profile->target_size,
                                     profile->vt1_rva, profile->vt2_rva, profile->prologue) == 0,
          "missing selected profile is rejected");

    if (failures) printf("%d failure(s)\n", failures);
    else printf("all audiodg build-profile table checks passed\n");
    return failures ? 1 : 0;
}
