// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <Windows.h>
#include <string>
#include <sstream>
#include <iomanip>
#include <stdexcept>
#include <string_view>
namespace spatial {
inline std::string Utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int bytes = WideCharToMultiByte(CP_UTF8, 0, value.data(),
                                          static_cast<int>(value.size()),
                                          nullptr, 0, nullptr, nullptr);
    if (bytes <= 0) throw std::runtime_error("Could not encode endpoint name as UTF-8");
    std::string result(static_cast<std::size_t>(bytes), '\0');
    if (WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                            result.data(), bytes, nullptr, nullptr) != bytes) {
        throw std::runtime_error("Could not encode endpoint name as UTF-8");
    }
    return result;
}

inline std::string JsonStringUtf8(std::string_view utf8) {
    std::ostringstream output;
    output << '"';
    for (const unsigned char character : utf8) {
        switch (character) {
        case '"': output << "\\\""; break;
        case '\\': output << "\\\\"; break;
        case '\b': output << "\\b"; break;
        case '\f': output << "\\f"; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (character < 0x20) {
                output << "\\u" << std::hex << std::uppercase << std::setw(4)
                       << std::setfill('0') << static_cast<unsigned>(character)
                       << std::dec << std::nouppercase;
            } else {
                output << static_cast<char>(character);
            }
        }
    }
    output << '"';
    return output.str();
}
inline std::string JsonString(const std::wstring& value) { return JsonStringUtf8(Utf8(value)); }

}
