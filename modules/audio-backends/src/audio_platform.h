// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <windows.h>
#include <wrl/client.h>
#include <string>
#include <stdexcept>
#include <utility>
#include <algorithm>
#include <cwctype>
namespace spatial {
using Microsoft::WRL::ComPtr;
inline std::wstring Lowercase(std::wstring s) { std::transform(s.begin(),s.end(),s.begin(),towlower); return s; }
inline void ThrowIfFailed(HRESULT hr,const char* what) { if (FAILED(hr)) throw std::runtime_error(std::string(what)+" HRESULT="+std::to_string(static_cast<unsigned>(hr))); }
class ComApartment {
public:
    explicit ComApartment(DWORD model=COINIT_MULTITHREADED) { ThrowIfFailed(CoInitializeEx(nullptr,model),"COM initialization"); }
    ~ComApartment() { CoUninitialize(); }
    ComApartment(const ComApartment&)=delete;
    ComApartment& operator=(const ComApartment&)=delete;
};
class WinHandle {
    HANDLE h_{};
public:
    explicit WinHandle(HANDLE h=nullptr):h_(h){}
    bool IsValid() const { return h_ && h_!=INVALID_HANDLE_VALUE; }
    HANDLE Get() const { return h_; }
    ~WinHandle() { if(IsValid()) CloseHandle(h_); }
    WinHandle(WinHandle&& other) noexcept:h_(std::exchange(other.h_,nullptr)){}
    WinHandle& operator=(WinHandle&& other) noexcept { if(this!=&other) { if(IsValid()) CloseHandle(h_); h_=std::exchange(other.h_,nullptr); } return *this; }
    WinHandle(const WinHandle&)=delete;
    WinHandle& operator=(const WinHandle&)=delete;
};
}
