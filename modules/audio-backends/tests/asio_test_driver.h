// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "audio_platform.h"
#include "iasiodrv.h"
#include <array>
#include <cstdint>
#include <vector>
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
