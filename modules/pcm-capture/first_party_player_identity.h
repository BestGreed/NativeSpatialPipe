#pragma once
#include "queue_probe.h"
#include <appmodel.h>
#include <algorithm>
#include <vector>

class FirstPartyPlayerIdentity {
    static constexpr const wchar_t* apps[] = {
        L"Microsoft.ZuneVideo_8wekyb3d8bbwe!Microsoft.ZuneVideo",
        L"Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
    };
    static constexpr size_t appCount = std::size(apps);
    static constexpr char graphKey[] = "882DC2C4101A0C7A6008528B0BB73AED1";
    static constexpr char serviceKey[] = "4C7AF70B068E25F44D0BEBEFE5D1F8631";
    static constexpr uintptr_t streamVtable = 0x1807f8;
    static constexpr uintptr_t streamInfoVtable = 0x180720;
    static constexpr size_t serviceScanBudget = 64 * 1024 * 1024;

    HANDLE process = nullptr;
    uintptr_t image = 0;

    bool read(uintptr_t at, void* out, size_t size) const {
        SIZE_T got = 0;
        return ReadProcessMemory(process, reinterpret_cast<void*>(at), out, size, &got) && got == size;
    }

    template<class T> T value(uintptr_t at) const {
        T result{};
        read(at, &result, sizeof(result));
        return result;
    }

    bool connect() {
        DWORD exitCode = 0;
        if (process && GetExitCodeProcess(process, &exitCode) && exitCode == STILL_ACTIVE) return image != 0;
        if (process) CloseHandle(process);
        process = nullptr;
        image = 0;

        SC_HANDLE manager = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
        if (!manager) return false;
        SC_HANDLE service = OpenServiceW(manager, L"Audiosrv", SERVICE_QUERY_STATUS);
        SERVICE_STATUS_PROCESS status{};
        DWORD needed = 0;
        const bool running = service && QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO,
            reinterpret_cast<BYTE*>(&status), sizeof(status), &needed) && status.dwCurrentState == SERVICE_RUNNING;
        if (service) CloseServiceHandle(service);
        CloseServiceHandle(manager);
        if (!running || !status.dwProcessId) return false;

        auto open = [&]() -> DWORD {
            process = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, status.dwProcessId);
            if (!process) return GetLastError();
            HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, status.dwProcessId);
            if (snapshot == INVALID_HANDLE_VALUE) {
                const auto error = GetLastError();
                CloseHandle(process);
                process = nullptr;
                return error;
            }
            MODULEENTRY32W module{sizeof(module)};
            for (BOOL more = Module32FirstW(snapshot, &module); more; more = Module32NextW(snapshot, &module)) {
                if (_wcsicmp(module.szModule, L"Audiosrv.dll") != 0) continue;
                const auto base = reinterpret_cast<uintptr_t>(module.modBaseAddr);
                PcmSymbolKey key{};
                auto readImage = [&](uint64_t offset, void* out, size_t size) { return read(base + offset, out, size); };
                if (pcm_symbol_key(readImage, key) && strcmp(key, serviceKey) == 0) image = base;
                break;
            }
            CloseHandle(snapshot);
            return ERROR_SUCCESS;
        };
        const DWORD result = open();
        if (result == ERROR_ACCESS_DENIED && !process) with_audio_read_privilege(open);
        return image != 0;
    }

    static size_t app_index(const wchar_t* id) {
        for (size_t i = 0; i < appCount; ++i)
            if (wcscmp(id, apps[i]) == 0) return i;
        return appCount;
    }

    size_t stream_app(uintptr_t stream) const {
        const auto owner = value<uintptr_t>(stream + 0x48);
        const auto ownerVtable = value<uintptr_t>(owner);
        const auto getter = value<uintptr_t>(ownerVtable + 0x48);
        // The observed IAudioProcess getter returns the AUMID pointer at +0xb0.
        if (value<uint64_t>(getter) != 0xc3000000b0818b48ULL) return appCount;
        const auto text = value<uintptr_t>(owner + 0xb0);
        for (size_t i = 0; i < appCount; ++i) {
            wchar_t id[64]{};
            const auto length = wcslen(apps[i]) + 1;
            if (read(text, id, length * sizeof(wchar_t)) && wmemcmp(id, apps[i], length) == 0) return i;
        }
        return appCount;
    }

