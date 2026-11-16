// Ordinary V1 queue reader: no target writes, injection, or audio interception.
#define NOMINMAX
#include "pcm_ring.h"
#include "queue_probe.h"
#include <mmdeviceapi.h>
#include <audiopolicy.h>
#include <mmreg.h>
#include <tlhelp32.h>
#include <bcrypt.h>
#include <avrt.h>
#include <atomic>
#include <algorithm>
#include <mutex>
#include <thread>
#include <vector>
#include <set>
#include <map>
#include <string>
#include <cstdio>
#include <cstring>
#include "browser_identity_reader.h"
#include "pcm_source_pool.h"
#include <array>
#include <sstream>

struct PcmSourceTransport {
    HANDLE mapping = nullptr, event = nullptr;
    PcmRing* ring = nullptr;
    std::atomic<uint64_t> lastProgress{0}, activeLease{0}, packets{0}, frames{0};
    std::atomic<uint32_t> generation{0};
    ~PcmSourceTransport() {
        if (ring) UnmapViewOfFile(ring);
        if (mapping) CloseHandle(mapping);
        if (event) CloseHandle(event);
    }
    bool prepare(const std::wstring& name, const std::wstring& eventName) {
        HANDLE newMapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(PcmRing), name.c_str());
        if (!newMapping) return false;
        if (GetLastError() == ERROR_ALREADY_EXISTS) { CloseHandle(newMapping); return false; }
        HANDLE newEvent = CreateEventW(nullptr, FALSE, FALSE, eventName.c_str());
        if (!newEvent) { CloseHandle(newMapping); return false; }
        if (GetLastError() == ERROR_ALREADY_EXISTS) { CloseHandle(newEvent); CloseHandle(newMapping); return false; }
        auto* newRing = static_cast<PcmRing*>(MapViewOfFile(newMapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(PcmRing)));
        if (!newRing) { CloseHandle(newEvent); CloseHandle(newMapping); return false; }
        memset(newRing, 0, sizeof(PcmRing));
        newRing->magic = PCM_MAGIC;
        newRing->version = 2;
        newRing->slots = PCM_SLOTS;
        newRing->slotBytes = sizeof(PcmSlot);
        mapping = newMapping;
        event = newEvent;
        ring = newRing;
        return true;
    }

};

struct PcmCapture : QueueProbe {
    HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    std::thread discover, reader;
    std::mutex lock;
    bool multi = false;
    std::wstring baseName, baseEvent;
    PcmSourcePool pool;
    std::array<QueueInfo, PCM_SOURCE_LIMIT> queues{};
    std::array<PcmSourceTransport, PCM_SOURCE_LIMIT> transports;
    std::atomic<uint32_t> preparedMask{0}, activatedMask{0};
    BrowserIdentityReader browserIdentity;
    std::atomic<uint32_t> sourceTabId{0};
    std::atomic<uint64_t> polls{0}, packets{0}, frames{0}, early{0}, dropped{0}, resets{0}, errors{0}, discoveries{0};
    std::atomic<uint32_t> candidates{0}, state{0}, queuesFound{0};

