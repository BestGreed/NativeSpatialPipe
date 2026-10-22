// SPDX-License-Identifier: GPL-3.0-only
// Capability query only: no setSampleRate/createBuffers/start/controlPanel.
#include "asio_backend.h"
#include "audio_platform.h"
#include <iostream>
int wmain(int argc, wchar_t** argv) {
    try {
        spatial::ComApartment apartment(COINIT_APARTMENTTHREADED);
        if (argc != 2) return 2;
        spatial::ProbeAsioDriverJson(argv[1]);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
