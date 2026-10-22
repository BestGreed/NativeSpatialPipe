// SPDX-License-Identifier: Apache-2.0
#include "speaker_layout.h"
#include "metadata_renderer.h"
#include "audio_platform.h"
#include <cmath>
#include <set>
#include <sstream>
namespace spatial {
SpeakerLayout LoadSpeakerLayout(const std::filesystem::path& source) {
    const auto path=std::filesystem::absolute(source);
    if(!std::filesystem::is_regular_file(path)) throw std::runtime_error("Profile not found");
    auto read=[&](const wchar_t* section,const wchar_t* key,const wchar_t* fallback=L"") {
        wchar_t value[4096]{};
        if(GetPrivateProfileStringW(section,key,fallback,value,4096,path.c_str())>=4095) throw std::runtime_error("Profile value too long");
        return std::wstring(value);
    };
    auto number=[&](const wchar_t* section,const wchar_t* key,const wchar_t* fallback) {
        const auto raw=read(section,key,fallback); std::size_t consumed{}; const double v=std::stod(raw,&consumed);
        if(consumed!=raw.size() || !std::isfinite(v)) throw std::runtime_error("Invalid profile number");
        return v;
    };
    SpeakerLayout result; result.name=read(L"profile",L"layout",L"9.1.6");
    const auto mode=read(L"profile",L"render_mode",L"native");
    if(mode==L"home-theater-source")result.renderMode=SpatialRenderMode::HomeTheaterSource;
    // Old profiles fall back to native; the retired renderer is never activated.
    else if(mode!=L"native" && mode!=L"home-theater")throw std::runtime_error("Unknown spatial render mode");
    const auto canonical=Canonical916Layout();
    std::wstring names=read(L"layout",L"speakers"); std::wistringstream list(names); std::wstring name;
    std::set<std::wstring> used;
    while(std::getline(list,name,L',')) {
        if(!used.insert(SpeakerKey(name)).second) throw std::runtime_error("Duplicate speaker");
        const auto i=canonical.FindSpeaker(name); if(!i) throw std::runtime_error("Unsupported speaker name");
        auto speaker=canonical.speakers[*i];speaker.name=name; const auto section=L"speaker."+name;
        speaker.trimDb=number(section.c_str(),L"trim_db",L"0");
        if(speaker.trimDb < -60 || speaker.trimDb>12) throw std::runtime_error("Speaker trim must be -60..12 dB");
        result.speakers.push_back(speaker);
    }
    if(result.speakers.size()<2 || result.speakers.size()>16 || !result.FindSpeaker(L"FL") || !result.FindSpeaker(L"FR")) throw std::runtime_error("Layout must include FL and FR (2..16 speakers)");
    OutputRouteDefinition route; route.name=L"ASIO"; route.endpointFilter=read(L"asio",L"driver");
    if(!route.endpointFilter.starts_with(L"asio:")) throw std::runtime_error("Only ASIO output is supported");
    route.asioSampleRate=number(L"asio",L"sample_rate",L"48000");
    auto buffer=number(L"asio",L"buffer_frames",L"0");
    if(buffer<0 || buffer>65536 || buffer!=std::floor(buffer) || route.asioSampleRate<8000 || route.asioSampleRate>384000) throw std::runtime_error("Invalid ASIO rate/buffer");
    route.asioBufferFrames=static_cast<long>(buffer);
    std::set<long> channels;
    for(std::size_t i=0;i<result.speakers.size();++i) {
        const auto section=L"speaker."+result.speakers[i].name;
        auto physical=number(section.c_str(),L"channel",std::to_wstring(i+1).c_str());
        if(physical<1 || physical>256 || physical!=std::floor(physical) || !channels.insert(static_cast<long>(physical)).second) throw std::runtime_error("ASIO channel mapping must be unique (1..256)");
        route.speakerIndices.push_back(i); route.physicalChannels.push_back(static_cast<long>(physical)-1);
    }
    result.outputs.push_back(route); return result;
}
}
