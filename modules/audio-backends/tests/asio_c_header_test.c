// SPDX-License-Identifier: Apache-2.0
#include "asio_output_c.h"
_Static_assert(sizeof(nsp_asio_config)==64,"Windows x64 ABI v1 config size");
_Static_assert(sizeof(nsp_asio_stats)==96,"Windows x64 ABI v1 stats size");
uint32_t nsp_asio_c_config_size(void) { return (uint32_t)sizeof(nsp_asio_config); }
uint32_t nsp_asio_c_stats_size(void) { return (uint32_t)sizeof(nsp_asio_stats); }
int32_t nsp_asio_c_open(const nsp_asio_config* config,nsp_asio_handle* out) {
    return nsp_asio_open(config,out);
}
