/* Local transport filtering is FSA core work. No subprocess/EDID reads needed. */
static int fsa_edid_transport_allowed(DWORD form,const wchar_t *enumerator){
    return form==9 && enumerator && !_wcsicmp(enumerator,L"HDAUDIO");
}
static int fsa_edid_endpoint_eligible(const wchar_t *endpoint){
    static const PROPERTYKEY enumerator_key={{0xa45c254e,0xdf1c,0x4efd,{0x80,0x20,0x67,0xd1,0x46,0xa8,0x50,0xe0}},24};
    IMMDevice *device=NULL;IPropertyStore *store=NULL;PROPVARIANT form,bus;
    PropVariantInit(&form);PropVariantInit(&bus);int eligible=0;
    HRESULT hr=device_open(endpoint,&device);
    if(SUCCEEDED(hr))hr=IMMDevice_OpenPropertyStore(device,STGM_READ,&store);
    if(SUCCEEDED(hr))hr=IPropertyStore_GetValue(store,&PKEY_AudioEndpoint_FormFactor,&form);
    if(SUCCEEDED(hr))hr=IPropertyStore_GetValue(store,&enumerator_key,&bus);
    if(SUCCEEDED(hr) && form.vt==VT_UI4 && bus.vt==VT_LPWSTR)eligible=fsa_edid_transport_allowed(form.ulVal,bus.pwszVal);
    PropVariantClear(&form);PropVariantClear(&bus);
    if(store)IPropertyStore_Release(store);if(device)IMMDevice_Release(device);
    return eligible;
}
static unsigned fsa_edid_state(unsigned spatial,int eligible,int busy){
    return (eligible?FSA_EDID_ELIGIBLE:0)|((spatial&((1u<<MODE_ATMOS)|(1u<<MODE_DTS)))?FSA_EDID_SUPPORTED:0)|(busy?FSA_EDID_BUSY:0);
}
