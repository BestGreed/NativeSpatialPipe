#include "pcm_ingress.cpp"
#include <cassert>
#include <vector>
static std::vector<unsigned char> last;static uint64_t calls=0;
static int accept(void*,uint64_t seq,uint32_t channels,const unsigned char*data,size_t bytes){assert(seq==calls++);assert(channels==2);last.assign(data,data+bytes);return 0;}

struct Output {
 HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, nullptr);
 std::atomic<uint32_t> calls{0};
 float expected;
 explicit Output(float value) : expected(value) { assert(ready); }
 ~Output() { CloseHandle(ready); }
};

static int accept_source(void* context, uint64_t seq, uint32_t channels, const unsigned char* data, size_t bytes) {
 auto& output = *static_cast<Output*>(context);
 assert(seq == output.calls.load());
 assert(channels == 2 && bytes == 2 * (32 + 480 * sizeof(float)));
 for (uint32_t sample = 0; sample < 480; ++sample) {
  float left, right;
  memcpy(&left, data + 64 + sample * sizeof(float), sizeof(float));
  memcpy(&right, data + 64 + (480 + sample) * sizeof(float), sizeof(float));
  assert(left == output.expected && right == -output.expected);
 }
 ++output.calls;
 SetEvent(output.ready);
 return 0;
}

struct Source {
 wchar_t mappingName[80]{}, eventName[80]{};
 HANDLE mapping, event;
 PcmRing* ring;
 Output output;
 void* ingress;

 Source(uint32_t lane, float value) : output(value) {
  swprintf(mappingName, 80, L"Local\\NSP-ingress-test-%lu-%u", GetCurrentProcessId(), lane);
  swprintf(eventName, 80, L"%ls-ready", mappingName);
  mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(PcmRing), mappingName);
  event = CreateEventW(nullptr, FALSE, FALSE, eventName);
  assert(mapping && event);
  ring = static_cast<PcmRing*>(MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, sizeof(PcmRing)));
  assert(ring);
  ring->magic = PCM_MAGIC;
  ring->version = 2;
  ring->slots = PCM_SLOTS;
  ring->slotBytes = sizeof(PcmSlot);
  ingress = nsp_pcm_ingress_open(mappingName, eventName, &output, accept_source);
  assert(ingress);
 }

 void publish(uint32_t index, uint64_t firstFrame, uint32_t frames) {
  auto& slot = ring->blocks[index];
  assert(InterlockedCompareExchange(&slot.ready, 0, 0) == 0);
  slot.descriptor = {sizeof(PcmDescriptor), 123, 456, 1, 48000, 2, 3, frames, 99, firstFrame};
  for (uint32_t sample = 0; sample < frames; ++sample) {
   slot.pcm[sample * 2] = output.expected;
   slot.pcm[sample * 2 + 1] = -output.expected;
  }
  InterlockedExchange(&slot.ready, 1);
  assert(SetEvent(event));
 }

 ~Source() {
  nsp_pcm_ingress_close(ingress);
  UnmapViewOfFile(ring);
  CloseHandle(event);
  CloseHandle(mapping);
 }
};

static void test_independent_rings() {
 Source first(8, 0.125f);
 Source second(9, 0.5f);
 first.publish(0, 0, 300);
 second.publish(0, 0, 480);
 assert(WaitForSingleObject(second.output.ready, 5000) == WAIT_OBJECT_0);
 assert(first.output.calls == 0);
 first.publish(1, 300, 180);
 assert(WaitForSingleObject(first.output.ready, 5000) == WAIT_OBJECT_0);
 assert(first.output.calls == 1 && second.output.calls == 1);
}

static int accept_lease(void* context, uint64_t seq, uint32_t, const unsigned char*, size_t,
                        uint64_t captureNs, uint64_t lease) {
 auto& leases = *static_cast<std::vector<uint64_t>*>(context);
 assert(captureNs == 0 && seq == leases.size());
 leases.push_back(lease);
 return 0;
}

static void test_source_leases() {
 PcmIngress ingress;
 std::vector<uint64_t> leases;
 ingress.pipeline = &leases;
 ingress.submitLeased = accept_lease;
 std::array<float, 480 * 2> samples{};
 PcmDescriptor descriptor{sizeof(PcmDescriptor), 1, 2, 1, 48000, 2, 3, 480, 3, 0};
 assert(ingress.consume(descriptor, samples.data()));
 descriptor.firstSourceFrame = 960;
 assert(ingress.consume(descriptor, samples.data()));
 descriptor.generation++;
 descriptor.firstSourceFrame = 0;
 assert(ingress.consume(descriptor, samples.data()));
 assert((leases == std::vector<uint64_t>{1, 1, 2}));
}

int main(){
 test_source_leases();
 PcmIngress adapter;adapter.submit=accept;adapter.pipeline=(void*)1;
 PcmDescriptor d{sizeof(PcmDescriptor),123,456,1,48000,2,3,300,99,0,1,2,0,1,0};
 float first[600],second[360];for(int i=0;i<300;i++){first[i*2]=i*.001f;first[i*2+1]=-i*.001f;}for(int i=0;i<180;i++){second[i*2]=(i+300)*.001f;second[i*2+1]=-(i+300)*.001f;}
 assert(adapter.consume(d,first)&&calls==0);d.frames=180;d.firstSourceFrame=300;assert(adapter.consume(d,second)&&calls==1);
 uint32_t type=0;memcpy(&type,last.data()+8,4);assert(type==2);memcpy(&type,last.data()+40,4);assert(type==4);
 for(int i=0;i<480;i++){float l,r;memcpy(&l,last.data()+64+i*4,4);memcpy(&r,last.data()+64+480*4+i*4,4);assert(l==i*.001f&&r==-i*.001f);}
 d.frames=300;d.firstSourceFrame=480;assert(adapter.consume(d,first));d.generation++;d.frames=180;d.firstSourceFrame=0;assert(adapter.consume(d,second)&&calls==1&&adapter.fill==180);
 d.sampleRate=44100;assert(adapter.consume(d,second)&&adapter.unsupported==1);
 test_independent_rings();
 puts("PASS split-block reassembly, stereo bed labels, bit-exact planar PCM, generation reset and unsupported rate rejection");
 puts("PASS independent named-ring workers assemble and emit separate PCM blocks");
}
