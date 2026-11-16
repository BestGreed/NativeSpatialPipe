#ifndef FSA_FLUENT_MENU_H
#define FSA_FLUENT_MENU_H
#include "language.h"
typedef struct { int selected,automatic,startup; unsigned available; FSA_Language language; int nsp_locked; unsigned extreme; int frontend; unsigned edid; } FSA_MenuState;
#define FSA_EDID_ELIGIBLE 1u
#define FSA_EDID_SUPPORTED 2u
#define FSA_EDID_BUSY 4u
#define FSA_MENU_ROUTE_LOCK 8u
#define FSA_MENU_LOCK_PENDING 16u
static inline const wchar_t *fsa_locked_nsp_label(int frontend){
    static const wchar_t *labels[]={L"Sonic NSp",L"Dolby Atmos B NSp",L"Dolby Atmos L NSp",L"Dolby Atmos HT NSp",L"DTS:X B NSp",L"DTS:X L NSp",L"DTS:X HT NSp"};
    return frontend>=0 && frontend<7?labels[frontend]:L"NSp";
}
static inline int fsa_menu_checked(const FSA_MenuState *state,UINT command) {
    if(command==106)return state->selected==6;
    if(command==311)return (state->extreme&FSA_MENU_ROUTE_LOCK)!=0;
    if(command==414)return (state->edid&FSA_EDID_SUPPORTED)!=0;
    if(command>=410 && command<=412)return (state->extreme&(1u<<(command-410)))!=0;
    int active=state->selected;
    if(active==6 && state->frontend>=0 && state->frontend<7){const int modes[]={5,7,8,3,9,10,4};active=modes[state->frontend];}
    return (command>=100 && command<111 && (int)command-100==active) ||
        (command==200 && state->automatic) || (command==207 && state->startup);
}
static inline int fsa_nsp_toggle_request(const FSA_MenuState *state) {return state->nsp_locked || state->selected==6?2:1;}
UINT fsa_popup(HWND owner,const FSA_MenuState *state);
#define FSA_WM_MENU_ACTION (WM_APP+8)
void fsa_popup_update(const FSA_MenuState *state);
#endif
