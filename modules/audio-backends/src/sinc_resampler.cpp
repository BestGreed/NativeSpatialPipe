// SPDX-License-Identifier: Apache-2.0
#include "sinc_resampler.h"
namespace spatial {
namespace {
using coeff_t=double;
#include "../third_party/libsamplerate/high_qual_coeffs.h"
}
double HighQualitySinc::Coefficient(double distance) {
    const double index=std::abs(distance)*slow_high_qual_coeffs.increment;
    if(index>=340237)return 0;
    const auto i=static_cast<unsigned>(index);const double t=index-i;
    // Cubic interpolation of the densely sampled filter, not of audio PCM.
    // This avoids the high-frequency error of linear table interpolation.
    const double a=slow_high_qual_coeffs.coeffs[i?i-1:1],b=slow_high_qual_coeffs.coeffs[i];
    const double c=slow_high_qual_coeffs.coeffs[i+1],d=slow_high_qual_coeffs.coeffs[i+2];
    return b+.5*t*(c-a+t*(2*a-5*b+4*c-d+t*(3*(b-c)+d-a)));
}
}
