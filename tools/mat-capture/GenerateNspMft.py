"""Generate COM signatures from the SDK. Behavior lives in checked source headers."""
import pathlib,re,sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
sdk=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/include/mftransform.h'
text=sdk.read_text(encoding='utf-8');at=text.index('IMFTransform : public IUnknown');text=text[at:text.index('\n};',at)];methods=re.findall(r'virtual HRESULT STDMETHODCALLTYPE (\w+)\((.*?)\) = 0;',text,re.S);assert len(methods)==23
bodies={
'GetStreamLimits':'if(!input_minimum||!input_maximum||!output_minimum||!output_maximum)return E_POINTER;*input_minimum=*input_maximum=*output_minimum=*output_maximum=1;return S_OK;',
'GetStreamCount':'if(!inputs||!outputs)return E_POINTER;*inputs=*outputs=1;return S_OK;',
'GetStreamIDs':'return E_NOTIMPL;',
'GetInputStreamInfo':'if(id)return MF_E_INVALIDSTREAMNUMBER;if(!info)return E_POINTER;*info={};info->dwFlags=MFT_INPUT_STREAM_WHOLE_SAMPLES;return S_OK;',
'GetOutputStreamInfo':'if(id)return MF_E_INVALIDSTREAMNUMBER;if(!info)return E_POINTER;*info={};info->dwFlags=MFT_OUTPUT_STREAM_PROVIDES_SAMPLES|MFT_OUTPUT_STREAM_WHOLE_SAMPLES|MFT_OUTPUT_STREAM_SINGLE_SAMPLE_PER_BUFFER;return S_OK;',
'GetAttributes':'if(!attributes)return E_POINTER;*attributes=attrs;if(!attrs)return E_OUTOFMEMORY;attrs->AddRef();return S_OK;',
'GetInputStreamAttributes':'if(id)return MF_E_INVALIDSTREAMNUMBER;if(!attributes)return E_POINTER;*attributes=nullptr;return E_NOTIMPL;',
'GetOutputStreamAttributes':'if(id)return MF_E_INVALIDSTREAMNUMBER;if(!attributes)return E_POINTER;*attributes=nullptr;return E_NOTIMPL;',
'GetInputAvailableType':'return available(id,index,type,true);','GetOutputAvailableType':'return available(id,index,type,false);',
'SetInputType':'return set(id,type,flags,true);','SetOutputType':'return set(id,type,flags,false);',
'GetInputCurrentType':'return current(id,type,true);','GetOutputCurrentType':'return current(id,type,false);',
'GetInputStatus':'if(id)return MF_E_INVALIDSTREAMNUMBER;if(!flags)return E_POINTER;std::lock_guard lock(mutex);*flags=input&&output&&!draining&&!decoder.ready()&&(!async||async->needInput)?MFT_INPUT_STATUS_ACCEPT_DATA:0;return S_OK;',
'GetOutputStatus':'if(!flags)return E_POINTER;std::lock_guard lock(mutex);*flags=(async?async->haveOutput:decoder.ready())?MFT_OUTPUT_STATUS_SAMPLE_READY:0;return S_OK;',
'ProcessMessage':'return handleMessage(message,param);','ProcessInput':'return consume(id,sample,flags);','ProcessOutput':'return produce(flags,count,samples,status);'}
source='// Generated SDK signatures; SPDX-License-Identifier: GPL-3.0-or-later\n#include "transform.h"\nstruct Transform final:IMFTransform,INspSpatialControl,IMFMediaEventGenerator,IMFShutdown,TransformBase{LONG refs=1;\nHRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid,void** out)override{if(!out)return E_POINTER;*out=nullptr;if(iid==IID_IUnknown||iid==IID_IMFTransform){*out=static_cast<IMFTransform*>(this);AddRef();return S_OK;}if(asynchronous&&iid==IID_IMFMediaEventGenerator){*out=static_cast<IMFMediaEventGenerator*>(this);AddRef();return S_OK;}if(asynchronous&&iid==IID_IMFShutdown){*out=static_cast<IMFShutdown*>(this);AddRef();return S_OK;}if(iid==IID_INspSpatialControl){*out=static_cast<INspSpatialControl*>(this);AddRef();return S_OK;}return E_NOINTERFACE;}\nULONG STDMETHODCALLTYPE AddRef()override{return InterlockedIncrement(&refs);}ULONG STDMETHODCALLTYPE Release()override{LONG n=InterlockedDecrement(&refs);if(!n)delete this;return n;}\n'
for name,args in methods:
 guard='' if name in ('GetAttributes','GetInputAvailableType','GetStreamCount','GetStreamIDs') else 'if(HRESULT hr=access();FAILED(hr))return hr;'
 source+='HRESULT STDMETHODCALLTYPE '+name+'('+args+')override{'+guard+bodies.get(name,'return E_NOTIMPL;')+'}\n'
source+='HRESULT STDMETHODCALLTYPE GetEvent(DWORD f,IMFMediaEvent** e)override{return async&&async->events?async->events->GetEvent(f,e):E_NOINTERFACE;}\nHRESULT STDMETHODCALLTYPE BeginGetEvent(IMFAsyncCallback* c,IUnknown* s)override{return async&&async->events?async->events->BeginGetEvent(c,s):E_NOINTERFACE;}\nHRESULT STDMETHODCALLTYPE EndGetEvent(IMFAsyncResult* r,IMFMediaEvent** e)override{return async&&async->events?async->events->EndGetEvent(r,e):E_NOINTERFACE;}\nHRESULT STDMETHODCALLTYPE QueueEvent(MediaEventType t,REFGUID g,HRESULT h,const PROPVARIANT* v)override{return async&&async->events?async->events->QueueEventParamVar(t,g,h,v):E_NOINTERFACE;}\nHRESULT STDMETHODCALLTYPE Shutdown()override{return shutdownMft();}\nHRESULT STDMETHODCALLTYPE GetShutdownStatus(MFSHUTDOWN_STATUS* s)override{if(!s)return E_POINTER;if(!async||!async->completed)return MF_E_INVALIDREQUEST;*s=MFSHUTDOWN_COMPLETED;return S_OK;}\n'
source+='HRESULT STDMETHODCALLTYPE BindPresentationClock(IMFPresentationClock* c,LPCWSTR e,LONGLONG o)override{return bindClock(c,e,o);}\nHRESULT STDMETHODCALLTYPE GetSpatialStatistics(UINT64* f,UINT64* v,HRESULT* r)override{return spatialStatistics(f,v,r);}\n};\n#include "registration.h"\n'
out=ROOT/'modules/media-foundation/src/mft.cpp';out.write_text(source,encoding='utf-8');print(out)
