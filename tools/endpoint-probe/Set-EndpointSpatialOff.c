/* Test cleanup on an explicitly selected endpoint; never selects a default. */
#define SPATIAL_POLICY_NO_MAIN
#include "../fsa-integration/native/spatial_policy.c"
int wmain(int argc,wchar_t **argv) {
    if(argc!=2 || wcsncmp(argv[1],L"{0.0.0.00000000}.{",18))return 2;
    HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);if(FAILED(hr))return 1;
    SpatialPolicy *p=NULL;WAVEFORMATEX *f=NULL;SpatialSettings *s=NULL;BYTE *d=NULL;UINT32 n=0;
    hr=CoCreateInstance(&spatial_policy_class,NULL,CLSCTX_ALL,&spatial_policy_iid,(void**)&p);
    if(SUCCEEDED(hr))hr=p->v->get(p,argv[1],FALSE,&f,&s,&n,&d);
    if(SUCCEEDED(hr) && !s)hr=E_UNEXPECTED;
    if(SUCCEEDED(hr)) {
        SpatialSettings off=*s;off.words[0]=0;memset(&off.words[3],0,16);off.words[17]=0;
        hr=p->v->set(p,argv[1],&off,NULL);
    }
    printf("{\"hr\":\"0x%08lX\"}\n",(unsigned long)hr);
    CoTaskMemFree(f);CoTaskMemFree(s);CoTaskMemFree(d);if(p)p->v->release(p);CoUninitialize();return FAILED(hr)?1:0;
}
