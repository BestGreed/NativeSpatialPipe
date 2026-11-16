// Generated SDK signatures; SPDX-License-Identifier: GPL-3.0-or-later
#include "transform.h"
struct Transform final:IMFTransform,INspSpatialControl,IMFMediaEventGenerator,IMFShutdown,TransformBase{LONG refs=1;
HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid,void** out)override{if(!out)return E_POINTER;*out=nullptr;if(iid==IID_IUnknown||iid==IID_IMFTransform){*out=static_cast<IMFTransform*>(this);AddRef();return S_OK;}if(asynchronous&&iid==IID_IMFMediaEventGenerator){*out=static_cast<IMFMediaEventGenerator*>(this);AddRef();return S_OK;}if(asynchronous&&iid==IID_IMFShutdown){*out=static_cast<IMFShutdown*>(this);AddRef();return S_OK;}if(iid==IID_INspSpatialControl){*out=static_cast<INspSpatialControl*>(this);AddRef();return S_OK;}return E_NOINTERFACE;}
ULONG STDMETHODCALLTYPE AddRef()override{return InterlockedIncrement(&refs);}ULONG STDMETHODCALLTYPE Release()override{LONG n=InterlockedDecrement(&refs);if(!n)delete this;return n;}
HRESULT STDMETHODCALLTYPE GetStreamLimits(
        DWORD *input_minimum,
        DWORD *input_maximum,
        DWORD *output_minimum,
        DWORD *output_maximum)override{if(HRESULT hr=access();FAILED(hr))return hr;if(!input_minimum||!input_maximum||!output_minimum||!output_maximum)return E_POINTER;*input_minimum=*input_maximum=*output_minimum=*output_maximum=1;return S_OK;}
HRESULT STDMETHODCALLTYPE GetStreamCount(
        DWORD *inputs,
        DWORD *outputs)override{if(!inputs||!outputs)return E_POINTER;*inputs=*outputs=1;return S_OK;}
