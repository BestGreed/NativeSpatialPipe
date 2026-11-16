#pragma once
#include <windows.h>
#include <openvino/c/openvino.h>
#include <array>
#include <atomic>
#include <vector>
#include <string>
#include <filesystem>
#include <algorithm>
#include <stdexcept>
#include <cmath>
// Experimental NPU-context-owned tensors. Only the inference owner touches
// recurrent state; output leases may be read on a second CPU thread.
class VocalModel {
 struct Buffer {ov_tensor_t*tensor=nullptr;float*data=nullptr;size_t length=0;};
 static constexpr unsigned Slots=64;
 ov_core_t*core=nullptr;ov_model_t*graph=nullptr;ov_compiled_model_t*compiled=nullptr;ov_remote_context_t*context=nullptr;
 std::array<ov_infer_request_t*,2>requests{};std::array<std::array<Buffer,4>,2>states;
 Buffer audio;std::array<Buffer,Slots>outputs;std::array<std::atomic<bool>,Slots>busy{};
 unsigned bank=0,next=0;int automatic=-1;std::array<unsigned,2>boundOutput{0,0};uint64_t rebindingCount=0;
 bool suppressAfterReset=false;
 std::atomic<uint64_t> nonfiniteHops{0};
 static void check(ov_status_e code){if(code!=OK){const char*e=ov_get_last_err_msg();throw std::runtime_error(std::string("NPU shared tensor: ")+(e?e:ov_get_error_info(code)));}}
 static std::string utf8(const std::wstring&s){int n=WideCharToMultiByte(CP_UTF8,0,s.data(),int(s.size()),nullptr,0,nullptr,nullptr);std::string out(n,0);WideCharToMultiByte(CP_UTF8,0,s.data(),int(s.size()),out.data(),n,nullptr,nullptr);return out;}
 void allocate(Buffer&b,const std::vector<int64_t>&shape){ov_shape_t s{int64_t(shape.size()),const_cast<int64_t*>(shape.data())};check(ov_remote_context_create_host_tensor(context,F32,s,&b.tensor));void*p=nullptr;check(ov_tensor_data(b.tensor,&p));if(!p)throw std::runtime_error("NPU host tensor has no CPU address");b.data=static_cast<float*>(p);b.length=1;for(auto d:shape)b.length*=d;std::fill_n(b.data,b.length,0.f);}
 void cleanup(){for(auto*r:requests)if(r)ov_infer_request_free(r);for(auto&b:outputs)if(b.tensor)ov_tensor_free(b.tensor);for(auto&group:states)for(auto&b:group)if(b.tensor)ov_tensor_free(b.tensor);if(audio.tensor)ov_tensor_free(audio.tensor);if(context)ov_remote_context_free(context);if(compiled)ov_compiled_model_free(compiled);if(graph)ov_model_free(graph);if(core)ov_core_free(core);}
public:
 static const char*backend(){return "NPU-OpenVINO-context-host-tensors";}
 explicit VocalModel(const wchar_t*path){try{
  check(ov_core_create(&core));check(ov_core_read_model(core,utf8(path).c_str(),nullptr,&graph));
  HMODULE owner=nullptr;GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,reinterpret_cast<LPCWSTR>(&backend),&owner);wchar_t location[32768]{};if(!GetModuleFileNameW(owner,location,32768))throw std::runtime_error("NPU shared module path");auto cache=std::filesystem::path(location).parent_path()/L"npu-cache";std::filesystem::create_directories(cache);
  check(ov_core_compile_model(core,graph,"NPU",4,&compiled,"PERFORMANCE_HINT","LATENCY","CACHE_DIR",utf8(cache.wstring()).c_str()));
  char*devices=nullptr;check(ov_compiled_model_get_property(compiled,"EXECUTION_DEVICES",&devices));std::string actual=devices?devices:"";if(devices)ov_free(devices);if(actual.find("NPU")==std::string::npos||actual.find("CPU")!=std::string::npos||actual.find("GPU")!=std::string::npos)throw std::runtime_error("Shared tensor execution device mismatch");
  check(ov_compiled_model_get_context(compiled,&context));
  const std::array<const char*,5>names={"audio_chunk","vocals_audio_history","vocals_local_hidden","vocals_global_hidden","vocals_spectral_numerator_tail"};
  const std::array<std::vector<int64_t>,5>shapes={std::vector<int64_t>{1,2,128},{1,2,896},{2,20,96},{2,1,192},{1,1,2,128}};
  size_t ni=0,no=0;check(ov_model_inputs_size(graph,&ni));check(ov_model_outputs_size(graph,&no));if(ni!=5||no!=5)throw std::runtime_error("Shared vocal IO count");
  for(unsigned i=0;i<5;i++){ov_output_const_port_t*port=nullptr;check(ov_model_const_input_by_name(graph,names[i],&port));ov_shape_t got{};auto code=ov_const_port_get_shape(port,&got);ov_output_const_port_free(port);check(code);bool matches=got.rank==int64_t(shapes[i].size())&&std::equal(shapes[i].begin(),shapes[i].end(),got.dims);ov_shape_free(&got);if(!matches)throw std::runtime_error("Shared vocal shape");}
  allocate(audio,shapes[0]);for(auto&group:states)for(unsigned i=0;i<4;i++)allocate(group[i],shapes[i+1]);for(auto&b:outputs)allocate(b,{1,1,2,128});
  for(unsigned b=0;b<2;b++){check(ov_compiled_model_create_infer_request(compiled,&requests[b]));check(ov_infer_request_set_tensor(requests[b],names[0],audio.tensor));check(ov_infer_request_set_tensor(requests[b],"separated_chunk",outputs[0].tensor));for(unsigned i=1;i<5;i++){check(ov_infer_request_set_tensor(requests[b],names[i],states[b][i-1].tensor));std::string output="next_"+std::string(names[i]);check(ov_infer_request_set_tensor(requests[b],output.c_str(),states[1-b][i-1].tensor));}}
 }catch(...){cleanup();throw;}}
 ~VocalModel(){cleanup();}
 void reset(){if(automatic>=0){busy[automatic].store(false,std::memory_order_release);automatic=-1;}for(auto&group:states)for(auto&b:group)std::fill_n(b.data,b.length,0.f);bank=0;suppressAfterReset=false;}
 const float*process(const float*input){
  if(automatic>=0){busy[automatic].store(false,std::memory_order_release);automatic=-1;}
  unsigned slot=Slots;bool free=false;
  // Keep each request's normal output address stable. The remaining slots
  // absorb a delayed CPU reader without overwriting an outstanding lease.
  if(busy[bank].compare_exchange_strong(free,true,std::memory_order_acq_rel))slot=bank;
  else for(unsigned i=0;i<Slots;i++){unsigned at=(next+i)%Slots;bool expected=false;if(busy[at].compare_exchange_strong(expected,true,std::memory_order_acq_rel)){slot=at;next=(at+1)%Slots;break;}}if(slot==Slots)throw std::runtime_error("NPU shared output leases exhausted");
  try{
   if(!std::all_of(input,input+256,[](float f){return std::isfinite(f);}))throw std::runtime_error("Nonfinite shared NPU input");
   std::copy_n(input,256,audio.data);
   if(boundOutput[bank]!=slot){check(ov_infer_request_set_tensor(requests[bank],"separated_chunk",outputs[slot].tensor));boundOutput[bank]=slot;rebindingCount++;}
   check(ov_infer_request_infer(requests[bank]));
   auto*separated=outputs[slot].data;
   if(!std::all_of(separated,separated+256,[](float f){return std::isfinite(f);})){
    nonfiniteHops.fetch_add(1,std::memory_order_relaxed);
    reset();
    suppressAfterReset=true;
    // Zero vocal contribution keeps the full, same-frame PCM in the FS residual.
    std::fill_n(separated,256,0.f);
   }else{
    // The first output after clearing history cannot describe the preceding hop.
    if(suppressAfterReset)std::fill_n(separated,256,0.f);
    suppressAfterReset=false;
    bank=1-bank;
   }
   automatic=int(slot);return separated;
  }
  catch(...){busy[slot].store(false,std::memory_order_release);throw;}
 }
 unsigned leaseLastOutput(){if(automatic<0)throw std::runtime_error("No automatic output lease");unsigned slot=automatic;automatic=-1;return slot;}
 void releaseOutput(unsigned slot){if(slot>=Slots)throw std::runtime_error("Invalid output lease");busy[slot].store(false,std::memory_order_release);}
 uint64_t outputRebindings()const{return rebindingCount;}
 uint64_t nonfiniteOutputHops()const{return nonfiniteHops.load(std::memory_order_relaxed);}
};
