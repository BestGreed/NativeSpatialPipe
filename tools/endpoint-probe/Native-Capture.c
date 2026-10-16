// Windows x64 capture/worker only. Existing nsp_link_send ABI remains unchanged.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#ifdef NSP_WORKER_MMCSS
#include <avrt.h>
#endif
#define API __declspec(dllexport)
#define CAP 16
#define OBJECTS 160
#define BYTES (OBJECTS*(32+4096*4))
typedef int (*Send)(void*,const char*,uint32_t,const void*,uint32_t);
typedef struct {
 uintptr_t block,pcm,props;uint64_t sequence,ordinal,capture,done,start,unix_ms;
 uint32_t bytes,count,stride,index,tid,frames[OBJECTS];uint8_t data[BYTES];
} Packet;
typedef struct {
 uintptr_t vt1,vt2;Send send;void *link;HANDLE event,worker;DWORD worker_id;
 volatile LONG write_seq,read_seq,busy,stopping,concurrent,faults,send_errors;
 uint64_t hits,copies,total,budget,budget_rejected,dropped,invalid,sent,wakes;
 uint32_t high,thread_count,tids[16];uintptr_t blocks[16];uint64_t sequences[16];uint32_t block_count;
 LARGE_INTEGER hz;uint64_t histogram[50001],callback_count,callback_sum,callback_max;
 DWORD mmcss_error;int mmcss_registered,mmcss_priority,mmcss_reverted;
 double worker_cpu_seconds;DWORD worker_times_error;int worker_times_ok;uint64_t worker_cycles;
 Packet slots[CAP];
} Capture;
static uint64_t ns(Capture *c){LARGE_INTEGER q;QueryPerformanceCounter(&q);return (q.QuadPart/c->hz.QuadPart)*1000000000ULL+(q.QuadPart%c->hz.QuadPart)*1000000000ULL/c->hz.QuadPart;}
static LONG acquire(volatile LONG *p){return InterlockedCompareExchange(p,0,0);}
static void capture_inner(Capture *c,uint8_t *b,uint64_t start){
 uintptr_t vt=*(uintptr_t*)b;if(vt!=c->vt1&&vt!=c->vt2)return;
 uint32_t count=*(uint32_t*)(b+0x5d4),index=*(uint32_t*)(b+0x5d0);
 if(b[0x5d8]!=1||count<1||count>OBJECTS||index>1){c->invalid++;return;}
 uint8_t *desc=*(uint8_t**)(b+0x510);uint32_t stride=*(uint32_t*)(desc+20);
 if(*(uint32_t*)(desc+12)!=32||stride<4||stride>16384||stride%4){c->invalid++;return;}
 uint8_t *props=*(uint8_t**)(desc+0x28+index*24),*pcm=*(uint8_t**)(desc+0x38+index*24);
 uint32_t w=(uint32_t)acquire(&c->write_seq),r=(uint32_t)acquire(&c->read_seq);
 if(w-r>=CAP){c->dropped++;return;}
 Packet *p=&c->slots[w%CAP];p->capture=ns(c);uint32_t bytes=count*32;
 for(uint32_t i=0;i<count;i++){uint32_t f=*(uint32_t*)(props+i*32+4);if(f>4096||f*4>stride){c->invalid++;return;}p->frames[i]=f;bytes+=f*4;}
 if(c->total+bytes>c->budget){c->budget_rejected++;return;}
 memcpy(p->data,props,count*32);uint32_t offset=count*32;
 for(uint32_t i=0;i<count;i++){memcpy(p->data+offset,pcm+i*stride,p->frames[i]*4);offset+=p->frames[i]*4;}
 if(b[0x5d8]!=1||*(uint32_t*)(b+0x5d0)!=index){c->invalid++;return;}
 uint32_t k;for(k=0;k<c->block_count;k++)if(c->blocks[k]==(uintptr_t)b)break;
 if(k==c->block_count){if(k==16){c->invalid++;return;}c->blocks[k]=(uintptr_t)b;c->block_count++;}
 p->done=ns(c);p->block=(uintptr_t)b;p->pcm=(uintptr_t)pcm;p->props=(uintptr_t)props;
 p->count=count;p->stride=stride;p->index=index;p->bytes=bytes;p->start=start;p->tid=GetCurrentThreadId();
 p->sequence=c->sequences[k]++;p->ordinal=c->copies;
 FILETIME ft;GetSystemTimeAsFileTime(&ft);ULARGE_INTEGER time;time.LowPart=ft.dwLowDateTime;time.HighPart=ft.dwHighDateTime;p->unix_ms=(time.QuadPart-116444736000000000ULL)/10000;
 uint32_t t;for(t=0;t<c->thread_count;t++)if(c->tids[t]==p->tid)break;if(t==c->thread_count&&t<16)c->tids[c->thread_count++]=p->tid;
 c->copies++;c->total+=bytes;if(w-r+1>c->high)c->high=w-r+1;
 InterlockedExchange(&c->write_seq,(LONG)(w+1));
 // One notification per publish: no unsafe empty->nonempty wakeup race.
 if(!SetEvent(c->event))InterlockedIncrement(&c->faults);
}
API void nsp_capture_packet(Capture *c,void *block){
 if(acquire(&c->stopping))return;
 if(InterlockedCompareExchange(&c->busy,1,0)!=0){InterlockedIncrement(&c->concurrent);return;}
 uint64_t start=ns(c);c->hits++;
 __try {capture_inner(c,block,start);} __except(EXCEPTION_EXECUTE_HANDLER){InterlockedIncrement(&c->faults);}
 uint64_t elapsed=ns(c)-start;c->callback_count++;c->callback_sum+=elapsed;if(elapsed>c->callback_max)c->callback_max=elapsed;
 c->histogram[elapsed/1000>50000?50000:elapsed/1000]++;
 InterlockedExchange(&c->busy,0);
}
static DWORD WINAPI drain(void *arg){
 Capture *c=arg;char header[8192];
#ifdef NSP_WORKER_MMCSS
 DWORD task_index=0;HANDLE task=AvSetMmThreadCharacteristicsW(L"Pro Audio",&task_index);
 c->mmcss_registered=task!=NULL;
 if(task){c->mmcss_priority=AvSetMmThreadPriority(task,AVRT_PRIORITY_NORMAL);if(!c->mmcss_priority)c->mmcss_error=GetLastError();}
 else c->mmcss_error=GetLastError();
#endif
 for(;;){
  uint32_t r=(uint32_t)acquire(&c->read_seq),w=(uint32_t)acquire(&c->write_seq);
  if(r==w){if(acquire(&c->stopping))break;DWORD result=WaitForSingleObject(c->event,1000);if(result==WAIT_OBJECT_0)c->wakes++;else if(result!=WAIT_TIMEOUT){InterlockedIncrement(&c->faults);break;}continue;}
  Packet *p=&c->slots[r%CAP];uint64_t sent=ns(c);
  int n=snprintf(header,sizeof(header),"{\"block\":\"0x%llx\",\"sequence\":%llu,\"ordinal\":%llu,\"bufferIndex\":%u,\"count\":%u,\"stride\":%u,\"threadId\":%u,\"captureQpcNs\":%llu,\"callbackStartQpcNs\":%llu,\"copyDoneQpcNs\":%llu,\"sendQpcNs\":%llu,\"pcmPointer\":\"0x%llx\",\"propertiesPointer\":\"0x%llx\",\"capturedUnixMs\":%llu,\"scope\":\"unmarked-synchronous-owned-copy\",",(unsigned long long)p->block,p->sequence,p->ordinal,p->index,p->count,p->stride,p->tid,p->capture,p->start,p->done,sent,(unsigned long long)p->pcm,(unsigned long long)p->props,p->unix_ms);
  uint32_t uniform=p->frames[0];for(uint32_t i=1;i<p->count;i++)if(p->frames[i]!=uniform){uniform=0;break;}
  if(n<0||n>6000){InterlockedIncrement(&c->send_errors);break;}
  if(uniform)n+=snprintf(header+n,sizeof(header)-n,"\"framesUniform\":%u}",uniform);
  else {n+=snprintf(header+n,sizeof(header)-n,"\"frames\":[");for(uint32_t i=0;i<p->count;i++)n+=snprintf(header+n,sizeof(header)-n,"%s%u",i?",":"",p->frames[i]);n+=snprintf(header+n,sizeof(header)-n,"]}");}
  if(n<0||n>=(int)sizeof(header)||c->send(c->link,header,(uint32_t)n,p->data,p->bytes)!=0){InterlockedIncrement(&c->send_errors);break;}
  c->sent++;InterlockedExchange(&c->read_seq,(LONG)(r+1));
 }
#ifdef NSP_WORKER_MMCSS
 if(task){c->mmcss_reverted=AvRevertMmThreadCharacteristics(task);if(!c->mmcss_reverted)c->mmcss_error=GetLastError();}
#endif
 FILETIME created,exited,kernel,user;
 c->worker_times_ok=GetThreadTimes(GetCurrentThread(),&created,&exited,&kernel,&user);
 if(c->worker_times_ok){
  ULARGE_INTEGER k,u;k.LowPart=kernel.dwLowDateTime;k.HighPart=kernel.dwHighDateTime;u.LowPart=user.dwLowDateTime;u.HighPart=user.dwHighDateTime;
  c->worker_cpu_seconds=(double)(k.QuadPart+u.QuadPart)/10000000.0;
 }else c->worker_times_error=GetLastError();
 QueryThreadCycleTime(GetCurrentThread(),&c->worker_cycles);
 return 0;
}
API Capture *nsp_capture_create(void *vt1,void *vt2,void *send,void *link,uint64_t budget){
 Capture *c=HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,sizeof(Capture));if(!c)return NULL;
 c->vt1=(uintptr_t)vt1;c->vt2=(uintptr_t)vt2;c->send=(Send)send;c->link=link;c->budget=budget;QueryPerformanceFrequency(&c->hz);
 c->event=CreateEventW(NULL,FALSE,FALSE,NULL);if(c->event)c->worker=CreateThread(NULL,0,drain,c,0,&c->worker_id);
 if(!c->worker){if(c->event)CloseHandle(c->event);HeapFree(GetProcessHeap(),0,c);return NULL;}return c;
}
// Caller closes native callback gate and waits for active callbacks before stop.
API int nsp_capture_stop(Capture *c){InterlockedExchange(&c->stopping,1);SetEvent(c->event);return WaitForSingleObject(c->worker,5000)==WAIT_OBJECT_0?0:-1;}
static double percentile(Capture *c,unsigned q){uint64_t total=0;for(int i=0;i<=50000;i++){total+=c->histogram[i];if(total*100>=c->callback_count*q)return i/1000.0;}return 50;}
API int nsp_capture_report(Capture *c,char *out,uint32_t cap){
 if(cap<8192)return -1;
 int n=snprintf(out,cap,"{\"hits\":%llu,\"copies\":%llu,\"sent\":%llu,\"queueDropped\":%llu,\"queueHighWater\":%u,\"queueRemaining\":%u,\"totalBytes\":%llu,\"budgetRejected\":%llu,\"invalidDescriptors\":%llu,\"concurrentRejected\":%ld,\"faults\":%ld,\"sendErrors\":%ld,\"schedulerStats\":{\"backend\":\"pure-c-worker\",\"threadId\":%lu,\"workerStopped\":true,\"notifications\":%llu},\"callbackMs\":{\"count\":%llu,\"mean\":%.9f,\"p50\":%.3f,\"p95\":%.3f,\"p99\":%.3f,\"max\":%.9f,\"overflow\":%llu},\"producerThreadIds\":[",c->hits,c->copies,c->sent,c->dropped,c->high,(uint32_t)c->write_seq-(uint32_t)c->read_seq,c->total,c->budget_rejected,c->invalid,c->concurrent,c->faults,c->send_errors,c->worker_id,c->wakes,c->callback_count,c->callback_count?(double)c->callback_sum/c->callback_count/1e6:0,percentile(c,50),percentile(c,95),percentile(c,99),c->callback_max/1e6,c->histogram[50000]);
 for(uint32_t i=0;i<c->thread_count;i++)n+=snprintf(out+n,cap-n,"%s%u",i?",":"",c->tids[i]);
 n+=snprintf(out+n,cap-n,"],\"workerCpuSeconds\":%.8f,\"workerTimesOk\":%d,\"workerTimesError\":%lu,\"workerCycles\":%llu,\"mmcss\":{\"registered\":%d,\"prioritySet\":%d,\"reverted\":%d,\"error\":%lu},\"blocks\":{",c->worker_cpu_seconds,c->worker_times_ok,c->worker_times_error,(unsigned long long)c->worker_cycles,c->mmcss_registered,c->mmcss_priority,c->mmcss_reverted,c->mmcss_error);for(uint32_t i=0;i<c->block_count;i++)n+=snprintf(out+n,cap-n,"%s\"0x%llx\":%llu",i?",":"",(unsigned long long)c->blocks[i],c->sequences[i]);n+=snprintf(out+n,cap-n,"}}");return n;
}
API void nsp_capture_destroy(Capture *c){CloseHandle(c->worker);CloseHandle(c->event);HeapFree(GetProcessHeap(),0,c);}