public:
    using ActivePlayers = std::array<DWORD, appCount>;

    ~FirstPartyPlayerIdentity() { if (process) CloseHandle(process); }

    static ActivePlayers active_players(const QueueProbe& probe, const std::set<DWORD>& eligible) {
        ActivePlayers players{};
        if (strcmp(probe.pcmSymbolKey, graphKey) != 0) return players;
        std::array<bool, appCount> ambiguous{};
        for (DWORD pid : eligible) {
            HANDLE app = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
            if (!app) continue;
            wchar_t id[64]{};
            UINT32 length = std::size(id);
            const auto status = GetApplicationUserModelId(app, &length, id);
            CloseHandle(app);
            if (status != ERROR_SUCCESS) continue;
            const auto index = app_index(id);
            if (index == appCount || ambiguous[index]) continue;
            ambiguous[index] = players[index] != 0;
            players[index] = ambiguous[index] ? 0 : pid;
        }
        return players;
    }

    void match(std::vector<QueueInfo>& queues, const ActivePlayers& players,
               std::vector<BYTE>& bytes, HANDLE stop) {
        if (queues.empty() || !connect()) return;
        std::vector<size_t> matches(queues.size(), appCount);
        std::vector<bool> seen(queues.size());
        size_t scanned = 0;
        for (uintptr_t at = 0x10000; scanned < serviceScanBudget;) {
            MEMORY_BASIC_INFORMATION region{};
            if (!VirtualQueryEx(process, reinterpret_cast<void*>(at), &region, sizeof(region))) break;
            const auto base = reinterpret_cast<uintptr_t>(region.BaseAddress);
            const auto next = base + region.RegionSize;
            if (next <= at) break;
            at = next;
            if (region.State != MEM_COMMIT || region.Type != MEM_PRIVATE ||
                (region.Protect & (PAGE_GUARD | PAGE_NOACCESS)) ||
                !(region.Protect & (PAGE_READWRITE | PAGE_WRITECOPY))) continue;
            for (size_t offset = 0; offset < region.RegionSize && scanned < serviceScanBudget; offset += bytes.size()) {
                if (WaitForSingleObject(stop, 0) == WAIT_OBJECT_0) return;
                const auto size = (std::min)(bytes.size(), region.RegionSize - offset);
                const auto address = base + offset;
                scanned += size;
                if (!read(address, bytes.data(), size)) continue;
                for (size_t i = 0; i + sizeof(uintptr_t) <= size; i += sizeof(uintptr_t)) {
                    uintptr_t vtable = 0;
                    memcpy(&vtable, bytes.data() + i, sizeof(vtable));
                    if (vtable != image + streamVtable) continue;
                    const auto stream = address + i;
                    const auto id = value<PcmStreamId>(stream + 0x1c0);
                    if (id == PcmStreamId{} || value<uintptr_t>(stream + 8) != image + streamInfoVtable ||
                        !value<uint64_t>(stream + 0x30) || !value<uintptr_t>(stream + 0x248)) continue;
                    for (size_t j = 0; j < queues.size(); ++j) {
                        if (queues[j].streamId != id) continue;
                        matches[j] = seen[j] ? appCount : stream_app(stream);
                        seen[j] = true;
                    }
                }
            }
        }
        std::array<unsigned, appCount> counts{};
        for (auto app : matches) if (app < appCount) ++counts[app];
        for (size_t i = 0; i < queues.size(); ++i) {
            const auto app = matches[i];
            if (app < appCount && counts[app] == 1) queues[i].pid = players[app];
        }
    }
};
