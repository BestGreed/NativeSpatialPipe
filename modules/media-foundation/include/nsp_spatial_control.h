#pragma once
#include <mfidl.h>
inline constexpr GUID IID_INspSpatialControl = {
    0x682b8893,
    0x7a46,
    0x4a41,
    {0xb3, 0x0b, 0xdc, 0x38, 0x40, 0xdb, 0x3c, 0x21}};
// Same-process control contract. Clock must be the real playback presentation
// clock.
MIDL_INTERFACE("682B8893-7A46-4A41-B30B-DC3840DB3C21")
INspSpatialControl : IUnknown {
  virtual HRESULT STDMETHODCALLTYPE BindPresentationClock(
      IMFPresentationClock *, LPCWSTR endpoint,
      LONGLONG sourceToClockOffset100ns) = 0;
  virtual HRESULT STDMETHODCALLTYPE GetSpatialStatistics(
      UINT64 * submittedFrames, UINT64 * nonzeroValues, HRESULT * result) = 0;
};
