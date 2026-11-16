#pragma once
#ifdef NSP_VOCAL_OPENVINO
#ifdef NSP_VOCAL_SHARED_OUTPUT
#include "vocal_model_shared_npu.h"
#else
#include "vocal_model_openvino.h"
#endif
#else
#define ORT_API_MANUAL_INIT
#include <onnxruntime_cxx_api.h>
#include <windows.h>
#include <array>
#include <vector>
#include <string>
#include <algorithm>
#include <stdexcept>
#include <cmath>
#include <cstring>
#ifdef NSP_JOINT_DIRECTML
#include <dxgi.h>
#endif

inline HMODULE initializeVocalOrt(){
 struct Runtime{
  HMODULE module=nullptr;
  Runtime(){
   HMODULE owner=nullptr;if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,reinterpret_cast<LPCWSTR>(&initializeVocalOrt),&owner))throw std::runtime_error("ORT module identity");
   wchar_t path[32768]{};DWORD count=GetModuleFileNameW(owner,path,32768);if(!count||count>=32768)throw std::runtime_error("ORT directory");std::wstring filename(path,count);filename=filename.substr(0,filename.find_last_of(L"\\/")+1)+
#ifdef NSP_JOINT_DIRECTML
    L"onnxruntime.dll";
   constexpr const char* version="1.24.4";
#else
    L"NSP.VocalOrt126.dll";
   constexpr const char* version="1.26.0";
#endif
   module=LoadLibraryExW(filename.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);if(!module)throw std::runtime_error("Private ORT load failed");
   auto get=reinterpret_cast<const OrtApiBase*(ORT_API_CALL*)()>(GetProcAddress(module,"OrtGetApiBase"));
   if(!get||strcmp(get()->GetVersionString(),version)){FreeLibrary(module);module=nullptr;throw std::runtime_error("Private ORT version");}
   const OrtApi* api=get()->GetApi(ORT_API_VERSION);if(!api){FreeLibrary(module);module=nullptr;throw std::runtime_error("Private ORT API unavailable");}Ort::InitApi(api);
  }
  ~Runtime(){if(module)FreeLibrary(module);}
 };static Runtime runtime;
 return runtime.module;
}

#ifdef NSP_JOINT_DIRECTML
inline void configureRadeon(Ort::SessionOptions& options){
 IDXGIFactory* raw=nullptr;
 const GUID factoryId={0x7b7166ec,0x21c7,0x44ae,{0xb2,0x1a,0xc9,0xae,0x32,0x1a,0xe3,0x69}};
 if(FAILED(CreateDXGIFactory(factoryId,reinterpret_cast<void**>(&raw))))throw std::runtime_error("DXGI factory failed");
 auto release=[](auto* ptr){ptr->Release();};
 std::unique_ptr<IDXGIFactory,decltype(release)> factory(raw,release);
 for(UINT index=0;;index++){
  IDXGIAdapter* rawAdapter=nullptr;
  const HRESULT result=factory->EnumAdapters(index,&rawAdapter);
  if(result==DXGI_ERROR_NOT_FOUND)break;
  if(FAILED(result))throw std::runtime_error("DXGI adapter enumeration failed");
  std::unique_ptr<IDXGIAdapter,decltype(release)> adapter(rawAdapter,release);
  DXGI_ADAPTER_DESC desc{};
  if(FAILED(adapter->GetDesc(&desc)))throw std::runtime_error("DXGI adapter description failed");
  // The local audition targets the Radeon 13c0 iGPU, even when NVIDIA is first.
  if(desc.VendorId!=0x1002||desc.DeviceId!=0x13c0)continue;
  using Append=OrtStatus*(ORT_API_CALL*)(OrtSessionOptions*,int);
  auto append=reinterpret_cast<Append>(GetProcAddress(initializeVocalOrt(),"OrtSessionOptionsAppendExecutionProvider_DML"));
  if(!append)throw std::runtime_error("ORT DirectML provider unavailable");
  options.DisableMemPattern();
  options.AddConfigEntry("session.disable_cpu_ep_fallback","1");
  Ort::ThrowOnError(append(options,static_cast<int>(index)));
  return;
 }
 throw std::runtime_error("AMD Radeon 13c0 adapter not found");
}
#endif