    static uint64_t process_started(DWORD pid) {
        HANDLE owner = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
        if (!owner) return 0;
        FILETIME created{}, exited{}, kernel{}, user{};
        const BOOL ok = GetProcessTimes(owner, &created, &exited, &kernel, &user);
        CloseHandle(owner);
        return ok ? (uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime : 0;
    }

    static PcmSourceKey key(const QueueInfo& q, uint64_t started) {
        return {q.base, q.mapping, q.control, started, q.pid, q.start, q.capacity, q.align, q.channels, q.mask};
    }

    bool prepare(uint32_t slot) {
        if (preparedMask.load(std::memory_order_acquire) & (1u << slot)) return true;
        const auto suffix = slot ? L"-" + std::to_wstring(slot) : L"";
        if (!transports[slot].prepare(baseName + suffix, baseEvent + suffix)) {
            ++errors;
            return false;
        }
        preparedMask.fetch_or(1u << slot, std::memory_order_release);
        return true;
    }

    void select_sources(const std::vector<QueueInfo>& advancing) {
        const uint64_t now = pcm_qpc_ns();
        std::vector<PcmSourceKey> keys;
        keys.reserve(advancing.size());
        std::map<uint32_t, uint64_t> started;
        for (const auto& q : advancing) {
            auto [it, inserted] = started.try_emplace(q.pid, 0);
            if (inserted) it->second = process_started(q.pid);
            keys.push_back(key(q, it->second));
        }
        {
            std::lock_guard guard(lock);
            for (uint32_t i = 0; i < PCM_SOURCE_LIMIT; ++i) {
                auto& binding = pool.slots[i];
                const bool replaced = binding.active && std::any_of(keys.begin(), keys.end(), [&](const auto& current) {
                    return current.base == binding.key.base && current != binding.key;
                });
                if (replaced) pool.retire(i, binding.lease);
                binding.lastProgress = (std::max)(binding.lastProgress, transports[i].lastProgress.load());
                if (binding.active && std::find(keys.begin(), keys.end(), binding.key) != keys.end())
                    binding.lastProgress = now;
            }
            if (!multi && advancing.size() != 1) pool.retire(0, pool.slots[0].lease);
            pool.expire(now);
            for (uint32_t i = 0; i < PCM_SOURCE_LIMIT; ++i) {
                if (!pool.slots[i].active) transports[i].activeLease.store(0, std::memory_order_release);
            }
        }
        if (!multi && advancing.size() != 1) return;
        for (size_t candidate = 0; candidate < keys.size(); ++candidate) {
            int slot;
            {
                std::lock_guard guard(lock);
                if (pool.find(keys[candidate]) >= 0) continue;
                slot = pool.vacant(multi ? PCM_SOURCE_LIMIT : 1);
                if (slot < 0) { ++pool.capacityMisses; continue; }
            }
            // Only this discovery thread installs transports. Existing capture
            // reads continue while a late slot's pages are prepared here.
            if (!prepare(static_cast<uint32_t>(slot))) continue;
            std::lock_guard guard(lock);
            if (pool.bind(slot, keys[candidate], now)) {
                queues[slot] = advancing[candidate];
                transports[slot].lastProgress.store(now);
                transports[slot].activeLease.store(pool.slots[slot].lease, std::memory_order_release);
            }
        }
    }
 void find(){
  HRESULT co=CoInitializeEx(nullptr,COINIT_MULTITHREADED);IMMDeviceEnumerator*enumerator=nullptr;
  CoCreateInstance(__uuidof(MMDeviceEnumerator),nullptr,CLSCTX_ALL,__uuidof(IMMDeviceEnumerator),(void**)&enumerator);
  std::vector<BYTE> bytes(1024*1024);
  while(WaitForSingleObject(stop,0)!=WAIT_OBJECT_0){
   auto allowed=enumerator?eligible(enumerator):std::set<DWORD>{};eligibleCount=allowed.size();std::vector<QueueInfo> found;uint64_t scanned=0;
   if(!allowed.empty())for(uintptr_t at=0x10000;at<0x00007fffffff0000ULL&&scanned<256*1024*1024ULL;){
    MEMORY_BASIC_INFORMATION region{};if(!VirtualQueryEx(process,(void*)at,&region,sizeof(region)))break;
    uintptr_t next=(uintptr_t)region.BaseAddress+region.RegionSize;if(next<=at)break;at=next;
    if(region.State!=MEM_COMMIT||region.Type!=MEM_PRIVATE||(region.Protect&(PAGE_GUARD|PAGE_NOACCESS))||!(region.Protect&(PAGE_READWRITE|PAGE_WRITECOPY))||region.RegionSize>32*1024*1024)continue;
    for(size_t offset=0;offset<region.RegionSize;offset+=bytes.size()){
     if(WaitForSingleObject(stop,0)==WAIT_OBJECT_0)break;
     size_t n=(std::min)(bytes.size(),region.RegionSize-offset);uintptr_t address=(uintptr_t)region.BaseAddress+offset;
     if(!read(address,bytes.data(),n))continue;scanned+=n;scanBytes+=n;
     for(size_t p=0;p+8<=n;p+=8){uintptr_t v;memcpy(&v,bytes.data()+p,8);if(v==image+PCM_INPUT_VTABLE_RVA&&address+p>=0x1e0){vtableHits++;QueueInfo q;if(inspect(address+p-0x1e0,q,allowed)&&std::none_of(found.begin(),found.end(),[&](auto& old){return old.base==q.base;}))found.push_back(q);}}
    }
   }
   // Queue objects can outlive playback. Distinguish live producers by cursor
   // progression, never by audio level: an advancing silent stream is still live.
   std::vector<uint64_t> positions(found.size());std::vector<bool> valid(found.size());
   for(size_t i=0;i<found.size();i++)valid[i]=value(found[i].control+0x10,positions[i]);
   if(!found.empty()&&WaitForSingleObject(stop,30)==WAIT_OBJECT_0)break;
   std::vector<QueueInfo> advancing;
   for(size_t i=0;i<found.size();i++){uint64_t now=0;QueueInfo verified;
    if(valid[i]&&value(found[i].control+0x10,now)&&now>positions[i]&&inspect(found[i].base,verified,allowed)&&verified.mapping==found[i].mapping&&verified.control==found[i].control){auto tag=browserIdentity.lookup(host,verified.pid,verified.base,verified.mapping,verified.control);if(!sourceTabId||tag.tabId==sourceTabId)advancing.push_back(verified);}
   }
   queuesFound=static_cast<uint32_t>(found.size());
   candidates=static_cast<uint32_t>(advancing.size());
   select_sources(advancing);
   discoveries++;WaitForSingleObject(stop,allowed.empty()?50:500);
  }if(enumerator)enumerator->Release();if(SUCCEEDED(co))CoUninitialize();
 }

    void retire(uint32_t slot, uint64_t lease) {
        std::lock_guard guard(lock);
        pool.retire(slot, lease);
        if (!pool.slots[slot].active)
            transports[slot].activeLease.store(0, std::memory_order_release);
    }

    struct ReaderState {
        uint64_t lease = 0, local = 0;
        uint32_t generation = 0, index = 0;
        bool ready = false;
        BrowserIdentityTag tag{};
    };

    bool next_generation(uint32_t slot, ReaderState& active, uint64_t lease) {
        if (active.generation == UINT32_MAX) {
            ++errors;
            active.ready = false;
            retire(slot, lease);
            return false;
        }
        ++active.generation;
        transports[slot].generation.store(active.generation);
        ++resets;
        return true;
    }

    void capture_one(uint32_t lane, const PcmSourceBinding& binding, const QueueInfo& q,
                     ReaderState& active, float* data) {
        auto& transport = transports[lane];
        if (!binding.active || !(activatedMask.load(std::memory_order_acquire) & (1u << lane))) {
            active.ready = false;
            return;
        }
        const auto tag = browserIdentity.lookup(host, q.pid, q.base, q.mapping, q.control);
        if (sourceTabId && tag.tabId != sourceTabId) { active.ready = false; return; }
        uintptr_t vtable = 0;
        if (!value(q.base + 0x1e0, vtable) || vtable != image + PCM_INPUT_VTABLE_RVA) {
            ++errors; active.ready = false; retire(lane, binding.lease); return;
        }
        uint64_t cursors[2]{};
        ++polls;
        if (!read(q.control + 0x10, cursors, sizeof(cursors))) {
            ++errors; active.ready = false; retire(lane, binding.lease); return;
        }
        const uint64_t write = cursors[0];
        const bool changedTag = active.tag.sourceKeyHi != tag.sourceKeyHi || active.tag.sourceKeyLo != tag.sourceKeyLo ||
            active.tag.browserPid != tag.browserPid || active.tag.tabId != tag.tabId || active.tag.generation != tag.generation;
        if (!active.ready || active.lease != binding.lease || write < active.local || changedTag) {
            if (!next_generation(lane, active, binding.lease)) return;
            active.lease = binding.lease;
            active.tag = tag;
            active.local = write;
            active.ready = true;
        }
        if (write == active.local) return;
        const uint64_t count = write - active.local;
        const uint64_t observed = pcm_qpc_ns();
        transport.lastProgress.store(observed);
        if (count > q.capacity || count % q.align || count / q.align > PCM_MAX_FRAMES) {
            ++dropped; active.local = write; next_generation(lane, active, binding.lease); return;
        }
        const uint32_t offset = static_cast<uint32_t>(active.local % q.capacity);
        const uint32_t first = (std::min)(static_cast<uint32_t>(count), q.capacity - offset);
        if (!read(q.mapping + q.start + offset, data, first) ||
            (count > first && !read(q.mapping + q.start, reinterpret_cast<BYTE*>(data) + first, count - first))) {
            ++errors; active.ready = false; retire(lane, binding.lease); return;
        }
        uint64_t after[2]{};
        uintptr_t afterVtable = 0;
        if (!read(q.control + 0x10, after, sizeof(after)) || !value(q.base + 0x1e0, afterVtable) ||
            afterVtable != vtable || after[0] < write || after[0] - active.local > q.capacity) {
            ++dropped; active.ready = false; return;
        }
        if (transport.activeLease.load(std::memory_order_acquire) != binding.lease) {
            active.ready = false;
            return;
        }
        auto& slot = transport.ring->blocks[active.index % PCM_SLOTS];
        if (InterlockedCompareExchange(&slot.ready, 0, 0) != 0) {
            ++dropped; active.local = write; return;
        }
        const bool unread = after[1] <= active.local;
        const uint32_t n = static_cast<uint32_t>(count / q.align);
        slot.descriptor = {sizeof(PcmDescriptor), q.pid, host, active.generation, 48000, q.channels, q.mask, n,
            q.base, active.local / q.align, observed, pcm_qpc_ns(), after[1] / q.align, unread ? 1u : 0u, 0};
        slot.descriptor.browser = tag;
        if (tag.tabId) slot.descriptor.flags |= 2;
        memcpy(slot.pcm, data, static_cast<size_t>(count));
        InterlockedExchange(&slot.ready, 1);
        ++active.index; ++packets; frames += n;
        ++transport.packets; transport.frames += n;
        if (unread) early += n;
        SetEvent(transport.event);
        active.local = write;
    }

    void capture() {
        DWORD task = 0;
        HANDLE mmcss = AvSetMmThreadCharacteristicsW(L"Pro Audio", &task);
        HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, 2, TIMER_ALL_ACCESS);
        LARGE_INTEGER due;
        due.QuadPart = -10000;
        if (!timer || !SetWaitableTimer(timer, &due, 1, nullptr, nullptr, FALSE)) {
            ++errors;
            if (timer) CloseHandle(timer);
            if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
            return;
        }
        std::array<ReaderState, PCM_SOURCE_LIMIT> cursors{};
        std::vector<float> data(65536 / sizeof(float));
        while (WaitForSingleObject(stop, 0) != WAIT_OBJECT_0) {
            std::array<PcmSourceBinding, PCM_SOURCE_LIMIT> bindings;
            std::array<QueueInfo, PCM_SOURCE_LIMIT> snapshot;
            {
                std::lock_guard guard(lock);
                bindings = pool.slots;
                snapshot = queues;
            }
            bool active = false;
            for (uint32_t lane = 0; lane < (multi ? PCM_SOURCE_LIMIT : 1); ++lane) {
                active |= bindings[lane].active;
                capture_one(lane, bindings[lane], snapshot[lane], cursors[lane], data.data());
            }
            state = active ? 1 : (!multi && candidates > 1 ? 2 : 0);
            HANDLE waits[]{stop, timer};
            if (WaitForMultipleObjects(2, waits, FALSE, 100) == WAIT_OBJECT_0) break;
        }
        state = 3;
        CancelWaitableTimer(timer);
        CloseHandle(timer);
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
    }

