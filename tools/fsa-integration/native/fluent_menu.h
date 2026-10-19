#ifndef FSA_FLUENT_MENU_H
#define FSA_FLUENT_MENU_H
#include "language.h"
typedef struct { int selected,automatic,startup; unsigned available; FSA_Language language; int nsp_locked; } FSA_MenuState;
static inline int fsa_menu_checked(const FSA_MenuState *state,UINT command) {
    /* NSp is a feature layered on Sonic, not a competing endpoint format. */
    if(command==105)return state->selected==5 || state->selected==6;
    if(command==106)return state->selected==6;
    return (command>=100 && command<105 && (int)command-100==state->selected) ||
        (command==200 && state->automatic) || (command==207 && state->startup);
}
static inline int fsa_nsp_toggle_request(const FSA_MenuState *state) {return state->selected==6?2:1;}
UINT fsa_popup(HWND owner,const FSA_MenuState *state);
#endif
