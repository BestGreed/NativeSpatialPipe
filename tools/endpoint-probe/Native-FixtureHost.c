// Fresh native-only source process for matched Gum/MinHook experiments.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
typedef int (*Feed)(const void*,const void*,uint32_t,uint32_t,uint32_t,uint32_t);
static double cpu(void){FILETIME a,b,k,u;GetProcessTimes(GetCurrentProcess(),&a,&b,&k,&u);ULARGE_INTEGER x,y;x.LowPart=k.dwLowDateTime;x.HighPart=k.dwHighDateTime;y.LowPart=u.dwLowDateTime;y.HighPart=u.dwHighDateTime;return (x.QuadPart+y.QuadPart)/1e7;}
int wmain(int argc,wchar_t **argv){
 if(argc!=4 && argc!=5)return 1;HMODULE fixture=LoadLibraryW(argv[1]);if(!fixture)return 2;
 Feed feed=(Feed)GetProcAddress(fixture,"feed");FILE *f=_wfopen(argv[2],L"rb");if(!feed||!f)return 3;
 const size_t bytes=140*(32+1920);unsigned char *data=malloc(bytes);if(!data)return 4;size_t n=fread(data,1,bytes,f);fclose(f);if(n!=bytes)return 5;
 /* Optional period is test-only. Sleep(10) is not a 48 kHz device clock;
    a finite fixed-clock PCM comparison may explicitly feed slightly ahead. */
 uint32_t period=argc==5?(uint32_t)wcstoul(argv[4],NULL,10):10;
 char line[32];puts("ready");fflush(stdout);while(fgets(line,sizeof(line),stdin)){if(line[0]!='g')break;double begin=cpu();int us=feed(data,data+140*32,140,1920,(uint32_t)wcstoul(argv[3],NULL,10),period);
 printf("{\"instrumentedCallTotalUs\":%d,\"childCpuSeconds\":%.9f}\n",us,cpu()-begin);fflush(stdout);}free(data);FreeLibrary(fixture);return 0;
}
