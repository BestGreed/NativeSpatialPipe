// SPDX-License-Identifier: Apache-2.0
#include "audio_platform.h"
#include "asio_backend.h"
#include "speaker_layout.h"
#include "diagnostics.h"
#include "output_deadline.h"
#include "audio_gap_ramp.h"
#include "iasiodrv.h"
#include <array>
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <fstream>

namespace {
void Expect(bool value, const char* text) { if (!value) throw std::runtime_error(text); }
long preferred=128, failCreate=0;
double driverRate=48000;
std::uint64_t testClockNs=0;
bool slowStart=false;
int opens=0, releases=0, disposes=0, stops=0;
class FakeDriver : public IASIO {
public:
    ASIOCallbacks callbacks{};
    std::vector<ASIOBufferInfo> buffers;
    std::vector<std::vector<float>> memory;
    bool running=false;
    ULONG refs=1;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID,void**) override { return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { auto n=--refs; if (!n) { ++releases; delete this; } return n; }
    ASIOBool init(void*) override { return ASIOTrue; }
    void getDriverName(char* s) override { strcpy_s(s,32,"Test"); }
    long getDriverVersion() override { return 1; }
    void getErrorMessage(char* s) override { strcpy_s(s,124,"Test error"); }
    ASIOError start() override {
        running=true;
        if(slowStart) { Sleep(500);callbacks.bufferSwitch(0,ASIOTrue); }
        return ASE_OK;
    }
    ASIOError stop() override { running=false; ++stops; return ASE_OK; }
    ASIOError getChannels(long* i,long* o) override { *i=0;*o=32;return ASE_OK; }
    ASIOError getLatencies(long* i,long* o) override { *i=*o=0;return ASE_OK; }
    ASIOError getBufferSize(long* a,long* b,long* p,long* g) override { *a=64;*b=256;*p=preferred;*g=64;return ASE_OK; }
    ASIOError canSampleRate(double r) override { return r==44100||r==48000||r==96000||r==192000?ASE_OK:ASE_NoClock; }
    ASIOError getSampleRate(double* r) override { *r=driverRate;return ASE_OK; }
    ASIOError setSampleRate(double r) override {if(canSampleRate(r)!=ASE_OK)return ASE_NoClock;driverRate=r;return ASE_OK;}
    ASIOError getClockSources(ASIOClockSource*,long* n) override { *n=0;return ASE_OK; }
    ASIOError setClockSource(long) override { return ASE_NotPresent; }
    ASIOError getSamplePosition(ASIOSamples*,ASIOTimeStamp*) override { return ASE_NotPresent; }
    ASIOError getChannelInfo(ASIOChannelInfo* i) override { i->type=ASIOSTFloat32LSB;return ASE_OK; }
    ASIOError createBuffers(ASIOBufferInfo* out,long n,long size,ASIOCallbacks* cb) override {
        if (failCreate) return ASE_InvalidMode;
        callbacks=*cb; memory.assign(n*2,std::vector<float>(size,123));
        for(long c=0;c<n;++c) for(int half=0;half<2;++half) out[c].buffers[half]=memory[c*2+half].data();
        buffers.assign(out,out+n); return ASE_OK;
    }
    ASIOError disposeBuffers() override { Expect(!running,"dispose while started"); ++disposes; return ASE_OK; }
    ASIOError controlPanel() override { return ASE_OK; }
    ASIOError future(long,void*) override { return ASE_NotPresent; }
    ASIOError outputReady() override { return ASE_OK; }
    void Tick(int index=0) { Expect(running,"callback without start"); callbacks.bufferSwitch(index,ASIOTrue); }
};
FakeDriver* current=nullptr;
}
namespace spatial {
IASIO* MakeTestAsioDriver() { ++opens; return current=new FakeDriver; }
std::uint64_t TestAsioClockNs(){return testClockNs;}
}
int main() {
    using namespace spatial;
    try {
        for(double rate:{44100.,48000.,96000.,192000.}) {
            AudioGapRamp ramp;ramp.Reset(rate);
            std::array<float,3> frame{.25f,-.125f,0.f};
            for(unsigned n=0;n<400;++n){ramp.Process(frame,true);Expect(frame[0]==.25f&&frame[1]==-.125f&&frame[2]==0,"Healthy PCM changed by the gap ramp");}
            const auto length=unsigned(std::lround(rate*.002));float previous=.25f;
            for(unsigned n=0;n<length+5;++n){
                ramp.Process(frame,false);
                Expect(frame[0]>=0&&frame[0]<=previous&&previous-frame[0]<.4f/length,"Gap fade-out has a hard edge");
                Expect(frame[1]==-frame[0]/2&&frame[2]==0,"Gap fade-out changed relative channel gain");previous=frame[0];
            }
            Expect(frame[0]==0,"Gap ramp repeated a held sample indefinitely");
            for(unsigned n=0;n<length+5;++n){
                frame={.25f,-.125f,0.f};ramp.Process(frame,true);
                Expect(frame[0]>=previous&&frame[0]<=.25f&&frame[0]-previous<.4f/length,"Gap fade-in has a hard edge");
                Expect(frame[1]==-frame[0]/2&&frame[2]==0,"Gap fade-in split channels");previous=frame[0];
            }
            Expect(frame[0]==.25f,"Gap ramp did not return to exact healthy PCM");
        }
        InterleavedPcmQueue precision(2);
        precision.AppendFloat({1e-8F, -1e-8F});
        Expect(precision.Sample(0,0)==1e-8F && precision.Sample(0,1)==-1e-8F,"float PCM quantized before ASIO");
        SpeakerLayout layout;
        for(int i=0;i<16;++i) { SpeakerDefinition speaker; speaker.name=L"S"+std::to_wstring(i); layout.speakers.push_back(speaker); }
        OutputRouteDefinition route; route.name=L"ASIO";route.endpointFilter=L"asio:test";
        for(int i=0;i<16;++i) { route.speakerIndices.push_back(i);route.physicalChannels.push_back(31-i); }
        layout.outputs.push_back(route);
        const auto diagRoot=std::filesystem::temp_directory_path()/(L"SpatialASIO-AsioTest-"+std::to_wstring(GetCurrentProcessId()));std::filesystem::create_directories(diagRoot);
        const auto profile=diagRoot/L"profile.ini";std::ofstream(profile)<<"test";
        Diagnostics diagnostics(diagRoot,profile);
        {
            AsioRenderer renderer(layout,1,RendererLatencyMode::Balanced);
            renderer.SetDiagnostics(&diagnostics);
            for(int c=0;c<16;++c) {
                Expect(current->buffers[c].channelNum==31-c,"physical mapping lost");
                for(float x:current->memory[c*2]) Expect(x==0,"initial buffer not silent");
            }
            InterleavedPcmQueue queue(16);
            std::vector<std::int16_t> samples(512*16);
            for(int f=0;f<512;++f) for(int c=0;c<16;++c) samples[f*16+c]=static_cast<std::int16_t>(1024*(c+1));
            queue.Append(std::move(samples)); renderer.Prime(queue); renderer.Start();
            renderer.Service(queue,true,0); current->Tick();
            for(int c=0;c<16;++c) Expect(std::abs(current->memory[c*2][63]-(c+1)/32.f)<1e-6,"channel sample mismatch");
            const auto beforePause=renderer.Stats()[0].clockSeconds;
            renderer.Pause();
            Expect(!current->running && !renderer.IsStarted(),"pause did not stop callbacks");
            Expect(renderer.Stats()[0].clockSeconds==beforePause,"pause advanced the hardware clock");
            renderer.Start();
            // Producer pause drains two periods, then emits zeros and counts exact missing frames.
            current->Tick(1); current->Tick();
            Expect(renderer.Stats()[0].starvationFrames==128,"underrun count mismatch");
            for(auto& channel:current->memory) for(float value:channel) (void)value;
            for(int c=0;c<16;++c) for(float x:current->memory[c*2]) Expect(x==0,"underrun was not silent");
            // Buffer-size/reset requests rebuild on Service, preserving source at callback consumption.
            preferred=64;
            Expect(current->callbacks.asioMessage(kAsioBufferSizeChange,64,nullptr,nullptr)==1,"reset not acknowledged");
            renderer.Service(queue,true,0);
            Expect(opens==2 && disposes==1 && renderer.Stats()[0].bufferFrames==64,"reset lifecycle failed");
            current->Tick(); current->Tick(1);
            renderer.Stop();
        }
        Expect(opens==releases && disposes==2 && stops==3,"driver leaked after stop/pause");
        failCreate=1;
        bool failed=false; try { AsioRenderer bad(layout,1,RendererLatencyMode::Balanced); } catch(...) { failed=true; }
        Expect(failed && opens==releases,"createBuffers failure leaked driver");
        failCreate=0;
        { AsioRenderer next(layout,1,RendererLatencyMode::Balanced); }
        Expect(opens==releases,"driver could not reopen after failure");
        diagnostics.Finish(true);Expect(!diagnostics.Failed()&&diagnostics.Dropped()==0,"ASIO diagnostics failed");
        std::ifstream recorded(diagnostics.Directory()/L"asio-0.sad",std::ios::binary);
        DiagnosticHeader header{};AsioDiagnostic info{};recorded.read(reinterpret_cast<char*>(&header),sizeof(header));recorded.read(reinterpret_cast<char*>(&info),sizeof(info));
        Expect(header.kind==3&&info.frames==128&&info.channels==16&&info.missing==0,"ASIO evidence header mismatch");
        std::array<float,128> channel{};for(int c=0;c<16;++c){recorded.read(reinterpret_cast<char*>(channel.data()),sizeof(channel));for(auto sample:channel)Expect(sample==(c+1)/32.f,"diagnostics differs from actual driver buffer");}
        recorded.close();Expect(std::filesystem::weakly_canonical(diagRoot).parent_path()==std::filesystem::weakly_canonical(std::filesystem::temp_directory_path()),"unsafe cleanup");std::filesystem::remove_all(diagRoot);
        // Reproduce gradual depletion with a +500 ppm callback clock and a
        // QPC-paced worker. The corrected scheduling must retain every sample,
        // without changing the ASIO buffer size or interpolating at 48 kHz.
        preferred=128;SpeakerLayout clockLayout=layout;clockLayout.speakers.resize(2);
        clockLayout.outputs[0].speakerIndices={0,1};clockLayout.outputs[0].physicalChannels={0,1};
        // DVS can block start() for 500 ms. Start before capture with silence,
        // keep source frame zero across a long idle, then arm the same running
        // driver. Waiting/partial prebuffer must not leak or consume audio.
        {
            slowStart=true;AsioRenderer driver(clockLayout,1,RendererLatencyMode::Balanced);
            InterleavedPcmQueue pcm(2);driver.Start();slowStart=false;
            Expect(driver.IsStarted()&&driver.RequiredWriteFrame()==0,"silent start released PCM deadline");
            pcm.AppendFloat(std::vector<float>(64*2,.125f));
            for(unsigned n=0;n<400;++n){driver.Service(pcm,false,0);current->Tick();}
            Expect(driver.Stats()[0].sourceFrame==0&&driver.BufferedFrames(pcm)==64,"idle start consumed capture samples");
            Expect(driver.Stats()[0].starvationFrames==0,"waiting for capture counted as starvation");
            for(float x:current->memory[0])Expect(x==0,"partial prebuffer leaked to output");
            pcm.AppendFloat(std::vector<float>(192*2,.25f));driver.Prime(pcm);driver.Service(pcm,true,0);
            current->Tick();
            for(unsigned f=0;f<128;++f)Expect(current->memory[0][f]==(f<64?.125f:.25f),"silent-start handoff skipped or replayed PCM");
            current->Tick(1);Expect(driver.Stats()[0].starvationFrames==0,"primed handoff starved");
            Expect(driver.Stats()[0].sourceFrame==256,"silent start became permanent source offset");driver.Pause();
        }
        auto clockRun=[&](bool paced){
            AsioRenderer driver(clockLayout,1,RendererLatencyMode::Balanced);InterleavedPcmQueue pcm(2);
            auto sample=[](std::uint64_t f,unsigned c){return static_cast<float>((f+17*c)%997)/2048.f;};
            auto append=[&](std::uint64_t until){const auto first=pcm.EndFrame();std::vector<float> block;block.reserve(static_cast<std::size_t>(until-first)*2);for(auto f=first;f<until;++f)for(unsigned c=0;c<2;++c)block.push_back(sample(f,c));pcm.AppendFloat(std::move(block));};
            append(256);driver.Prime(pcm);driver.Start();driver.Service(pcm,true,0);
            for(std::uint64_t n=0;n<10000;++n){
                current->Tick();
                if(paced)for(unsigned c=0;c<2;++c)for(unsigned f=0;f<128;++f)if(current->memory[c*2][f]!=sample(n*128+f,c))throw std::runtime_error("clock pacing sample mismatch: callback="+std::to_string(n)+" frame="+std::to_string(f)+" channel="+std::to_string(c)+" actual="+std::to_string(current->memory[c*2][f])+" expected="+std::to_string(sample(n*128+f,c))+" missing="+std::to_string(driver.Stats()[0].starvationFrames));
                const auto wall=256+static_cast<std::uint64_t>((n+1)*128/1.0005);
                // Input and output audio clocks are close to each other but
                // both differ from QPC (+490 vs +500 ppm in this stress case).
                const auto captured=512+static_cast<std::uint64_t>((n+1)*128*1.00049/1.0005);
                const auto until=paced?OutputDeadline(wall,pcm.EndFrame(),captured,driver.RequiredWriteFrame()):std::max(wall,pcm.EndFrame());
                append(until);driver.Service(pcm,true,0);driver.DiscardConsumed(pcm);
            }
            const auto missing=driver.Stats()[0].starvationFrames;driver.Pause();return missing;
        };
        Expect(clockRun(false)>100,"clock drift regression did not reproduce old starvation");
        Expect(clockRun(true)==0,"sample-clock pacing still starves");
        {
            // In synchronized live mode a real underrun is elapsed output
            // time, not permission to play old source samples late afterwards.
            AsioRenderer driver(clockLayout,1,RendererLatencyMode::Balanced);driver.EnableClockSync();
            InterleavedPcmQueue pcm(2);pcm.AppendFloat(std::vector<float>(2048*2,.125f));
            driver.Prime(pcm);driver.Start();driver.Service(pcm,true,0);
            current->Tick();current->Tick();current->Tick();
            Expect(driver.Stats()[0].starvationFrames==0,"Clustered callbacks exhausted HQ precomputed headroom");
            current->Tick();
            Expect(driver.Stats()[0].starvationFrames==128,"Synchronized underrun count mismatch");
            driver.Service(pcm,true,0);
            Expect(driver.Stats()[0].sourceFrame>895&&driver.Stats()[0].sourceFrame<897,"Missing output time was replayed as stale PCM");
            // Live recovery ramps for 2 ms; healthy audio after the transition
            // is exact and both channels use the same envelope.
            current->Tick();
            for(unsigned f=0;f<128;++f){
                const auto v=current->memory[0][f];
                Expect(v>=0&&v<=.125f&&v==current->memory[2][f],"Underrun ramp overshot or split channel phase");
                if(f)Expect(v>=current->memory[0][f-1]&&v-current->memory[0][f-1]<.0021f,"Underrun recovery introduced a hard edge");
                if(f>=95)Expect(std::abs(v-.125f)<1e-7,"Underrun recovery changed steady PCM");
            }
            Expect(std::abs(driver.Stats()[0].resampleAdjustmentPpm)<10,"A short dropout became a clock-rate jump");driver.Pause();
        }
        for(double rate:{44100.,48000.,96000.,192000.}){
            // Exercise the actual producer/callback through a buffer rebuild,
            // including the last sinc lookahead at EOF, at each device rate.
            clockLayout.outputs[0].asioSampleRate=rate;preferred=128;
            AsioRenderer driver(clockLayout,1,RendererLatencyMode::Balanced);driver.EnableClockSync();
            InterleavedPcmQueue pcm(2);std::vector<float> signal(4096*2);
            for(unsigned f=0;f<4096;++f){signal[f*2]=.125f;signal[f*2+1]=-.125f;}
            pcm.AppendFloat(std::move(signal));driver.SetInputComplete();driver.Prime(pcm);driver.Start();
            double elapsed=0;unsigned nonzero=0;
            for(unsigned tick=0;tick<300;++tick){
                if(tick==4){preferred=64;current->callbacks.asioMessage(kAsioBufferSizeChange,64,nullptr,nullptr);}
                driver.Service(pcm,false,0);const auto frames=driver.Stats()[0].bufferFrames;
                elapsed+=frames/rate;testClockNs=1'000'000'000+std::uint64_t(elapsed*1e9);current->Tick();
                for(unsigned f=0;f<frames;++f){const auto value=current->memory[0][f];
                    Expect(std::isfinite(value)&&value>=0&&value<.15f,"HQ EOF/reset introduced an invalid sample");
                    Expect(current->memory[2][f]==-value,"HQ EOF/reset split channel phase");
                    if(value!=0)++nonzero;
                }
                driver.DiscardConsumed(pcm);
            }
            Expect(std::abs(double(nonzero)-std::ceil(4096*rate/48000))<=1,"HQ EOF/reset skipped or replayed source frames");
            Expect(!driver.Stats()[0].starvationFrames&&driver.MinimumFramesAvailable(pcm)==0,"HQ EOF/reset failed to drain");
            driver.Pause();testClockNs=0;
            std::cout<<"HQ ASIO "<<rate<<" Hz: EOF frames="<<nonzero<<", buffer reset passed\n";
        }
        Expect(opens==releases,"HQ reset/EOF leaked driver");
        {
            SpeakerLayout calibrated;
            calibrated.speakers={{L"L",0,0,-6.020599913279624},{L"R",0,0,0}};
            OutputRouteDefinition route;
            route.name=L"Legacy calibration";route.endpointFilter=L"asio:test";
            route.speakerIndices={0,1};route.delayMilliseconds=1;
            calibrated.outputs.push_back(route);
            AsioRenderer driver(calibrated,.5,RendererLatencyMode::Balanced);
            Expect(driver.Stats()[0].sourceFrame==-48,"Legacy delay mapping changed");
            InterleavedPcmQueue pcm(2);pcm.AppendFloat(std::vector<float>(8192,.25f));
            driver.Prime(pcm);driver.Start();
            for(unsigned tick=0;tick<4;++tick){driver.Service(pcm,false,0);current->Tick();}
            Expect(std::abs(current->memory[0].back()-.0625f)<1e-7f,"Legacy trim/gain changed");
            Expect(std::abs(current->memory[2].back()-.125f)<1e-7f,"Legacy master gain changed");
            driver.Stop();
        }
        Expect(opens==releases,"Legacy calibration leaked driver");
        std::cout<<"ASIO 16-channel callback mapping, silence, underrun, reset, stop and failure cleanup passed\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
