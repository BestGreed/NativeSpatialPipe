#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
int wmain(int argc,wchar_t **argv){
 if(argc!=2)return 1;
 HMODULE m=LoadLibraryW(argv[1]);if(!m)return 2;
 puts("ready");fflush(stdout);char line[16];while(fgets(line,sizeof(line),stdin)){if(line[0]=='q')break;}
 FreeLibrary(m);return 0;
}
