// SPDX-License-Identifier: Apache-2.0
#include "diagnostics.h"
#include "diagnostic_file.h"
#include "json_text.h"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <fstream>
#include <thread>
#include <psapi.h>

namespace spatial {
void DiagnosticRing::CopyIn(std::uint64_t offset,const void* data,std::size_t bytes) noexcept {
    if(!bytes)return;
    const auto at=static_cast<std::size_t>(offset%data_.size()), first=std::min(bytes,data_.size()-at);
    std::memcpy(data_.data()+at,data,first);std::memcpy(data_.data(),static_cast<const std::uint8_t*>(data)+first,bytes-first);
}
void DiagnosticRing::CopyOut(std::uint64_t offset,void* data,std::size_t bytes) const noexcept {
    const auto at=static_cast<std::size_t>(offset%data_.size()), first=std::min(bytes,data_.size()-at);
    std::memcpy(data,data_.data()+at,first);std::memcpy(static_cast<std::uint8_t*>(data)+first,data_.data(),bytes-first);
}
bool DiagnosticRing::TryWrite(std::uint32_t kind,std::uint64_t stamp,std::span<const DiagnosticPart> parts) noexcept {
    std::size_t size=0;for(const auto& p:parts)size+=p.bytes;
    const auto ordinal=ordinal_++;
    if(size>data_.size()-sizeof(DiagnosticHeader)){++dropped;return false;}
    const auto w=write_.load(std::memory_order_relaxed),r=read_.load(std::memory_order_acquire);
    const auto total=size+sizeof(DiagnosticHeader);
    if(w-r+total>data_.size()){++dropped;return false;}
    DiagnosticHeader h{0x31444153,kind,static_cast<std::uint32_t>(size),0,stamp,ordinal};
    CopyIn(w,&h,sizeof(h));auto at=w+sizeof(h);
    for(const auto& p:parts){CopyIn(at,p.data,p.bytes);at+=p.bytes;}
    write_.store(at,std::memory_order_release);
    const auto used=at-r;if(used>highWater.load(std::memory_order_relaxed))highWater.store(used,std::memory_order_relaxed);
    return true;
}
bool DiagnosticRing::Pop(std::vector<std::uint8_t>& record) {
    const auto r=read_.load(std::memory_order_relaxed),w=write_.load(std::memory_order_acquire);
    if(r==w)return false;
    DiagnosticHeader h;CopyOut(r,&h,sizeof(h));
    const auto size=sizeof(h)+h.bytes;
    if(h.magic!=0x31444153 || size>data_.size() || size>w-r)throw std::runtime_error("Diagnostic ring corruption");
    record.resize(size);CopyOut(r,record.data(),size);read_.store(r+size,std::memory_order_release);return true;
}
namespace {
constexpr std::uint64_t MiB=1024*1024;
// Only manifest-owned sessions and their manifest-owned flat source folders.
// Never follow junctions; unknown entries prevent directory removal.
void RetainSessions(const std::filesystem::path& root) {
    std::vector<std::filesystem::path> sessions;
    for(const auto& entry:std::filesystem::directory_iterator(root)) {
        const auto attributes=GetFileAttributesW(entry.path().c_str());
        if(attributes==INVALID_FILE_ATTRIBUTES||(attributes&FILE_ATTRIBUTE_REPARSE_POINT)||!entry.is_directory())continue;
        if(!entry.path().filename().wstring().starts_with(L"session-"))continue;
        std::ifstream manifest(entry.path()/L"manifest.json");std::string first;std::getline(manifest,first);
        if(first.find("SpatialASIO.Diagnostics.v1")!=std::string::npos)sessions.push_back(entry.path());
    }
    std::sort(sessions.begin(),sessions.end());
    while(sessions.size()>2){
        const auto old=std::filesystem::weakly_canonical(sessions.front());sessions.erase(sessions.begin());
        if(old.parent_path()!=std::filesystem::weakly_canonical(root))continue;
        for(const auto& source:std::filesystem::directory_iterator(old)){
            const auto a=GetFileAttributesW(source.path().c_str());
            if(a==INVALID_FILE_ATTRIBUTES||(a&FILE_ATTRIBUTE_REPARSE_POINT)||!source.is_directory()||!source.path().filename().wstring().starts_with(L"source-"))continue;
            const auto resolved=std::filesystem::weakly_canonical(source.path());if(resolved.parent_path()!=old)continue;
            std::ifstream owned(source.path()/L"source-report.json");std::string content((std::istreambuf_iterator<char>(owned)),{});
            if(content.find("SpatialASIO.SourceProbe.v1")==std::string::npos)continue;owned.close();
            for(const auto& entry:std::filesystem::directory_iterator(resolved)){
                const auto flags=GetFileAttributesW(entry.path().c_str());
                if(flags==INVALID_FILE_ATTRIBUTES||(flags&FILE_ATTRIBUTE_REPARSE_POINT)||!entry.is_regular_file())continue;
                const auto ext=entry.path().extension().wstring();
                if(ext==L".sad"||ext==L".json"||ext==L".jsonl")std::filesystem::remove(entry.path());
            }
            if(std::filesystem::is_empty(resolved))std::filesystem::remove(resolved);
        }
        for(const auto& entry:std::filesystem::directory_iterator(old)){
            const auto a=GetFileAttributesW(entry.path().c_str());
            if(a==INVALID_FILE_ATTRIBUTES||(a&FILE_ATTRIBUTE_REPARSE_POINT)||!entry.is_regular_file())continue;
            const auto ext=entry.path().extension().wstring();
            if(ext==L".sad"||ext==L".json"||ext==L".jsonl"||ext==L".ini"||ext==L".html"||ext==L".csv"||ext==L".log")std::filesystem::remove(entry.path());
        }
        if(std::filesystem::is_empty(old))std::filesystem::remove(old);
    }
}
}
struct Diagnostics::Impl {
    std::filesystem::path directory;
    std::array<std::unique_ptr<DiagnosticRing>,5> rings;
    std::atomic<bool> stopping{false},failed{false},raw{false};
    std::atomic<unsigned> mark{0};
    std::thread writer;std::atomic<bool> success{false};bool autoMarked{},manualMarked{};std::string error;
    std::uint64_t startNs{Diagnostics::NowNs()};
    void Write(){
        SetThreadPriority(GetCurrentThread(),THREAD_PRIORITY_BELOW_NORMAL);
        try {
            RollingDiagnosticFile capture(directory,"capture",8,32*MiB),render(directory,"render",4,48*MiB),asio(directory,"asio",4,32*MiB),events(directory,"events",3,16*MiB,true);
            RollingDiagnosticFile overview(directory,"overview",1,128*MiB,true);
            RollingDiagnosticFile ordinary(directory,"ordinary",4,16*MiB);
            RollingDiagnosticFile* files[]{&capture,&render,&asio,&events,&ordinary};
            std::vector<std::uint8_t> record;record.reserve(10*MiB);
            unsigned incident=0,window=0,pendingMark=0;std::uint64_t freezeAt=0,lastFlush=0,lastSpace=0,lastOverview=0;
            auto ticks=[](FILETIME t){return (static_cast<std::uint64_t>(t.dwHighDateTime)<<32)|t.dwLowDateTime;};
            while(true){
                bool activity=false;
                // Fair draining: busy input PCM cannot starve the event journal.
                for(unsigned i=0;i<5;++i)for(unsigned n=0;n<32&&rings[i]->Pop(record);++n){
                    activity=true;
                    if(i==3){
                        const auto* h=reinterpret_cast<const DiagnosticHeader*>(record.data());const auto* data=reinterpret_cast<const char*>(record.data()+sizeof(*h));files[i]->Write(data,h->bytes);
                        const std::string_view line(data,h->bytes);
                        if(line.find("\"event\":\"packet\"")==std::string_view::npos&&line.find("\"event\":\"worker_gap\"")==std::string_view::npos){
                            if(line.find("\"event\":\"status\"")==std::string_view::npos||h->qpcNs-lastOverview>=1000000000ULL){overview.Write(data,h->bytes);lastOverview=h->qpcNs;}
                        }
                    }
                    else if(raw.load(std::memory_order_relaxed))files[i]->Write(reinterpret_cast<const char*>(record.data()),record.size());
                }
                const auto now=Diagnostics::NowNs();pendingMark|=mark.exchange(0);
                if(freezeAt && (now>=freezeAt||stopping)){
                    for(unsigned i: {0U,1U,2U,4U})files[i]->EndIncident();freezeAt=0;
                }
                if(pendingMark&&raw){
                    ++incident;
                    if(!freezeAt){window=incident;for(unsigned i: {0U,1U,2U,4U})files[i]->BeginIncident("incident-"+std::to_string(window));}
                    std::ofstream info(directory/("incident-"+std::to_string(incident)+".json"));
                    info<<"{\"qpcNs\":"<<now<<",\"manual\":"<<((pendingMark&2)?"true":"false")<<",\"automatic\":"<<((pendingMark&1)?"true":"false")
                        <<",\"postSeconds\":"<<DiagnosticPostSeconds<<",\"captureWindow\":"<<window<<"}";
                    if(!info)throw std::runtime_error("Cannot write incident marker");
                    pendingMark=0;freezeAt=now+static_cast<std::uint64_t>(DiagnosticPostSeconds)*1000000000ULL;
                }
                if(now-lastFlush>=1000000000ULL){
                    FILETIME created{},exit{},kernel{},user{},idle{},systemKernel{},systemUser{};
                    GetProcessTimes(GetCurrentProcess(),&created,&exit,&kernel,&user);GetSystemTimes(&idle,&systemKernel,&systemUser);
                    PROCESS_MEMORY_COUNTERS memory{};GetProcessMemoryInfo(GetCurrentProcess(),&memory,sizeof(memory));
                    MEMORYSTATUSEX total{sizeof(total)};GlobalMemoryStatusEx(&total);IO_COUNTERS io{};GetProcessIoCounters(GetCurrentProcess(),&io);
                    std::ostringstream sample;sample<<"{\"qpcNs\":"<<now<<",\"data\":{\"event\":\"system_sample\",\"processKernel100ns\":"<<ticks(kernel)<<",\"processUser100ns\":"<<ticks(user)
                        <<",\"systemIdle100ns\":"<<ticks(idle)<<",\"systemKernel100ns\":"<<ticks(systemKernel)<<",\"systemUser100ns\":"<<ticks(systemUser)
                        <<",\"workingSetBytes\":"<<memory.WorkingSetSize<<",\"pageFaultCount\":"<<memory.PageFaultCount<<",\"availableMemoryBytes\":"<<total.ullAvailPhys
                        <<",\"processReadBytes\":"<<io.ReadTransferCount<<",\"processWriteBytes\":"<<io.WriteTransferCount<<",\"rawAudioActive\":"<<(raw?"true":"false")<<"}}\n";
                    const auto text=sample.str();events.Write(text.data(),text.size());overview.Write(text.data(),text.size());
                    for(auto* f:files)f->Flush();overview.Flush();lastFlush=now;
                }
                if(now-lastSpace>=5000000000ULL){
                    std::error_code ec;const auto space=std::filesystem::space(directory,ec);
                    if(!ec&&space.available<512*MiB)raw.store(false);lastSpace=now;
                }
                if(stopping&&!activity)break;
                if(!activity)Sleep(4);
            }
            if(freezeAt)for(unsigned i: {0U,1U,2U,4U})files[i]->EndIncident();
            for(auto* f:files)f->Close();
            overview.Close();
        }catch(const std::exception& e){error=e.what();failed=true;raw=false;}
        try{
            std::ofstream summary(directory/"engine-end.json");
            summary<<"{\"success\":"<<(success?"true":"false")<<",\"loggerFailed\":"<<(failed?"true":"false")
                <<",\"rawAudioActive\":"<<(raw?"true":"false")<<",\"startNs\":"<<startNs<<",\"endNs\":"<<Diagnostics::NowNs()<<",\"rings\":[";
            for(unsigned i=0;i<5;++i){if(i)summary<<',';summary<<"{\"stream\":"<<i<<",\"droppedRecords\":"<<rings[i]->dropped.load()<<",\"highWaterBytes\":"<<rings[i]->highWater.load()<<",\"pendingBytes\":"<<rings[i]->Used()<<'}';}
            summary<<"],\"error\":"<<JsonString(std::wstring(error.begin(),error.end()))<<'}';
        }catch(...){}
    }
};
std::uint64_t Diagnostics::NowNs() noexcept {
    static const auto frequency=[] {LARGE_INTEGER f{};QueryPerformanceFrequency(&f);return f.QuadPart;}();
    LARGE_INTEGER q{};QueryPerformanceCounter(&q);
    return static_cast<std::uint64_t>(q.QuadPart/frequency)*1000000000ULL+static_cast<std::uint64_t>(q.QuadPart%frequency)*1000000000ULL/frequency;
}
Diagnostics::Diagnostics(const std::filesystem::path& logs,const std::filesystem::path& profile,bool enabled,bool audio) {
    if(!enabled)return;
    try{
        auto p=std::make_unique<Impl>();const auto root=std::filesystem::absolute(logs)/L"Diagnostics";
        std::filesystem::create_directories(root);RetainSessions(root);
        SYSTEMTIME utc{};GetSystemTime(&utc);wchar_t name[128]{};
        swprintf_s(name,L"session-%04u%02u%02u-%02u%02u%02u-%03u-%lu",utc.wYear,utc.wMonth,utc.wDay,utc.wHour,utc.wMinute,utc.wSecond,utc.wMilliseconds,GetCurrentProcessId());
        p->directory=root/name;std::filesystem::create_directory(p->directory);
        std::filesystem::copy_file(profile,p->directory/L"profile.ini");
        SYSTEM_INFO sys{};GetNativeSystemInfo(&sys);MEMORYSTATUSEX memory{sizeof(memory)};GlobalMemoryStatusEx(&memory);
        std::ofstream manifest(p->directory/L"manifest.json");
        manifest<<"{\"schema\":\"SpatialASIO.Diagnostics.v1\",\"version\":\""<<SPATIALASIO_VERSION<<"\",\"rendererRevision\":\"direct-layout-collision-isolation-20261014\",\"pid\":"<<GetCurrentProcessId()
            <<",\"qpcNs\":"<<p->startNs<<",\"utc\":"<<JsonString(name)<<",\"logicalProcessors\":"<<sys.dwNumberOfProcessors<<",\"physicalMemoryBytes\":"<<memory.ullTotalPhys
            <<",\"rawAudio\":"<<(audio?"true":"false")<<",\"captureRingMiB\":32,\"workerRingMiB\":16,\"asioRingMiB\":16,\"ordinaryRingMiB\":16,\"eventRingMiB\":4,\"retainedSessions\":3,\"maxIncidents\":2,\"incidentPostSeconds\":600,\"incidentAppendSegments\":true,\"rawRollingMiB\":640}";
        if(!manifest)throw std::runtime_error("Cannot create diagnostics manifest");manifest.close();
        const std::size_t sizes[]{32*MiB,16*MiB,16*MiB,4*MiB,16*MiB};
        for(unsigned i=0;i<5;++i)p->rings[i]=std::make_unique<DiagnosticRing>(sizes[i]);
        p->raw=audio;impl_=std::move(p);impl_->writer=std::thread([this]{impl_->Write();});
    }catch(...){impl_.reset();}
}
Diagnostics::~Diagnostics(){Finish(false);}
const std::filesystem::path& Diagnostics::Directory()const{static const std::filesystem::path empty;return impl_?impl_->directory:empty;}
bool Diagnostics::Enabled()const noexcept{return impl_!=nullptr;}
bool Diagnostics::AudioEnabled()const noexcept{return impl_&&impl_->raw.load(std::memory_order_relaxed)&&!impl_->failed.load(std::memory_order_relaxed);}
void Diagnostics::Record(Stream s,std::uint32_t kind,std::uint64_t stamp,std::span<const DiagnosticPart> parts)noexcept{
    if(impl_&&!impl_->failed.load(std::memory_order_relaxed)&&(s==Events||AudioEnabled()))impl_->rings[s]->TryWrite(kind,stamp,parts);
}
void Diagnostics::Event(const std::string& json){if(!impl_)return;const auto time=NowNs();const std::string line="{\"qpcNs\":"+std::to_string(time)+",\"data\":"+json+"}\n";const DiagnosticPart p{line.data(),line.size()};Record(Events,4,time,{&p,1});}
void Diagnostics::Mark(bool manual){if(!impl_)return;auto& done=manual?impl_->manualMarked:impl_->autoMarked;if(done){Event("{\"event\":\"incident_limit\",\"manual\":"+std::string(manual?"true":"false")+"}");return;}done=true;impl_->mark.fetch_or(manual?2:1);Event("{\"event\":\"incident_requested\",\"manual\":"+std::string(manual?"true":"false")+"}");}
std::uint64_t Diagnostics::Dropped()const noexcept{std::uint64_t n=0;if(impl_)for(const auto& r:impl_->rings)n+=r->dropped.load();return n;}
bool Diagnostics::Failed()const noexcept{return impl_&&impl_->failed.load();}
void Diagnostics::Finish(bool success){if(impl_&&impl_->writer.joinable()){impl_->success=success;impl_->stopping=true;impl_->writer.join();}}
}