// Stateful StemgenRT vocal partition, original stereo output. CPU worker only.
class VocalModel {
 static Ort::Env makeEnvironment(){initializeVocalOrt();return Ort::Env{ORT_LOGGING_LEVEL_ERROR,"NSpVocal"};}
 Ort::Env env=makeEnvironment();
 Ort::SessionOptions options;Ort::Session session{nullptr};
 Ort::MemoryInfo memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
 Ort::RunOptions runOptions;
 std::array<std::string,5> inputNames,outputNames;
 std::array<const char*,5> in{},out{};
 std::array<std::array<std::vector<float>,4>,2> states;
 std::array<std::vector<Ort::Value>,2> inputs,outputs;
 std::array<float,256> audio{},separated{};unsigned bank=0;
public:
 static const char* backend(){
#ifdef NSP_JOINT_DIRECTML
  return "DirectML-Radeon";
#else
  return "CPU-ORT";
#endif
 }
 explicit VocalModel(const wchar_t* path){
  options.SetIntraOpNumThreads(1);options.SetInterOpNumThreads(1);options.SetExecutionMode(ExecutionMode::ORT_SEQUENTIAL);options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
  options.AddConfigEntry("session.intra_op.allow_spinning","0");options.AddConfigEntry("session.inter_op.allow_spinning","0");
#ifdef NSP_JOINT_DIRECTML
  configureRadeon(options);
#endif
  session=Ort::Session(env,path,options);if(session.GetInputCount()!=5||session.GetOutputCount()!=5)throw std::runtime_error("Vocal partition contract");
  Ort::AllocatorWithDefaultOptions allocator;
  std::array<std::vector<int64_t>,5> shapes,outputShapes;
  for(size_t i=0;i<5;i++){
   inputNames[i]=session.GetInputNameAllocated(i,allocator).get();outputNames[i]=session.GetOutputNameAllocated(i,allocator).get();in[i]=inputNames[i].c_str();out[i]=outputNames[i].c_str();
   shapes[i]=session.GetInputTypeInfo(i).GetTensorTypeAndShapeInfo().GetShape();outputShapes[i]=session.GetOutputTypeInfo(i).GetTensorTypeAndShapeInfo().GetShape();
   if(session.GetInputTypeInfo(i).GetTensorTypeAndShapeInfo().GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT||session.GetOutputTypeInfo(i).GetTensorTypeAndShapeInfo().GetElementType()!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)throw std::runtime_error("Vocal tensor type");
  }
  if(inputNames[0]!="audio_chunk"||shapes[0]!=std::vector<int64_t>{1,2,128}||elements(outputShapes[0])!=256)throw std::runtime_error("Vocal audio geometry");
  for(unsigned b=0;b<2;b++)for(size_t i=1;i<5;i++){if(inputNames[i].find("vocals_")!=0||outputNames[i]!="next_"+inputNames[i]||shapes[i]!=outputShapes[i])throw std::runtime_error("Vocal state geometry");states[b][i-1].resize(elements(shapes[i]));}
  for(unsigned b=0;b<2;b++)for(size_t i=0;i<5;i++){
   auto* input=i?states[b][i-1].data():audio.data();auto* output=i?states[1-b][i-1].data():separated.data();
   inputs[b].push_back(Ort::Value::CreateTensor<float>(memory,input,elements(shapes[i]),shapes[i].data(),shapes[i].size()));
   outputs[b].push_back(Ort::Value::CreateTensor<float>(memory,output,elements(outputShapes[i]),outputShapes[i].data(),outputShapes[i].size()));
  }
 }
 static size_t elements(const std::vector<int64_t>& shape){size_t n=1;for(auto d:shape){if(d<1||d>1000000||n>1000000/(size_t)d)throw std::runtime_error("Vocal dimension bound");n*=d;}return n;}
 void reset(){for(auto& b:states)for(auto& v:b)std::fill(v.begin(),v.end(),0.f);bank=0;}
 const float* process(const float* planar128){std::copy_n(planar128,256,audio.begin());session.Run(runOptions,in.data(),inputs[bank].data(),5,out.data(),outputs[bank].data(),5);bank=1-bank;if(!std::all_of(separated.begin(),separated.end(),[](float x){return std::isfinite(x);}))throw std::runtime_error("Nonfinite vocal output");return separated.data();}
};
#endif
