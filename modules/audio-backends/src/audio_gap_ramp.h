// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <span>
namespace spatial {
// Callback-owned, allocation-free transition at a real live underrun only.
// Healthy PCM is unchanged. This cannot recover missing source audio; it
// prevents an abrupt cut to/from zero from adding a broadband click.
class AudioGapRamp {
    std::array<float,32> last_{},edge_{};
    unsigned length_{96},progress_{};
    bool heard_{},missing_{},transition_{};
public:
    void Reset(double rate) {last_.fill(0);edge_.fill(0);length_=std::max(1U,unsigned(std::lround(rate*.002)));progress_=0;heard_=missing_=transition_=false;}
    void Process(std::span<float> frame,bool have) noexcept {
        if(!heard_) {
            if(!have){std::fill(frame.begin(),frame.end(),0.f);return;}
            heard_=true;
        }
        if(missing_==have){edge_=last_;missing_=!have;progress_=0;transition_=true;}
        if(transition_) {
            const double x=std::min(1.,double(++progress_)/length_);
            const double weight=x*x*(3-2*x);
            for(std::size_t c=0;c<frame.size();++c)frame[c]=float(edge_[c]*(1-weight)+(have?frame[c]*weight:0.));
            transition_=progress_<length_;
        } else if(!have)std::fill(frame.begin(),frame.end(),0.f);
        std::copy(frame.begin(),frame.end(),last_.begin());
    }
};
}
