#ifndef FSA_FLUENT_MENU_H
#define FSA_FLUENT_MENU_H
#include "language.h"
typedef struct { int selected,automatic,startup; unsigned available; FSA_Language language; int nsp_locked; } FSA_MenuState;
UINT fsa_popup(HWND owner,const FSA_MenuState *state);
#endif
