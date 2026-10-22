// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace spatial {
// A virtual 48 kHz mix domain is tied to monotonic QPC. Each input is smoothly
// rate matched into that domain; the output follows actual ASIO consumption.
// A phase servo leaves a small bounded error rather than accumulating drift.
// Abrupt missing-data intervals must be recovered separately, never disguised
// as oscillator drift by an extreme resampling rate.
class ClockRateServo {
    std::uint64_t anchorNs_{},lastNs_{};
    double anchorFrame_{},filtered_{},adjustment_{};
    std::uint64_t resets_{};
public:
    void Reset(){anchorNs_=lastNs_=0;filtered_=adjustment_=0;}
    void Observe(std::uint64_t ns,double consumed) {
        if(!ns||!std::isfinite(consumed)||ns<=lastNs_)return;
        if(!anchorNs_||ns-lastNs_>250'000'000){
            if(anchorNs_)++resets_;
            anchorNs_=lastNs_=ns;anchorFrame_=consumed;filtered_=0;return;
        }
        const double dt=(ns-lastNs_)/1e9;
        const double error=(ns-anchorNs_)*48000./1e9-(consumed-anchorFrame_);
        filtered_+=(-std::expm1(-dt/.5))*(error-filtered_);
        const double target=std::clamp(filtered_*1e-5,-.002,.002);
        adjustment_+=std::clamp(target-adjustment_,-dt*.0005,dt*.0005);
        lastNs_=ns;
    }
    double Ratio() const{return 1+adjustment_;}
    double Ppm() const{return adjustment_*1e6;}
    double PhaseMs() const{return filtered_/48.;}
    std::uint64_t Resets() const{return resets_;}
};
}