    void stats(char* out, uint32_t size) {
        PcmSourcePool snapshot;
        {
            std::lock_guard guard(lock);
            snapshot = pool;
        }
        const auto& first = snapshot.slots[0];
        std::ostringstream json;
        json << "{\"state\":" << state.load() << ",\"candidates\":" << candidates.load()
             << ",\"discoveredQueues\":" << queuesFound.load()
             << ",\"selectedPid\":" << (first.active ? first.key.pid : 0)
             << ",\"selectedProcessStarted\":" << (first.active ? first.key.processStarted : 0)
             << ",\"packets\":" << packets.load() << ",\"frames\":" << frames.load()
             << ",\"copiedBeforeWindowsConsumedFrames\":" << early.load()
             << ",\"dropped\":" << dropped.load() << ",\"resets\":" << resets.load()
             << ",\"readErrors\":" << errors.load() << ",\"polls\":" << polls.load()
             << ",\"discoveries\":" << discoveries.load() << ",\"multiSource\":" << (multi ? "true" : "false")
             << ",\"slotLimit\":" << (multi ? PCM_SOURCE_LIMIT : 1)
             << ",\"sourceAdmissions\":" << snapshot.admissions << ",\"sourceRetirements\":" << snapshot.retirements
             << ",\"capacityMisses\":" << snapshot.capacityMisses << ",\"sources\":[";
        for (uint32_t lane = 0; lane < (multi ? PCM_SOURCE_LIMIT : 1); ++lane) {
            if (lane) json << ',';
            const auto& binding = snapshot.slots[lane];
            const auto& transport = transports[lane];
            json << "{\"slot\":" << lane << ",\"active\":" << (binding.active ? "true" : "false")
                 << ",\"prepared\":" << ((preparedMask.load() & (1u << lane)) ? "true" : "false")
                 << ",\"activated\":" << ((activatedMask.load() & (1u << lane)) ? "true" : "false")
                 << ",\"pid\":" << (binding.active ? binding.key.pid : 0)
                 << ",\"processStarted\":" << (binding.active ? binding.key.processStarted : 0)
                 << ",\"streamId\":\"0x" << std::hex << (binding.active ? binding.key.base : 0) << std::dec << '"'
                 << ",\"lease\":" << binding.lease << ",\"generation\":" << transport.generation.load()
                 << ",\"packets\":" << transport.packets.load() << ",\"frames\":" << transport.frames.load() << '}';
        }
        json << "],\"readOnlyTarget\":true,\"scope\":\"ordinary V1 queues, 48k Float32, no SRC\"}";
        const auto text = json.str();
        if (text.size() >= size) { if (size) out[0] = 0; return; }
        memcpy(out, text.c_str(), text.size() + 1);
    }

