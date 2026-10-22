// SPDX-License-Identifier: Apache-2.0
#include "asio_test_driver.h"
#include "asio_backend.h"
int main() {
    using namespace spatial;
    try {
        AsioDeviceConfig config;
        config.name=L"PCM only"; config.endpointFilter=L"asio:test";
        config.sourceChannels=2; config.sourceChannelIndices={1,0}; config.physicalChannels={7,3};
        {
            auto invalid=config; invalid.sourceChannelIndices={2};
            bool rejected=false;
            try { AsioRenderer bad(invalid); } catch(const std::invalid_argument&) { rejected=true; }
            Expect(rejected && opens==0,"Invalid source index opened driver");
        }
        {
            auto invalid=config; invalid.physicalChannels={7};
            bool rejected=false;
            try { AsioRenderer bad(invalid); } catch(const std::invalid_argument&) { rejected=true; }
            Expect(rejected && opens==0,"Invalid channel mapping opened driver");
        }
        {
            AsioRenderer device(config);
            Expect(current->buffers[0].channelNum==7 && current->buffers[1].channelNum==3,"Device mapping changed");
            device.Start();
            current->Tick();
            Expect(device.Stats()[0].sourceFrame==0 && device.Stats()[0].starvationFrames==0,"Idle start advanced source");
            for(std::size_t c=0;c<2;++c) for(float sample:current->memory[c*2])
                Expect(sample==0,"Idle callback produced audio");
            InterleavedPcmQueue wrong(1); wrong.AppendFloat({.1f});
            bool rejected=false;
            try { device.Prime(wrong); } catch(const std::invalid_argument&) { rejected=true; }
            Expect(rejected,"Mismatched queue channels accepted");
            InterleavedPcmQueue pcm(2);
            std::vector<float> block(4096*2);
            for(std::size_t i=0;i<4096;++i){block[i*2]=.125f;block[i*2+1]=-.25f;}
            pcm.AppendFloat(std::move(block)); device.Prime(pcm);
            for(unsigned tick=0;tick<4;++tick){ device.Service(pcm,false,0); current->Tick(); }
            for(std::size_t i=0;i<128;++i){
                Expect(current->memory[0][i]==-.25f && current->memory[2][i]==.125f,"PCM mapping or neutral processing changed");
            }
            device.Stop();
        }
        Expect(opens==releases,"Device instance leaked");
        std::cout<<"PCM-only ASIO device: validation, mapping, silent start, queue contract and cleanup passed\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
