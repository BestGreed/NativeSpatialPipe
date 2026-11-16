/* Logic-only regression; no windows, audio endpoints, EDID or driver changes. */
#include "fluent_menu.c"
#include <assert.h>
static int row(UINT command){for(unsigned i=0;i<sizeof(main_commands)/sizeof(*main_commands);i++)if(main_commands[i]==command)return (int)i;return -1;}
static void check_ht(FSA_MenuState *s,UINT command,int visible){
    int i=row(command);assert(i>=0);
    assert(extra_visible(s,command)==visible);
    assert(((hidden_rows(s)&(1u<<i))==0)==visible);
    Popup p={0};p.state=*s;p.hidden=hidden_rows(s);
    assert(enabled(&p,i)==visible);
}
int main(void){
    Popup menu={0};menu.advanced=2;
    assert(!wcscmp(label_at(&menu,1),L"Enum OLR") && command_at(&menu,1)==412);
    assert(!wcscmp(label_at(&menu,2),L"Enum OBR") && command_at(&menu,2)==411);
    menu.state.extreme=4;
    assert(fsa_menu_checked(&menu.state,command_at(&menu,1)));
    assert(!fsa_menu_checked(&menu.state,command_at(&menu,2)));
    menu.state.extreme=2;
    assert(!fsa_menu_checked(&menu.state,command_at(&menu,1)));
    assert(fsa_menu_checked(&menu.state,command_at(&menu,2)));
    assert(count(&menu)==5 && command_at(&menu,4)==414);
    menu.state.edid=0;assert(!enabled(&menu,4) && !fsa_menu_checked(&menu.state,414));
    menu.state.edid=FSA_EDID_SUPPORTED;assert(!enabled(&menu,4) && fsa_menu_checked(&menu.state,414));
    menu.state.edid=FSA_EDID_ELIGIBLE;assert(enabled(&menu,4) && !fsa_menu_checked(&menu.state,414));
    menu.state.edid|=FSA_EDID_SUPPORTED;assert(enabled(&menu,4) && fsa_menu_checked(&menu.state,414));
    menu.state.edid|=FSA_EDID_BUSY;assert(!enabled(&menu,4));
    FSA_MenuState s={0};s.selected=-1;
    Popup main={0};main.state=s;main.state.nsp_locked=1;
    assert(enabled(&main,row(106)) && fsa_nsp_toggle_request(&main.state)==2);
    assert(!wcscmp(main_label(&main.state,row(106)),fsa_text(main.state.language,TXT_NSP_STOP)));
    main.state.nsp_locked=0;assert(!enabled(&main,row(106)));
    Popup advanced={0};advanced.advanced=1;assert(count(&advanced)==8);
    int emergency=0;for(int i=0;i<count(&advanced);i++){assert(command_at(&advanced,i)!=208);emergency+=command_at(&advanced,i)==209;}
    assert(emergency==1 && submenu_row(&advanced,7));
    check_ht(&s,103,0);check_ht(&s,104,0);
    s.available=1u<<3;check_ht(&s,103,1);check_ht(&s,104,0);
    s.available=1u<<4;check_ht(&s,103,0);check_ht(&s,104,1);
    s.available=(1u<<3)|(1u<<4);check_ht(&s,103,1);check_ht(&s,104,1);
    s.extreme=FSA_MENU_ROUTE_LOCK;check_ht(&s,103,0);check_ht(&s,104,0);
    s.extreme=0;s.available=~0u;
    assert(!extra_visible(&s,107) && !extra_visible(&s,108));
    s.extreme=2;assert(extra_visible(&s,107) && extra_visible(&s,109));assert(!extra_visible(&s,108));
    s.extreme=4;assert(extra_visible(&s,108) && extra_visible(&s,110));assert(!extra_visible(&s,107));
    s.available=0;assert(!extra_visible(&s,108) && !extra_visible(&s,110));
    assert(extra_visible(&s,100) && extra_visible(&s,105) && extra_visible(&s,106) && extra_visible(&s,300));
    puts("PASS independent HT availability, hidden rows and interaction, route lock, existing enumeration groups and other entries");
    return 0;
}
