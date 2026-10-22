// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <algorithm>
#include <cstdint>
namespace spatial {
// QPC is a capture holdback/deadline, not the ASIO sample clock. Release already
// rendered frames as the driver consumes them. Never invent future PCM for
// clock assistance, resample, skip or repeat a sample to chase a timestamp.
inline std::uint64_t OutputDeadline(std::uint64_t wall,std::uint64_t cursor,
                                    std::uint64_t renderedEnd,std::uint64_t asioRequired) {
    return std::max({cursor,wall,std::min(renderedEnd,asioRequired)});
}
}