HRESULT STDMETHODCALLTYPE GetStreamIDs(
        DWORD input_size,
        DWORD *inputs,
        DWORD output_size,
        DWORD *outputs)override{return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE GetInputStreamInfo(
        DWORD id,
        MFT_INPUT_STREAM_INFO *info)override{if(HRESULT hr=access();FAILED(hr))return hr;if(id)return MF_E_INVALIDSTREAMNUMBER;if(!info)return E_POINTER;*info={};info->dwFlags=MFT_INPUT_STREAM_WHOLE_SAMPLES;return S_OK;}
HRESULT STDMETHODCALLTYPE GetOutputStreamInfo(
        DWORD id,
        MFT_OUTPUT_STREAM_INFO *info)override{if(HRESULT hr=access();FAILED(hr))return hr;if(id)return MF_E_INVALIDSTREAMNUMBER;if(!info)return E_POINTER;*info={};info->dwFlags=MFT_OUTPUT_STREAM_PROVIDES_SAMPLES|MFT_OUTPUT_STREAM_WHOLE_SAMPLES|MFT_OUTPUT_STREAM_SINGLE_SAMPLE_PER_BUFFER;return S_OK;}
HRESULT STDMETHODCALLTYPE GetAttributes(
        IMFAttributes **attributes)override{if(!attributes)return E_POINTER;*attributes=attrs;if(!attrs)return E_OUTOFMEMORY;attrs->AddRef();return S_OK;}
HRESULT STDMETHODCALLTYPE GetInputStreamAttributes(
        DWORD id,
        IMFAttributes **attributes)override{if(HRESULT hr=access();FAILED(hr))return hr;if(id)return MF_E_INVALIDSTREAMNUMBER;if(!attributes)return E_POINTER;*attributes=nullptr;return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE GetOutputStreamAttributes(
        DWORD id,
        IMFAttributes **attributes)override{if(HRESULT hr=access();FAILED(hr))return hr;if(id)return MF_E_INVALIDSTREAMNUMBER;if(!attributes)return E_POINTER;*attributes=nullptr;return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE DeleteInputStream(
        DWORD id)override{if(HRESULT hr=access();FAILED(hr))return hr;return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE AddInputStreams(
        DWORD streams,
        DWORD *ids)override{if(HRESULT hr=access();FAILED(hr))return hr;return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE GetInputAvailableType(
        DWORD id,
        DWORD index,
        IMFMediaType **type)override{return available(id,index,type,true);}
HRESULT STDMETHODCALLTYPE GetOutputAvailableType(
        DWORD id,
        DWORD index,
        IMFMediaType **type)override{if(HRESULT hr=access();FAILED(hr))return hr;return available(id,index,type,false);}
HRESULT STDMETHODCALLTYPE SetInputType(
        DWORD id,
        IMFMediaType *type,
        DWORD flags)override{if(HRESULT hr=access();FAILED(hr))return hr;return set(id,type,flags,true);}
HRESULT STDMETHODCALLTYPE SetOutputType(
        DWORD id,
        IMFMediaType *type,
        DWORD flags)override{if(HRESULT hr=access();FAILED(hr))return hr;return set(id,type,flags,false);}
HRESULT STDMETHODCALLTYPE GetInputCurrentType(
        DWORD id,
        IMFMediaType **type)override{if(HRESULT hr=access();FAILED(hr))return hr;return current(id,type,true);}
HRESULT STDMETHODCALLTYPE GetOutputCurrentType(
        DWORD id,
        IMFMediaType **type)override{if(HRESULT hr=access();FAILED(hr))return hr;return current(id,type,false);}
HRESULT STDMETHODCALLTYPE GetInputStatus(
        DWORD id,
        DWORD *flags)override{if(HRESULT hr=access();FAILED(hr))return hr;if(id)return MF_E_INVALIDSTREAMNUMBER;if(!flags)return E_POINTER;std::lock_guard lock(mutex);*flags=input&&output&&!draining&&!decoder.ready()&&(!async||async->needInput)?MFT_INPUT_STATUS_ACCEPT_DATA:0;return S_OK;}
HRESULT STDMETHODCALLTYPE GetOutputStatus(
        DWORD *flags)override{if(HRESULT hr=access();FAILED(hr))return hr;if(!flags)return E_POINTER;std::lock_guard lock(mutex);*flags=(async?async->haveOutput:decoder.ready())?MFT_OUTPUT_STATUS_SAMPLE_READY:0;return S_OK;}
HRESULT STDMETHODCALLTYPE SetOutputBounds(
        LONGLONG lower,
        LONGLONG upper)override{if(HRESULT hr=access();FAILED(hr))return hr;return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE ProcessEvent(
        DWORD id,
        IMFMediaEvent *event)override{if(HRESULT hr=access();FAILED(hr))return hr;return E_NOTIMPL;}
HRESULT STDMETHODCALLTYPE ProcessMessage(
        MFT_MESSAGE_TYPE message,
        ULONG_PTR param)override{if(HRESULT hr=access();FAILED(hr))return hr;return handleMessage(message,param);}
HRESULT STDMETHODCALLTYPE ProcessInput(
        DWORD id,
        IMFSample *sample,
        DWORD flags)override{if(HRESULT hr=access();FAILED(hr))return hr;return consume(id,sample,flags);}
HRESULT STDMETHODCALLTYPE ProcessOutput(
        DWORD flags,
        DWORD count,
        MFT_OUTPUT_DATA_BUFFER *samples,
        DWORD *status)override{if(HRESULT hr=access();FAILED(hr))return hr;return produce(flags,count,samples,status);}
HRESULT STDMETHODCALLTYPE GetEvent(DWORD f,IMFMediaEvent** e)override{return async&&async->events?async->events->GetEvent(f,e):E_NOINTERFACE;}
HRESULT STDMETHODCALLTYPE BeginGetEvent(IMFAsyncCallback* c,IUnknown* s)override{return async&&async->events?async->events->BeginGetEvent(c,s):E_NOINTERFACE;}
HRESULT STDMETHODCALLTYPE EndGetEvent(IMFAsyncResult* r,IMFMediaEvent** e)override{return async&&async->events?async->events->EndGetEvent(r,e):E_NOINTERFACE;}
HRESULT STDMETHODCALLTYPE QueueEvent(MediaEventType t,REFGUID g,HRESULT h,const PROPVARIANT* v)override{return async&&async->events?async->events->QueueEventParamVar(t,g,h,v):E_NOINTERFACE;}
HRESULT STDMETHODCALLTYPE Shutdown()override{return shutdownMft();}
HRESULT STDMETHODCALLTYPE GetShutdownStatus(MFSHUTDOWN_STATUS* s)override{if(!s)return E_POINTER;if(!async||!async->completed)return MF_E_INVALIDREQUEST;*s=MFSHUTDOWN_COMPLETED;return S_OK;}
HRESULT STDMETHODCALLTYPE BindPresentationClock(IMFPresentationClock* c,LPCWSTR e,LONGLONG o)override{return bindClock(c,e,o);}
HRESULT STDMETHODCALLTYPE GetSpatialStatistics(UINT64* f,UINT64* v,HRESULT* r)override{return spatialStatistics(f,v,r);}
};
#include "registration.h"
