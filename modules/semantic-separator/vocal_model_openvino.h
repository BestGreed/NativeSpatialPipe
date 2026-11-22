#pragma once
#include <windows.h>
#include <openvino/c/openvino.h>
#include <array>
#include <vector>
#include <string>
#include <algorithm>
#include <stdexcept>
#include <filesystem>
#include <cmath>
#include <fstream>
#include <xmmintrin.h>
// Compile against an explicit execution device. The scheduler owns fallback.
class VocalModel {
#ifdef NSP_JOINT_CPU
 static constexpr const char* device="CPU";
 static constexpr const wchar_t* cacheName=L"cpu-cache";
#elif defined(NSP_JOINT_GPU)
 static constexpr const char* device="GPU";
 static constexpr const wchar_t* cacheName=L"gpu-cache";
#else
 static constexpr const char* device="NPU";
 static constexpr const wchar_t* cacheName=L"npu-cache";
#endif
 ov_core_t* core=nullptr;ov_model_t* model=nullptr;ov_compiled_model_t* compiled=nullptr;
 std::array<ov_infer_request_t*,2> requests{};
 std::array<std::array<ov_tensor_t*,5>,2> in{},out{};
 std::array<std::array<std::vector<float>,4>,2> states;
 std::array<float,256> audio{},separated{};unsigned bank=0;
#ifdef NSP_NPU_DIAGNOSTICS
 std::array<std::vector<float>,4> checkpoint;std::filesystem::path diagnosticFolder;uint64_t diagnosticHop=0;unsigned dumpCount=0;
 void dumpFailure(const char* message){if(dumpCount>=3)return;try{auto prefix=diagnosticFolder/("failure-"+std::to_string(dumpCount++));std::ofstream input(prefix.string()+"-input.f32",std::ios::binary),output(prefix.string()+"-output.f32",std::ios::binary),meta(prefix.string()+".txt");input.write(reinterpret_cast<const char*>(audio.data()),sizeof(audio));output.write(reinterpret_cast<const char*>(separated.data()),sizeof(separated));for(unsigned i=0;i<4;++i){input.write(reinterpret_cast<const char*>(checkpoint[i].data()),checkpoint[i].size()*sizeof(float));output.write(reinterpret_cast<const char*>(states[1-bank][i].data()),states[1-bank][i].size()*sizeof(float));}meta<<"hop="<<diagnosticHop<<" bank="<<bank<<" mxcsr="<<_mm_getcsr()<<"\n"<<message<<"\n";}catch(...){}}
#endif
 static void check(ov_status_e status){if(status!=OK){const char* msg=ov_get_last_err_msg();throw std::runtime_error(std::string(backend())+": "+(msg?msg:ov_get_error_info(status)));}}
 void release(){for(auto p:requests)if(p)ov_infer_request_free(p);for(auto& b:in)for(auto p:b)if(p)ov_tensor_free(p);for(auto& b:out)for(auto p:b)if(p)ov_tensor_free(p);if(compiled)ov_compiled_model_free(compiled);if(model)ov_model_free(model);if(core)ov_core_free(core);}
 static std::string utf8(const std::wstring& s){int n=WideCharToMultiByte(CP_UTF8,0,s.data(),int(s.size()),nullptr,0,nullptr,nullptr);std::string result(n,0);WideCharToMultiByte(CP_UTF8,0,s.data(),int(s.size()),result.data(),n,nullptr,nullptr);return result;}
public:
 static const char* backend(){
#ifdef NSP_JOINT_CPU
  return "CPU-OpenVINO";
#elif defined(NSP_JOINT_GPU)
  return "GPU-OpenVINO";
#else
  return "NPU-OpenVINO";
#endif
 }
 explicit VocalModel(const wchar_t* path){try{
  check(ov_core_create(&core));check(ov_core_read_model(core,utf8(path).c_str(),nullptr,&model));
  HMODULE owner=nullptr;GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,reinterpret_cast<LPCWSTR>(&backend),&owner);wchar_t location[32768]{};if(!GetModuleFileNameW(owner,location,32768))throw std::runtime_error("NPU module location");
#ifdef NSP_NPU_DIAGNOSTICS
  diagnosticFolder=std::filesystem::path(location).parent_path();
#endif
  auto cache=std::filesystem::path(location).parent_path()/cacheName;std::filesystem::create_directories(cache);
  check(ov_core_compile_model(core,model,device,4,&compiled,"PERFORMANCE_HINT","LATENCY","CACHE_DIR",utf8(cache.wstring()).c_str()));
  char* devices=nullptr;check(ov_compiled_model_get_property(compiled,"EXECUTION_DEVICES",&devices));std::string actual=devices?devices:"";if(devices)ov_free(devices);
  if(actual.find(device)==std::string::npos)
   throw std::runtime_error(std::string(backend())+" execution device mismatch: "+actual);
  const std::array<const char*,5> names={"audio_chunk","vocals_audio_history","vocals_local_hidden","vocals_global_hidden","vocals_spectral_numerator_tail"};
  const std::array<std::vector<int64_t>,5> shapes={std::vector<int64_t>{1,2,128},{1,2,896},{2,20,96},{2,1,192},{1,1,2,128}};
  size_t ni=0,no=0;check(ov_model_inputs_size(model,&ni));check(ov_model_outputs_size(model,&no));if(ni!=5||no!=5)throw std::runtime_error("NPU vocal IO count");
  for(unsigned b=0;b<2;++b)for(unsigned i=1;i<5;++i){size_t n=1;for(auto d:shapes[i])n*=d;states[b][i-1].resize(n);}
#ifdef NSP_NPU_DIAGNOSTICS
  for(unsigned i=0;i<4;++i)checkpoint[i].resize(states[0][i].size());
#endif
  for(unsigned b=0;b<2;++b){check(ov_compiled_model_create_infer_request(compiled,&requests[b]));for(unsigned i=0;i<5;++i){
    ov_output_const_port_t* port=nullptr;check(ov_model_const_input_by_name(model,names[i],&port));ov_shape_t actual_shape{};auto code=ov_const_port_get_shape(port,&actual_shape);ov_output_const_port_free(port);check(code);
    bool matches=actual_shape.rank==int64_t(shapes[i].size())&&std::equal(shapes[i].begin(),shapes[i].end(),actual_shape.dims);ov_shape_free(&actual_shape);if(!matches)throw std::runtime_error("NPU vocal input shape");
    ov_shape_t shape{int64_t(shapes[i].size()),const_cast<int64_t*>(shapes[i].data())};
    check(ov_tensor_create_from_host_ptr(F32,shape,i?states[b][i-1].data():audio.data(),&in[b][i]));
    std::vector<int64_t> output_shape=i?shapes[i]:std::vector<int64_t>{1,1,2,128};ov_shape_t os{int64_t(output_shape.size()),output_shape.data()};
    check(ov_tensor_create_from_host_ptr(F32,os,i?states[1-b][i-1].data():separated.data(),&out[b][i]));
    check(ov_infer_request_set_tensor(requests[b],names[i],in[b][i]));std::string output_name=i?"next_"+std::string(names[i]):"separated_chunk";check(ov_infer_request_set_tensor(requests[b],output_name.c_str(),out[b][i]));
  }}
 }catch(...){release();throw;}}
 ~VocalModel(){release();}
 void reset(){for(auto& b:states)for(auto& v:b)std::fill(v.begin(),v.end(),0.f);bank=0;}
 const float* process(const float* input){std::copy_n(input,256,audio.begin());
#ifdef NSP_NPU_DIAGNOSTICS
 diagnosticHop++;for(unsigned i=0;i<4;++i)std::copy(states[bank][i].begin(),states[bank][i].end(),checkpoint[i].begin());
#endif
 try{if(!std::all_of(audio.begin(),audio.end(),[](float x){return std::isfinite(x);}))throw std::runtime_error("Nonfinite NPU input audio");check(ov_infer_request_infer(requests[bank]));if(!std::all_of(separated.begin(),separated.end(),[](float x){return std::isfinite(x);}))throw std::runtime_error("Nonfinite NPU vocal output");bank=1-bank;return separated.data();}catch(const std::exception& e){
#ifdef NSP_NPU_DIAGNOSTICS
 dumpFailure(e.what());
#endif
 throw;}}

};
