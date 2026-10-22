// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <span>
#include <stdexcept>

namespace spatial {
// Band-limited variable-ratio interpolation. All channels use exactly the
// same source position and coefficients. Call only on the worker, never ASIO.
// The coefficients are libsamplerate 0.2.2's best-quality filter (BSD-2-Clause).
// Its documented quality is not a substitute for testing this adapter.
class HighQualitySinc {
public:
    static double Coefficient(double distance);
    static unsigned Radius(double step) {return static_cast<unsigned>(std::ceil(143.*std::max(1.,step)));}
    template<class GetFrame>
    static void Sample(double position,double step,std::size_t channels,GetFrame get,std::span<float> out) {
        if(!std::isfinite(position)||!std::isfinite(step)||step<=0||step>32||channels>32||out.size()<channels)
            throw std::runtime_error("Invalid sinc interpolation request");
        const double scale=std::min(1.,1./step);
        const auto first=static_cast<std::int64_t>(std::ceil(position-143./scale));
        const auto last=static_cast<std::int64_t>(std::floor(position+143./scale));
        std::array<double,32> sums{};double dc=0;
        for(auto f=first;f<=last;++f){
            const double weight=Coefficient((double(f)-position)*scale)*scale;dc+=weight;
            if(const float* frame=get(f))for(std::size_t c=0;c<channels;++c)sums[c]+=frame[c]*weight;
        }
        // Normalize tiny interpolation/table DC errors, including at fractional phase.
        for(std::size_t c=0;c<channels;++c)out[c]=static_cast<float>(sums[c]/dc);
    }
};
}
