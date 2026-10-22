// SPDX-License-Identifier: Apache-2.0
#include "speaker_layout.h"
#include "audio_platform.h"
#include <map>
#include <set>
#include <cmath>
#include <algorithm>
#include <numbers>
namespace spatial {
constexpr double kPi=std::numbers::pi;
std::vector<double> PanAdmOarPoint(const SpeakerLayout& layout, double x, double y,
                                  double z, double anchorTolerance) {
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) ||
        std::abs(x)>1 || std::abs(y)>1 || z<0 || z>1 || anchorTolerance!=0)
        throw std::invalid_argument("OAR point mode requires Cartesian unit-room positions and zero snap tolerance");
    // Numeric geometry and quantization verified against local DSP CPU probes.
    // This is an independent mathematical implementation, with no proprietary
    // binary dependency. The active layout alone builds the panning mesh;
    // no intermediate 9.1.6 signal or post-render downmix is used.
    static const std::map<std::wstring,std::array<int,3>> points = {
        {L"fl",{0,0,0}}, {L"fr",{32767,0,0}}, {L"fc",{16384,0,0}},
        {L"sl",{0,16384,0}}, {L"sr",{32767,16384,0}},
        {L"bl",{0,32767,0}}, {L"br",{32767,32767,0}},
        {L"fwl",{0,5285,0}}, {L"fwr",{32767,5285,0}},
        {L"tfl",{7928,7928,32767}}, {L"tfr",{24840,7928,32767}},
        {L"tml",{7928,16384,32767}}, {L"tmr",{24840,16384,32767}},
        {L"tbl",{7928,24840,32767}}, {L"tbr",{24840,24840,32767}}
    };
    if (layout.speakers.size()<2 || layout.speakers.size()>16 ||
        !layout.FindSpeaker(L"FL") || !layout.FindSpeaker(L"FR"))
        throw std::invalid_argument("Point rendering requires 2..16 speakers including FL and FR");
    struct Prepared {
        std::vector<std::wstring> names;
        std::map<int,std::map<float,std::map<float,std::size_t>>> layers;
    };
    // One bounded cache per rendering thread, keyed by the complete semantic
    // order (never by a layout pointer whose lifetime could end).
    static thread_local Prepared prepared;
    bool same=prepared.names.size()==layout.speakers.size();
    for(std::size_t i=0;same && i<layout.speakers.size();++i) same=prepared.names[i]==layout.speakers[i].name;
    if(!same) {
        Prepared next;
        std::set<std::wstring> seen;
        const bool surroundIsRear=!layout.FindSpeaker(L"BL") && !layout.FindSpeaker(L"BR");
        for (std::size_t i=0;i<layout.speakers.size();++i) {
            const auto name=SpeakerKey(layout.speakers[i].name);
            if (!seen.insert(name).second) throw std::invalid_argument("Duplicate OAR speaker");
            if (name==L"lfe") continue;
            const auto it=points.find(name);
            if (it==points.end()) throw std::invalid_argument("Unsupported OAR speaker");
            auto p=it->second;
            // A 5.x layout has one surround pair and two ground rows. That
            // pair spans the rear of the room; retaining the 7.x side row
            // would collapse the entire rear half of moving trajectories.
            if(surroundIsRear && (name==L"sl" || name==L"sr"))p[1]=32767;
            next.layers[p[2]!=0][p[1]/32768.f].emplace(p[0]/32768.f,i);
        }
        for(const auto& s:layout.speakers) next.names.push_back(s.name);
        prepared=std::move(next);
    }
    const auto& layers=prepared.layers;
    const auto quantize=[](double v,int steps) {
        const float input=static_cast<float>(v);
        const long n=std::clamp(std::lround(static_cast<double>(input)*steps),0L,static_cast<long>(steps));
        return std::min(32767L,n*32768/steps)/32768.f;
    };
    const float px=quantize((x+1)/2,310),py=quantize((1-y)/2,310),pz=quantize(z,75);
    const auto pair=[](float t) -> std::array<float,2> {
        if (t<=0) return {1,0};
        if (t>=1) return {0,1};
        const bool flip=t>.5f;
        const float u=flip ? 1.f-t : t;
        const float angle=((u*static_cast<float>(kPi/4))*.5f)*4.f;
        const float co=std::cos(angle),si=std::sin(angle);
        return flip ? std::array<float,2>{si,co} : std::array<float,2>{co,si};
    };
    const auto select=[&](const auto& coordinates,float v) {
        std::vector<std::pair<float,float>> weights;
        const auto hi=coordinates.lower_bound(v);
        if (hi==coordinates.end()) weights.push_back({coordinates.rbegin()->first,1.f});
        else if (hi==coordinates.begin() || hi->first==v) weights.push_back({hi->first,1.f});
        else {
            const auto lo=std::prev(hi);
            const float t=static_cast<float>(static_cast<double>(v-lo->first)/(hi->first-lo->first));
            const auto g=pair(t);
            weights.push_back({lo->first,g[0]});weights.push_back({hi->first,g[1]});
        }
        return weights;
    };
    // With no height layer, height has no independent output dimension. Do
    // not attenuate to silence as z approaches one. A two-speaker layout also
    // clamps the unavailable front/back dimension to its single row.
    const auto zg=layers.size()==1 ? std::array<float,2>{1,1} : pair(pz>=32767/32768.f ? 1.f : pz);
    std::vector<float> values(layout.speakers.size());
    for (const auto& [layer,rows]:layers)
        for (const auto& [row,yg]:select(rows,py)) {
            const auto& columns=rows.at(row);
            for (const auto& [column,xg]:select(columns,px))
                values[columns.at(column)]=(xg*yg)*zg[layer];
        }
    double power=0;
    for (const float v:values) power+=static_cast<double>(v*v);
    const float norm=static_cast<float>(std::sqrt(power));
    std::vector<double> result(values.size());
    if (norm>0) for (std::size_t i=0;i<values.size();++i) result[i]=values[i]/norm;
    return result;
}


}
