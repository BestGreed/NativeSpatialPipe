// Fixed control-plane roster. Audio buffers and read cursors belong to capture.
#pragma once
#include <array>
#include <cstdint>
#include <limits>

constexpr uint32_t PCM_SOURCE_LIMIT = 8;
constexpr uint32_t PCM_SOURCE_INITIAL = 4;
constexpr uint64_t PCM_SOURCE_IDLE_NS = 500000000;

struct PcmSourceKey {
    uint64_t base = 0, mapping = 0, control = 0, processStarted = 0;
    uint32_t pid = 0, start = 0, capacity = 0, align = 0, channels = 0, mask = 0;
    bool operator==(const PcmSourceKey&) const = default;
};

struct PcmSourceBinding {
    PcmSourceKey key;
    uint64_t lease = 0, lastProgress = 0;
    bool active = false;
};

struct PcmSourcePool {
    std::array<PcmSourceBinding, PCM_SOURCE_LIMIT> slots{};
    uint64_t admissions = 0, retirements = 0, capacityMisses = 0;

    void expire(uint64_t now) {
        for (auto& slot : slots) {
            if (slot.active && now >= slot.lastProgress &&
                now - slot.lastProgress >= PCM_SOURCE_IDLE_NS) {
                slot.active = false;
                ++retirements;
            }
        }
    }

    int find(const PcmSourceKey& key) const {
        for (uint32_t i = 0; i < slots.size(); ++i) {
            if (slots[i].active && slots[i].key == key) return static_cast<int>(i);
        }
        return -1;
    }

    int vacant(uint32_t limit = PCM_SOURCE_LIMIT) const {
        for (uint32_t i = 0; i < limit; ++i) {
            if (!slots[i].active && slots[i].lease != std::numeric_limits<uint64_t>::max())
                return static_cast<int>(i);
        }
        return -1;
    }

    bool bind(uint32_t index, const PcmSourceKey& key, uint64_t now) {
        if (index >= slots.size() || slots[index].active ||
            slots[index].lease == std::numeric_limits<uint64_t>::max()) return false;
        auto& slot = slots[index];
        slot.key = key;
        ++slot.lease;
        slot.lastProgress = now;
        slot.active = true;
        ++admissions;
        return true;
    }

    void retire(uint32_t index, uint64_t lease) {
        auto& slot = slots[index];
        if (slot.active && slot.lease == lease) {
            slot.active = false;
            ++retirements;
        }
    }
};
