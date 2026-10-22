// SPDX-License-Identifier: Apache-2.0
#include "speaker_layout.h"
#include <cwctype>
namespace spatial {
std::wstring SpeakerKey(std::wstring_view name) {
    auto key=std::wstring(name); for(auto& c:key)c=static_cast<wchar_t>(std::towlower(c));
    static constexpr std::pair<std::wstring_view,std::wstring_view> aliases[]{
        {L"l",L"fl"},{L"r",L"fr"},{L"c",L"fc"},{L"lw",L"fwl"},{L"rw",L"fwr"},
        {L"ls",L"sl"},{L"rs",L"sr"},{L"lrs",L"bl"},{L"rrs",L"br"},
        {L"ltf",L"tfl"},{L"rtf",L"tfr"},{L"ltm",L"tml"},{L"rtm",L"tmr"},{L"ltr",L"tbl"},{L"rtr",L"tbr"}};
    for(const auto& [alias,canonical]:aliases)if(key==alias)return std::wstring(canonical);
    return key;
}
std::optional<std::size_t> SpeakerLayout::FindSpeaker(std::wstring_view speakerName) const {
    const auto key=SpeakerKey(speakerName);
    for(std::size_t i=0;i<speakers.size();++i) if(SpeakerKey(speakers[i].name)==key) return i;
    return {};
}
}
