// Exercise the real producer with queues owned by this test process.
#include "pcm_capture.cpp"
#include <cassert>

struct OwnedQueue {
    std::array<unsigned char, 0x200> object{};
    std::array<uint64_t, 4> control{};
    std::array<float, 8192> samples{};
    QueueInfo info;

    OwnedQueue(uintptr_t image) {
        const uintptr_t vtable = image + PCM_INPUT_VTABLE_RVA;
        memcpy(object.data() + 0x1e0, &vtable, sizeof(vtable));
        info.base = reinterpret_cast<uintptr_t>(object.data());
        info.mapping = reinterpret_cast<uintptr_t>(samples.data());
        info.control = reinterpret_cast<uintptr_t>(control.data());
        info.pid = GetCurrentProcessId();
        info.capacity = sizeof(samples);
        info.channels = 2;
        info.mask = 3;
        info.align = 8;
    }

    void append(float value, uint32_t frames = 480) {
        for (uint32_t sample = 0; sample < frames * 2; ++sample)
            samples[(control[2] / 4 + sample) % samples.size()] = value;
        control[2] += frames * info.align;
    }
};

static void test_roster() {
    PcmSourcePool pool;
    for (uint32_t lane = 0; lane < PCM_SOURCE_LIMIT; ++lane) {
        PcmSourceKey key;
        key.base = lane + 1;
        assert(pool.vacant() == static_cast<int>(lane));
        assert(pool.bind(lane, key, 1));
    }
    assert(pool.vacant() == -1);
    pool.slots[1].lastProgress = PCM_SOURCE_IDLE_NS;
    pool.expire(PCM_SOURCE_IDLE_NS + 1);
    assert(pool.retirements == 7);
    assert(pool.slots[1].active);
    const uint64_t oldLease = pool.slots[0].lease;
    PcmSourceKey replacement;
    replacement.base = 100;
    assert(pool.bind(0, replacement, PCM_SOURCE_IDLE_NS + 1));
    assert(pool.slots[0].lease == oldLease + 1);
    pool.retire(0, oldLease);
    assert(pool.slots[0].active);
}

int main() {
    test_roster();
    PcmCapture capture;
    capture.multi = true;
    capture.process = GetCurrentProcess();
    capture.host = GetCurrentProcessId();
    capture.image = 0x10000000;
    capture.baseName = L"Local\\NSP-Multi-Test-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(pcm_qpc_ns());
    capture.baseEvent = capture.baseName + L"-event";
    for (uint32_t lane = 0; lane < PCM_SOURCE_INITIAL; ++lane) assert(capture.prepare(lane));
    assert(nsp_pcm_capture_source_mask(&capture) == 15);
    assert(nsp_pcm_capture_activate_source(&capture, 4) == -1);

    std::array<OwnedQueue, 9> sources{
        OwnedQueue(capture.image), OwnedQueue(capture.image), OwnedQueue(capture.image),
        OwnedQueue(capture.image), OwnedQueue(capture.image), OwnedQueue(capture.image),
        OwnedQueue(capture.image), OwnedQueue(capture.image), OwnedQueue(capture.image)};
    std::vector<QueueInfo> candidates;
    for (uint32_t lane = 0; lane < PCM_SOURCE_INITIAL; ++lane) candidates.push_back(sources[lane].info);
    capture.select_sources(candidates);
    std::array<PcmCapture::ReaderState, PCM_SOURCE_LIMIT> cursors{};
    std::array<float, 16384> scratch{};
    auto poll = [&](uint32_t lane) {
        capture.capture_one(lane, capture.pool.slots[lane], capture.queues[lane], cursors[lane], scratch.data());
    };
    for (uint32_t lane = 0; lane < PCM_SOURCE_INITIAL; ++lane) {
        assert(nsp_pcm_capture_activate_source(&capture, lane) == 0);
        poll(lane);
        sources[lane].append(static_cast<float>(lane + 1));
        poll(lane);
        auto& block = capture.transports[lane].ring->blocks[0];
        assert(block.ready == 1 && block.descriptor.frames == 480);
        assert(block.descriptor.generation == 1);
        for (uint32_t i = 0; i < 960; ++i) assert(block.pcm[i] == static_cast<float>(lane + 1));
        InterlockedExchange(&block.ready, 0);
    }
    for (uint32_t lane = PCM_SOURCE_INITIAL; lane < PCM_SOURCE_LIMIT; ++lane)
        candidates.push_back(sources[lane].info);
    capture.select_sources(candidates);
    assert(nsp_pcm_capture_source_mask(&capture) == 255);
    // A delayed consumer starts at the latest cursor, without replaying audio
    // produced while its slot was waiting for the control-plane attachment.
    sources[4].append(5);
    poll(4);
    assert(capture.transports[4].packets == 0);
    assert(nsp_pcm_capture_activate_source(&capture, 4) == 0);
    poll(4);
    sources[4].append(6);
    poll(4);
    auto& fifth = capture.transports[4].ring->blocks[0];
    assert(fifth.ready == 1 && fifth.descriptor.firstSourceFrame == 480);
    assert(fifth.pcm[0] == 6);

    candidates.push_back(sources[8].info);
    capture.select_sources(candidates);
    assert(capture.pool.capacityMisses == 1);
    assert(capture.pool.slots[0].key.base == sources[0].info.base);
    const uint64_t oldLease = capture.pool.slots[0].lease;
    capture.retire(0, oldLease);
    candidates.erase(candidates.begin());
    capture.select_sources(candidates);
    assert(capture.pool.slots[0].key.base == sources[8].info.base);
    assert(capture.pool.slots[0].lease == oldLease + 1);
    poll(0);
    sources[8].append(9);
    poll(0);
    auto& replacement = capture.transports[0].ring->blocks[1];
    assert(replacement.ready == 1 && replacement.descriptor.generation == 2);
    assert(replacement.pcm[0] == 9);
    // An advancing silent stream remains registered.
    sources[1].append(0);
    poll(1);
    assert(capture.transports[1].ring->blocks[1].pcm[0] == 0);
    assert(capture.pool.slots[1].active);

    // A stalled consumer only loses its own new data. The other source still
    // publishes in its own ring, with no shared cursor or ring index.
    for (uint32_t i = 0; i < PCM_SLOTS; ++i) {
        sources[8].append(9);
        poll(0);
    }
    const auto dropsBefore = capture.dropped.load();
    sources[8].append(9);
    poll(0);
    assert(capture.dropped == dropsBefore + 1);
    sources[1].append(2);
    poll(1);
    assert(capture.transports[1].ring->blocks[2].ready == 1);
    assert(capture.transports[1].ring->blocks[2].pcm[0] == 2);

    // A verified queue format replacement retires the old binding immediately.
    const auto previousLease = capture.pool.slots[1].lease;
    auto changed = sources[1].info;
    changed.mask = 12;
    auto found = std::find_if(candidates.begin(), candidates.end(), [&](const auto& q) { return q.base == changed.base; });
    assert(found != candidates.end());
    *found = changed;
    capture.select_sources(candidates);
    assert(capture.pool.slots[1].lease == previousLease + 1);
    assert(capture.pool.slots[1].key.mask == 12);

    char stats[8192]{};
    capture.stats(stats, sizeof(stats));
    assert(strstr(stats, "\"capacityMisses\":1"));
    assert(strstr(stats, "\"slot\":7"));
    puts("PCM multi capture: four prepared, eight candidates, ninth rejected, reuse and independent samples passed");
}
