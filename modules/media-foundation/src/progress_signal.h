// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <atomic>
#include <windows.h>

struct ProgressSignal {
  HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  std::atomic<bool> armed{false};
  ~ProgressSignal() {
    if (event)
      CloseHandle(event);
  }
  void signal() {
    if (event)
      SetEvent(event);
  }
  void notifyCapacity() {
    if (armed.exchange(false, std::memory_order_acq_rel))
      signal();
  }
};
