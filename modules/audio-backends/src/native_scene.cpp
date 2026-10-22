// SPDX-License-Identifier: Apache-2.0
#include "native_scene.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <numbers>
#include <stdexcept>

namespace spatial {
void ValidateNativeHeader(const NativePacketHeader& h) {
    if (h.magic != 0x3150534e || (h.flags & ~NativeStateCheckpoint) || h.sampleRate != 48000 ||
        h.objects < 1 || h.objects > 160 || h.frames < 1 || h.frames > 4096 ||
        h.bytes != h.objects * (32 + h.frames * 4 + ((h.flags&NativeStateCheckpoint)?32:0)) || !h.captureNs)
        throw std::runtime_error("Invalid NSP1 header; only 48 kHz float32 basic objects are supported");
}
NativePacket DecodeNativePacket(const NativePacketHeader& h, std::span<const std::uint8_t> data) {
    ValidateNativeHeader(h);
    if (data.size() != h.bytes) throw std::runtime_error("Truncated NSP1 payload");
    NativePacket p{h};
    p.properties.resize(h.objects);
    p.planar.resize(static_cast<std::size_t>(h.objects) * h.frames);
    std::memcpy(p.properties.data(), data.data(), h.objects * 32);
    std::memcpy(p.planar.data(), data.data() + h.objects * 32, p.planar.size() * 4);
    if(h.flags&NativeStateCheckpoint){
        p.checkpoint.resize(h.objects);
        std::memcpy(p.checkpoint.data(),data.data()+h.objects*(32+h.frames*4),h.objects*32);
        for(std::size_t i=0;i<p.checkpoint.size();++i){
            const auto& s=p.checkpoint[i];const auto& v=p.properties[i];
            if(s.candidateId!=v.candidateId||s.type!=v.type||s.frames!=h.frames||(s.flags&~6U)||
                !std::isfinite(s.x)||!std::isfinite(s.y)||!std::isfinite(s.z)||!std::isfinite(s.gain)||s.gain<0)
                throw std::runtime_error("Invalid native metadata checkpoint");
        }
    }
    for (const auto& v : p.properties) {
        if (v.frames != h.frames || !std::isfinite(v.x) || !std::isfinite(v.y) ||
            !std::isfinite(v.z) || !std::isfinite(v.gain) || v.gain < 0)
            throw std::runtime_error("Invalid native object property");
    }
    if (std::any_of(p.planar.begin(), p.planar.end(), [](float v) { return !std::isfinite(v); }))
        throw std::runtime_error("Nonfinite native object PCM");
    return p;
}
NativeSpatialScene::NativeSpatialScene(const SpeakerLayout& layout) : layout_(layout) {

}
void NativeSpatialScene::RetireStream(std::uint64_t stream) {
    if (auto it = streams_.find(stream); it != streams_.end()) {
        stats_.retires += it->second.slots.size(); streams_.erase(it);
    }
}
void NativeSpatialScene::Reset() {
    for (const auto& [id,s] : streams_) stats_.retires += s.slots.size();
    streams_.clear();
}
std::vector<float> NativeSpatialScene::Render(const NativePacket& p) {
    ValidateNativeHeader(p.header);
    if (p.properties.size() != p.header.objects || p.planar.size() != p.header.objects * p.header.frames ||
        p.checkpoint.size()!=((p.header.flags&NativeStateCheckpoint)?p.header.objects:0))
        throw std::runtime_error("Native packet dimensions do not match its header");
    if (!streams_.contains(p.header.stream) && streams_.size() >= 16)
        throw std::runtime_error("Native spatial stream limit (16) exceeded");
    auto& stream = streams_[p.header.stream];
    if (stream.seen && p.header.sequence != stream.sequence + 1) {
        ++stats_.gaps; stats_.retires += stream.slots.size(); stream.slots.clear();
        stream.renderer.reset();
    }
    if (!stream.renderer) stream.renderer=std::make_unique<MetadataRenderer>(layout_,48000,160);
    std::vector<MetadataEvent> events;
    std::vector<MetadataEvent> ends;
    std::vector<float> interleaved(static_cast<std::size_t>(p.header.frames)*160);
    stream.seen = true; stream.sequence = p.header.sequence;
    std::vector<float> mix(static_cast<std::size_t>(p.header.frames) * layout_.speakers.size());
    constexpr std::uint32_t knownTypes[]{1,2,4,8,16,32,64,128,256,512,1024,2048,4096,8192,16384,32768,65536,131072,262144,524288};
    const auto supported=[&](std::uint32_t type){return std::find(std::begin(knownTypes),std::end(knownTypes),type)!=std::end(knownTypes);};
    constexpr std::uint64_t transient=1ULL<<63;
    const auto identity=[](const NativeProperty& v){return (static_cast<std::uint64_t>(v.type)<<32)|v.candidateId;};
    std::map<std::uint64_t,unsigned> counts;
    std::map<std::uint32_t,unsigned> rawCounts;
    bool collision=false;
    for(const auto& v:p.properties)if(supported(v.type)){
        ++counts[identity(v)];collision|=++rawCounts[v.candidateId]>1;
    }
    stats_.identityCollisionPackets+=collision;
    std::map<std::uint64_t,const NativeProperty*> present;
    std::vector<std::uint64_t> keys(p.properties.size());
    std::vector<bool> accepted(p.properties.size());
    for(std::size_t i=0;i<p.properties.size();++i){
        const auto& v=p.properties[i];if(!supported(v.type))continue;
        auto key=identity(v);
        if(counts[key]>1){
            ++stats_.ambiguousObjects;
            // No source lifetime can be inferred from two equal candidate IDs.
            // Exact zero PCM needs no state and contributes exactly zero. Do
            // not use a threshold, a stale gain, or a guessed slot association.
            const auto first=p.planar.begin()+i*p.header.frames;
            if(std::all_of(first,first+p.header.frames,[](float x){return x==0.f;})){
                ++stats_.silentCollisionBlocks;continue;
            }
            // Preserve independently specified audible objects as isolated
            // one-packet lifetimes. Never merge PCM or inherit a sibling's
            // deltas. This cannot reconstruct cross-block position smoothing.
            const bool complete=(v.flags&1U)||((v.flags&2U)&&(v.type!=1||(v.flags&4U)));
            if(!complete){++stats_.ambiguousStateBlocks;++stats_.unknownStateBlocks;continue;}
            key=transient|i;++stats_.collisionSnapshotBlocks;
        }
        keys[i]=key;accepted[i]=true;present.emplace(key,&v);
    }
    std::array<bool,160> used{};
    for(auto it=stream.slots.begin();it!=stream.slots.end();) {
        const auto current=present.find(it->first);
        if((it->first&transient) || current==present.end() || current->second->type!=it->second.type || (current->second->flags&1U)) {
            events.push_back({0,it->second.lane,MetadataEnd});
            ++stats_.retires;it=stream.slots.erase(it);
        } else {used[it->second.lane]=true;++it;}
    }
    std::vector<std::uint64_t> ended;
    for (std::size_t i = 0; i < p.properties.size(); ++i) {
        const auto& v = p.properties[i];
        if(!supported(v.type)){++stats_.unknownTypes;continue;}
        if(!accepted[i])continue;
        const auto key=keys[i];
        auto [pos, created] = stream.slots.try_emplace(key);
        auto& s = pos->second;
        if (created) {
            s.candidateId=v.candidateId;s.type=v.type;++stats_.creates;
            const auto free=std::find(used.begin(),used.end(),false);
            if(free==used.end())throw std::runtime_error("Native object lane limit exceeded");
            s.lane=static_cast<unsigned>(free-used.begin());used[s.lane]=true;
            // Only a witnessed creation permits public API defaults. Joining
            // an existing life or recovering a packet gap remains unknown.
            if(v.flags&1U){s.gain=1;s.gainKnown=true;s.positionKnown=true;}
            // A dropped packet may have held the only gain/position update or
            // a complete new lifetime. Seed solely from the capture-side
            // checkpoint; continuous lifetimes still use original deltas.
            if(!p.checkpoint.empty()&&!(key&transient)){
                const auto& cp=p.checkpoint[i];
                if(cp.flags&2U){s.gain=cp.gain;s.gainKnown=true;}
                if(cp.flags&4U){s.x=cp.x;s.y=cp.y;s.z=cp.z;s.positionKnown=true;}
                ++stats_.checkpointRestores;
            }
        }
        if (v.flags & ~15U) ++stats_.unknownFlagBlocks;
        if((v.flags&8U)||(key&transient)){ends.push_back({p.header.frames,s.lane,MetadataEnd});ended.push_back(key);}
        if (v.flags & 2) { s.gain = v.gain; s.gainKnown = true; ++stats_.updates; }
        if (v.type == 1 && (v.flags & 4)) {
            s.x=v.x; s.y=v.y; s.z=v.z; s.positionKnown=true;
            ++stats_.updates;
        }
        if (!s.gainKnown || (v.type == 1 && !s.positionKnown)) { ++stats_.unknownStateBlocks; continue; }
        // Wwise reuses persistent native pool slots: an unused slot can carry
        // an explicit zero-position/zero-gain sentinel before another voice
        // takes it. End only its renderer history, not the observed native
        // identity or metadata. Otherwise position smoothing walks towards
        // the sentinel and contaminates the next audible voice. Exact silence
        // is required; ordinary silent PCM, muted motion at a real position,
        // omitted delta fields and unknown state are not pool sentinels.
        const auto firstSample=p.planar.begin()+i*p.header.frames;
        const bool parked=v.type==1 && s.gain==0.f && s.x==0.f && s.y==0.f && s.z==0.f &&
            std::all_of(firstSample,firstSample+p.header.frames,[](float sample){return sample==0.f;});
        if(parked) {
            if(s.renderStarted)events.push_back({0,s.lane,MetadataEnd});
            s.renderStarted=false;s.parked=true;++stats_.parkedObjectBlocks;
            continue;
        }
        if(s.parked){s.parked=false;++stats_.parkedObjectRestarts;}
        {
            constexpr std::uint32_t bedTypes[]{2,4,8,16,128,256,32,64,0,0,512,1024,0,0,2048,4096};
            int bed=-1;
            for (int c=0;c<16;++c) if (bedTypes[c]==v.type) bed=c;
            if (v.type==8192 || v.type==262144) bed=0;
            if (v.type==16384 || v.type==524288) bed=1;
            if (v.type==32768) bed=4;
            if (v.type==65536) bed=5;
            const unsigned flags=MetadataGain | MetadataPosition | MetadataBed | (s.renderStarted ? 0U : MetadataBegin);
            // Native coordinates are metres, X right/Y up/Z rear. Project
            // unsupported negative height onto the floor, then scale all
            // supported axes together for positions outside the unit room.
            // Independent component clamps would turn (2,0,-4) into (1,1,0),
            // changing the direction compared with the same ray (0.5,0,-1).
            // Retain in-room point placement and the producer's PCM/gain;
            // this adapter does not add distance attenuation.
            const float height=std::max(s.y,0.f);
            const float extent=std::max({1.f,std::abs(s.x),height,std::abs(s.z)});
            events.push_back({0,s.lane,flags,bed,
                s.x/extent,v.type==131072 ? -1.f : -s.z/extent,
                height/extent,s.gain,0});
            s.renderStarted=true;
        }
        bool nonzero = false;
        for (std::size_t f = 0; f < p.header.frames; ++f) {
            const float sample = p.planar[i * p.header.frames + f];
            nonzero |= sample != 0;
            interleaved[f*160+s.lane]=sample;
        }
        if (nonzero && v.type == 1) ++stats_.nonzeroDynamicBlocks;
    }
    // EOS applies after the complete captured block. Windows has already
    // zero-filled any short EOS tail; the private frame field is block length.
    events.insert(events.end(),ends.begin(),ends.end());
    stream.renderer->Process(interleaved,p.header.frames,events,mix);
    for(const auto id:ended){stream.slots.erase(id);++stats_.retires;}
    for (float sample : mix) {
        if (!std::isfinite(sample)) throw std::runtime_error("Native spatial mix overflow");
        if (std::abs(sample) > 1) ++stats_.clippedSamples; // Headroom warning, no pre-gain hard clipping.
    }
    ++stats_.packets; stats_.frames += p.header.frames;
    return mix;
}
std::vector<float> NativeSpatialScene::FlushStream(std::uint64_t id) {
    auto it=streams_.find(id);
    if (it==streams_.end() || !it->second.renderer) return {};
    auto& renderer=*it->second.renderer;
    std::vector<float> result(renderer.Period()*layout_.speakers.size());
    result.resize(renderer.Flush(result)*layout_.speakers.size());
    return result;
}
std::vector<float> NativeSpatialScene::BreakRendering(std::uint64_t id) {
    auto tail=FlushStream(id);
    if(auto it=streams_.find(id);it!=streams_.end()) {
        // The sample clock paused, not the object lifetime. Keep witnessed
        // gain/position deltas, but finish the old lookahead before the gap.
        it->second.renderer.reset();
        for(auto& [key,slot]:it->second.slots)slot.renderStarted=false;
    }
    return tail;
}
}
