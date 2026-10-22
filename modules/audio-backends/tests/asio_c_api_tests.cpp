// SPDX-License-Identifier: Apache-2.0
#include "asio_test_driver.h"
#include "asio_output_c.h"
#include "asio_format.h"
#include <thread>
#include <limits>
extern "C" uint32_t nsp_asio_c_config_size(void);
extern "C" uint32_t nsp_asio_c_stats_size(void);
extern "C" int32_t nsp_asio_c_open(const nsp_asio_config*,nsp_asio_handle*);
int main(){
    try {
        Expect(nsp_asio_c_config_size()==sizeof(nsp_asio_config)&&nsp_asio_c_stats_size()==sizeof(nsp_asio_stats),"C/C++ structure mismatch");
        Expect(nsp_asio_abi_version()==1,"ABI version");
        uint32_t sources[]{1,0};int32_t physical[]{7,3};
        nsp_asio_config config{};config.struct_size=sizeof(config);config.abi_version=1;
        config.driver_id_utf8="asio:test";config.source_channels=2;config.output_channels=2;
        config.source_channel_indices=sources;config.physical_channels=physical;config.device_sample_rate=48000;
        nsp_asio_handle h=nullptr;
        auto invalid=config;invalid.abi_version=2;
        Expect(nsp_asio_c_open(&invalid,&h)==NSP_ASIO_INVALID&&!h&&opens==0,"Version rejection opened driver");
        invalid=config;invalid.flags=2;
        Expect(nsp_asio_c_open(&invalid,&h)==NSP_ASIO_INVALID&&!h,"Unknown config flags accepted");
        int32_t mtaResult=0;
        std::thread mta([&]{
            const auto initialized=CoInitializeEx(nullptr,COINIT_MULTITHREADED);
            nsp_asio_handle rejected=nullptr;
            mtaResult=nsp_asio_open(&config,&rejected);
            if(rejected){nsp_asio_stop(rejected);nsp_asio_close(&rejected);mtaResult=0;}
            if(SUCCEEDED(initialized))CoUninitialize();
        });mta.join();
        Expect(mtaResult==NSP_ASIO_DEVICE_ERROR&&opens==0,"MTA caller reached driver activation");
        Expect(nsp_asio_c_open(&config,&h)==0&&h,"C caller open failed");
        const auto apartmentCheck=CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
        Expect(apartmentCheck==S_FALSE,"ASIO owner is not an initialized STA");
        if(SUCCEEDED(apartmentCheck))CoUninitialize();
        Expect(nsp_asio_open(&config,&h)==NSP_ASIO_STATE&&h,"Repeated open lost handle");
        sources[0]=31;physical[0]=31; // The module must already own config copies.
        Expect(current->buffers[0].channelNum==7,"Config lifetime leaked across ABI");
        Expect(nsp_asio_service(h)==NSP_ASIO_STATE,"Service before start accepted");
        Expect(nsp_asio_prime(h)==NSP_ASIO_STATE,"Prime before start accepted");
        Expect(nsp_asio_start(h)==0,"Start failed");current->Tick();
        nsp_asio_stats stats{};stats.struct_size=sizeof(stats);
        Expect(nsp_asio_get_stats(h,&stats)==0&&!stats.primed&&stats.source_frame==0,"Silent start advanced source");
        Expect(nsp_asio_close(&h)==NSP_ASIO_STATE&&h,"Close released active device");
        int32_t otherThread=0;
        std::thread foreign([&]{otherThread=nsp_asio_start(h);});foreign.join();
        Expect(otherThread==NSP_ASIO_STATE,"Foreign thread was not rejected");
        std::vector<float> samples(8192);
        for(std::size_t i=0;i<4096;++i){samples[i*2]=.125f;samples[i*2+1]=-.25f;}
        Expect(nsp_asio_submit(h,1,samples.data(),4096)==NSP_ASIO_INVALID,"Frame gap accepted");
        samples[0]=std::numeric_limits<float>::quiet_NaN();
        Expect(nsp_asio_submit(h,0,samples.data(),4096)==NSP_ASIO_INVALID,"Nonfinite PCM accepted");
        samples[0]=.125f;
        Expect(nsp_asio_submit(h,0,samples.data(),4096)==0,"PCM submit failed");
        std::fill(samples.begin(),samples.end(),0); // Caller may reuse immediately.
        Expect(nsp_asio_prime(h)==0&&nsp_asio_prime(h)==NSP_ASIO_STATE,"Prime lifecycle failed");
        Expect(nsp_asio_get_stats(h,&stats)==0,"Hold stats failed");
        const auto heldSource=stats.source_frame;
        const auto heldStarvation=stats.starvation_frames;
        Expect(nsp_asio_hold(h,1)==0,"Hold failed");
        for(unsigned i=0;i<3;++i)current->Tick();
        Expect(nsp_asio_get_stats(h,&stats)==0&&stats.source_frame==heldSource&&stats.starvation_frames==heldStarvation,"Hold advanced source or counted starvation");
        for(float v:current->memory[0])Expect(v==0,"Held callback did not emit silence");
        Expect(nsp_asio_hold(h,0)==0,"Resume failed");
        for(unsigned i=0;i<4;++i){Expect(nsp_asio_service(h)==0,"Service failed");current->Tick();}
        Expect(current->memory[0].back()==-.25f&&current->memory[2].back()==.125f,"Copied PCM mapping failed");
        Expect(nsp_asio_finish_input(h)==0,"EOF failed");
        Expect(nsp_asio_submit(h,4096,samples.data(),1)==NSP_ASIO_STATE,"Submit after EOF accepted");
        const auto priorOpens=opens;
        current->callbacks.asioMessage(kAsioResetRequest,0,nullptr,nullptr);
        Expect(nsp_asio_service(h)==NSP_ASIO_DEVICE_ERROR&&opens==priorOpens,"Reset automatically reopened driver");
        char message[8]{};Expect(nsp_asio_last_error(message,sizeof(message))>sizeof(message)&&message[7]==0,"Error copy-out failed");
        Expect(nsp_asio_get_stats(h,&stats)==0&&stats.faulted,"Driver fault not latched");
        Expect(nsp_asio_service(h)==NSP_ASIO_STATE,"Faulted device continued");
        Expect(nsp_asio_stop(h)==0,"Stop failed");
        Expect(nsp_asio_start(h)==NSP_ASIO_STATE,"Stopped device restarted");
        Expect(nsp_asio_close(&h)==0&&!h&&opens==releases,"Close failed or leaked");
        Expect(nsp_asio_close(&h)==0,"Null close not idempotent");
        sources[0]=1;physical[0]=7;failCreate=1;
        Expect(nsp_asio_c_open(&config,&h)==NSP_ASIO_DEVICE_ERROR&&!h&&opens==releases,"Open failure leaked driver");
        failCreate=0;
        Expect(nsp_asio_c_open(&config,&h)==0&&nsp_asio_close(&h)==0,"Failed open poisoned later open");
        // NSP's existing writer hands off 480-frame, 12-channel interleaved PCM.
        uint32_t layout[]{11,10,9,8,7,6,5,4,3,2,1,0};
        int32_t ports[]{0,2,4,6,8,10,12,14,16,18,20,22};
        config.source_channels=12;config.output_channels=12;
        config.source_channel_indices=layout;config.physical_channels=ports;
        Expect(nsp_asio_open(&config,&h)==0&&nsp_asio_start(h)==0,"714 open/start failed");
        Expect(nsp_asio_get_stats(h,&stats)==0,"714 stats failed");
        const uint32_t blocks=(stats.minimum_prime_frames+479)/480+2;
        std::vector<float> block(480*12);
        for(unsigned f=0;f<480;++f)for(unsigned c=0;c<12;++c)block[f*12+c]=float(c+1)/4.f;
        for(uint32_t b=0;b<blocks;++b)
            Expect(nsp_asio_submit(h,uint64_t(b)*480,block.data(),480)==0,"480-frame continuity failed");
        Expect(nsp_asio_get_stats(h,&stats)==0&&stats.submitted_frames==uint64_t(blocks)*480,"Source frame unit mismatch");
        const auto submitted=stats.submitted_frames;
        Expect(nsp_asio_submit(h,submitted+1,block.data(),480)==NSP_ASIO_INVALID,"714 frame gap accepted");
        Expect(nsp_asio_get_stats(h,&stats)==0&&stats.submitted_frames==submitted,"Rejected frame advanced timeline");
        current->Tick();
        Expect(nsp_asio_get_stats(h,&stats)==0&&stats.source_frame==0&&!stats.primed,"714 silent start consumed data");
        Expect(nsp_asio_prime(h)==0,"714 prime failed");
        bool observed=false;
        for(unsigned tick=0;tick<64&&!observed;++tick){
            Expect(nsp_asio_service(h)==0,"714 service failed");current->Tick();
            if(current->memory[0].back()!=0){
                for(unsigned c=0;c<12;++c){
                    Expect(current->buffers[c].channelNum==ports[c],"714 physical port mismatch");
                    Expect(current->memory[c*2].back()==float(layout[c]+1)/4.f,"714 PCM mapping or float passthrough mismatch");
                }
                observed=true;
            }
        }
        Expect(observed,"714 PCM not observed");
        Expect(nsp_asio_finish_input(h)==0,"714 EOF failed");
        bool drained=false;
        for(unsigned tick=0;tick<128;++tick){
            Expect(nsp_asio_service(h)==0,"714 EOF service failed");current->Tick();
            Expect(nsp_asio_get_stats(h,&stats)==0,"714 EOF stats failed");
            if(stats.drained){drained=true;break;}
        }
        Expect(drained,"714 EOF did not drain");
        Expect(nsp_asio_stop(h)==0&&nsp_asio_close(&h)==0&&!h&&opens==releases,"714 lifecycle leaked");
        float floating{};std::int16_t integer{};
        spatial::WriteAsioSample(&floating,ASIOSTFloat32LSB,2.5,true);
        Expect(floating==2.5f,"Float format silently limited output");
        spatial::WriteAsioSample(&floating,ASIOSTFloat32LSB,2.5);
        Expect(floating==1.f,"Legacy format behavior changed");
        spatial::WriteAsioSample(&integer,ASIOSTInt16LSB,2.5,true);
        Expect(integer==32767,"Integer format did not saturate");
        std::cout<<"ASIO C ABI: C layout/calls, config/PCM ownership, thread/lifecycle guards, fault latch and cleanup passed\n";
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
