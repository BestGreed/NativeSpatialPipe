// SPDX-License-Identifier: Apache-2.0
#include "spatial_submit.h"
#include <cassert>
#include <cstdio>
struct FakeFrame {
  UINT64 sequence;
};
struct FakeDecoder {
  UINT64 next = 0;
};
static std::atomic<UINT64> freed{0};
static std::atomic<DWORD> consumerThread{0};
static std::atomic<bool> freedOnConsumer{false};
static NspOwnedFrame *takeFake(NspDecoder *decoder) {
  return reinterpret_cast<NspOwnedFrame *>(
      new FakeFrame{reinterpret_cast<FakeDecoder *>(decoder)->next++});
}
static UINT64 startFake(const NspOwnedFrame *frame) {
  return reinterpret_cast<const FakeFrame *>(frame)->sequence;
}
static void releaseFake(NspOwnedFrame *frame) {
  if (GetCurrentThreadId() == consumerThread)
    freedOnConsumer = true;
  delete reinterpret_cast<FakeFrame *>(frame);
  freed.fetch_add(1);
}
static void destroyFake(NspDecoder *) {}
int main() {
  Decoder decoder;
  FakeDecoder context;
  decoder.context = reinterpret_cast<NspDecoder *>(&context);
  decoder.take = takeFake;
  decoder.frameStart = startFake;
  decoder.releaseFrame = releaseFake;
  decoder.destroy = destroyFake;
  auto signal = std::make_shared<ProgressSignal>();
  auto submit = std::make_unique<SpatialSubmitter>(decoder, signal);
  submit->recycleEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  assert(submit->recycleEvent);
  NspFrame view{};
  view.samples = 1;
  view.channels = 1;
  view.rate = 48000;
  consumerThread = GetCurrentThreadId();
  // Fill all credits. Recycling has not started: retirement alone cannot make
  // the producer overwrite memory whose deallocation is still pending.
  for (size_t i = 0; i < SpatialSubmitter::maxFrames; i++)
    assert(submit->enqueue(view) == S_OK);
  assert(!submit->capacity(1));
  for (size_t i = 0; i < SpatialSubmitter::maxFrames; i++) {
    assert(submit->queue.pop(submit->active));
    submit->haveActive = true;
    assert(submit->active.start == i);
    assert(submit->retireActive());
  }
  assert(freed == 0 && submit->queuedSamples == 0 && !submit->capacity(1));
  signal->armed = true;
  submit->reclaimer = std::thread([&] { submit->reclaim(); });
  SetEvent(submit->recycleEvent);
  assert(WaitForSingleObject(signal->event, 5000) == WAIT_OBJECT_0);
  assert(freed == SpatialSubmitter::maxFrames &&
         submit->credits == SpatialSubmitter::maxFrames);
  assert(!freedOnConsumer && submit->capacity(1));
  constexpr UINT64 count = 200000;
  UINT64 first = context.next;
  std::thread consumer([&] {
    consumerThread = GetCurrentThreadId();
    bool pending = false;
    for (UINT64 i = 0; i < count;) {
      if (submit->queue.pop(submit->active)) {
        submit->haveActive = true;
        assert(submit->active.start == first + i);
        assert(submit->retireActive());
        i++;
        pending = true;
        if (i % 64 == 0) {
          SetEvent(submit->recycleEvent);
          submit->notifyCapacity();
          pending = false;
        }
      } else {
        if (pending) {
          SetEvent(submit->recycleEvent);
          submit->notifyCapacity();
          pending = false;
        }
        std::this_thread::yield();
      }
    }
    if (pending)
      SetEvent(submit->recycleEvent);
  });
  for (UINT64 i = 0; i < count; i++) {
    while (!submit->capacity(1)) {
      signal->armed = true;
      if (submit->capacity(1)) {
        signal->armed = false;
        break;
      }
      assert(WaitForSingleObject(signal->event, 5000) == WAIT_OBJECT_0);
    }
    assert(submit->enqueue(view) == S_OK);
  }
  consumer.join();
  submit->stop();
  assert(freed == first + count && !freedOnConsumer);
  assert(submit->queue.producerSize() == 0 &&
         submit->retired.producerSize() == 0);
  puts("{\"ownershipCreditsBounded\":true,\"capacityWakePassed\":true,"
       "\"consumerFrees\":0,\"framesReclaimed\":208192,"
       "\"concurrentSequencePassed\":true}");
}
