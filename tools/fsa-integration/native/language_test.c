#include "language.h"
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
int main(void) {
    LANGID chinese[]={0x0804,0x0404,0x0c04,0x1004,0x1404};
    LANGID other[]={0x0409,0x0809,0x0411,0x0407,0x040c,0};
    for(unsigned i=0;i<sizeof(chinese)/sizeof(*chinese);i++) {
        assert(fsa_select_language(L"auto",chinese[i])==FSA_ZH);
        assert(fsa_select_language(L"EN",chinese[i])==FSA_EN);
    }
    for(unsigned i=0;i<sizeof(other)/sizeof(*other);i++) {
        assert(fsa_select_language(L"auto",other[i])==FSA_EN);
        assert(fsa_select_language(L"ZH",other[i])==FSA_ZH);
    }
    assert(fsa_select_language(NULL,0x0804)==FSA_ZH);
    assert(fsa_select_language(L"invalid",0x0409)==FSA_EN);
    assert(fsa_select_language(L"",0x0404)==FSA_ZH);
    assert(!wcscmp(fsa_text(FSA_EN,TXT_MANUAL),L"Manual lock"));
    assert(!wcscmp(fsa_text(FSA_ZH,TXT_ADVANCED),L"高级"));
    for(int i=0;i<TXT_COUNT;i++) {
        assert(*fsa_text(FSA_ZH,(FSA_Text)i));
        const wchar_t *en=fsa_text(FSA_EN,(FSA_Text)i);
        assert(*en);
        for(;*en;en++)assert(*en<128);
    }
    /* Match the popup's font, left text inset and right padding at common DPIs. */
    HDC dc=CreateCompatibleDC(NULL);assert(dc);
    int dpis[]={96,120,144,192};
    for(unsigned d=0;d<sizeof(dpis)/sizeof(*dpis);d++) {
        HFONT font=CreateFontW(-MulDiv(14,dpis[d],96),0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Segoe UI");assert(font);
        HGDIOBJ old=SelectObject(dc,font);
        for(int i=0;i<=TXT_STARTUP;i++) {
            const wchar_t *label=fsa_text(FSA_EN,(FSA_Text)i);SIZE size;
            assert(GetTextExtentPoint32W(dc,label,(int)wcslen(label),&size));
            assert(size.cx<=MulDiv(200-44-8,dpis[d],96));
        }
        SelectObject(dc,old);DeleteObject(font);
    }
    DeleteDC(dc);
    puts("PASS language detection, overrides, fallback, translation coverage and English menu widths at 4 DPIs");
    return 0;
}