    ~PcmCapture() {
        if (stop) SetEvent(stop);
        if (discover.joinable()) discover.join();
        if (reader.joinable()) reader.join();
        if (stop) CloseHandle(stop);
    }
};
static void*open_capture(DWORD pid,const wchar_t*endpoint,DWORD source,const wchar_t*name,const wchar_t*event,const wchar_t*identityFile,uint32_t leaseMs,uint32_t tabId,bool multi=false){
 if(!endpoint||!name||!event||!sha_matches())return nullptr;auto*p=new PcmCapture;p->host=pid;p->source=source;p->endpoint=endpoint;
 auto open_and_find_image=[&]() -> DWORD {
  if(p->process){CloseHandle(p->process);p->process=nullptr;}
  p->process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|PROCESS_VM_READ,FALSE,pid);
  if(!p->process)return GetLastError();
  DWORD protection=0;
  if(!GetProcessInformation(p->process,(PROCESS_INFORMATION_CLASS)7,&protection,4)||protection!=0xfffffffe)
   return ERROR_NOT_SUPPORTED;
  HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
  if(snapshot==INVALID_HANDLE_VALUE)return GetLastError();
  MODULEENTRY32W mod{sizeof(mod)};
  if(Module32FirstW(snapshot,&mod)&&!_wcsicmp(mod.szModule,L"audiodg.exe"))p->image=(uintptr_t)mod.modBaseAddr;
  CloseHandle(snapshot);
  return p->image?ERROR_SUCCESS:ERROR_MOD_NOT_FOUND;
 };
 const DWORD status=open_and_find_image();
 if(status!=ERROR_SUCCESS&&(status!=ERROR_ACCESS_DENIED||!with_audio_read_privilege(open_and_find_image))){delete p;return nullptr;}
 BYTE prologue[32];if(!p->image||!p->read(p->image+PCM_INPUT_FUNCTION_RVA,prologue,32)||memcmp(prologue,PCM_INPUT_PROLOGUE,32)){delete p;return nullptr;}

 p->multi=multi;p->baseName=name;p->baseEvent=event;
 for(uint32_t lane=0;lane<(multi?PCM_SOURCE_INITIAL:1);++lane)if(!p->prepare(lane)){delete p;return nullptr;}
 if(!multi)p->activatedMask=1;
 if(identityFile){if(!p->browserIdentity.start(identityFile,leaseMs)){delete p;return nullptr;}p->sourceTabId=tabId;}
 p->discover=std::thread([p]{p->find();});p->reader=std::thread([p]{p->capture();});return p;
}
extern "C" __declspec(dllexport) void*nsp_pcm_capture_open(DWORD pid,const wchar_t*endpoint,DWORD source,const wchar_t*name,const wchar_t*event){return open_capture(pid,endpoint,source,name,event,nullptr,0,0);}
extern "C" __declspec(dllexport) void*nsp_pcm_capture_open_browser(DWORD pid,const wchar_t*endpoint,DWORD source,const wchar_t*name,const wchar_t*event,const wchar_t*identityFile,uint32_t leaseMs,uint32_t tabId){return open_capture(pid,endpoint,source,name,event,identityFile,leaseMs,tabId);}
extern "C" __declspec(dllexport) void*nsp_pcm_capture_open_multi(DWORD pid,const wchar_t*endpoint,DWORD source,const wchar_t*name,const wchar_t*event,const wchar_t*identityFile,uint32_t leaseMs,uint32_t tabId){return open_capture(pid,endpoint,source,name,event,identityFile,leaseMs,tabId,true);}
extern "C" __declspec(dllexport) uint32_t nsp_pcm_capture_source_mask(void*ctx){return ctx?static_cast<PcmCapture*>(ctx)->preparedMask.load(std::memory_order_acquire):0;}
extern "C" __declspec(dllexport) int nsp_pcm_capture_activate_source(void*ctx,uint32_t lane){
 if(!ctx||lane>=PCM_SOURCE_LIMIT)return -1;
 auto*p=static_cast<PcmCapture*>(ctx);
 if(!(p->preparedMask.load(std::memory_order_acquire)&(1u<<lane)))return -1;
 p->activatedMask.fetch_or(1u<<lane,std::memory_order_release);return 0;
}
extern "C" __declspec(dllexport) void nsp_pcm_capture_stats(void*ctx,char*out,uint32_t n){if(ctx&&out)static_cast<PcmCapture*>(ctx)->stats(out,n);}
extern "C" __declspec(dllexport) void nsp_pcm_capture_diagnostics(void*ctx,char*out,uint32_t n){if(!ctx||!out)return;auto*p=(PcmCapture*)ctx;snprintf(out,n,"{\"eligibleProcesses\":%llu,\"vtableHits\":%llu,\"ownerMatches\":%llu,\"formatRejected\":%llu,\"scanBytes\":%llu,\"expectedPid\":%lu,\"lastCandidatePid\":%u,\"lastOwnerPid\":%u,\"pidMatches\":%u}",p->eligibleCount.load(),p->vtableHits.load(),p->ownerMatches.load(),p->formatRejected.load(),p->scanBytes.load(),p->source,p->lastCandidatePid.load(),p->lastOwnerPid.load(),p->pidMatches.load());}
extern "C" __declspec(dllexport) void nsp_pcm_capture_close(void*ctx){delete (PcmCapture*)ctx;}
