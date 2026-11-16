# Spatial ASIO License Scope

As of 0.2.7-preview.14, this project is distributed according to the following scopes. The "project" here only refers to the SpatialASIO directory and does not cover other projects in the parent warehouse.

| Scope | License |
|---|---|
| This project has its own original code, documentation, testing, build and installation scripts (except for the following exceptions) | Apache-2.0, for the full text, see LICENSE-APACHE-2.0 (reference outside this source tree) |
| ASIO access implementation: `src/asio_backend.cpp`, `src/asio_backend.h`, `src/asio_format.cpp`, `src/asio_format.h` | GPL-3.0-only, see LICENSE (reference outside this source tree) for the full text |
| The DEV16 module extracts the newly added ASIO configuration and adapts it to the old layout: `src/asio_device_config.h`, `src/asio_layout_adapter.cpp` | Follow the GPL-3.0-only implementation of ASIO access; do not expand the license scope of other independent original parts |
| DEV16 ASIO Device C ABI: `src/asio_output_c.h`, `src/asio_output_c.cpp` | GPL-3.0-only; derived interface for device access, does not change the declaration of the independent NSP core |
| `third_party/asio/` | Retain Steinberg's original statement; this release selects its GPLv3 licensed branch, see [SDK License](third_party/asio/LICENSE.txt) |
| Complete combined program including the above GPLv3 parts | Distributed in its entirety under GPL-3.0-only, while retaining Apache-2.0 and other components' Notice, License and NOTICE |
| Other third-party materials, fonts, runtimes | Retain their respective original licenses, see THIRD-PARTY-NOTICES.md (reference outside this source tree) |
| The original part from BestGreed/Native_Spatial_pipe in `tools/EndpointSpatial/` | Retain the upstream ownership and existing authorization scope, and do not add Apache authorization for the upstream code this time; the independent new contributions of this project use Apache-2.0, the source can be found in this directory README |

Apache-2.0 native parts available independently continue to be available as Apache-2.0. The full combined version is not an alternative license of `Apache-2.0 OR GPL-3.0-only`, and cannot be used to change the ASIO SDK or the entire program containing GPLv3 code to only Apache-2.0 distribution. The Apache Software Foundation explains the direction of the combination of the two in [Apache 2.0 and GPLv3 compatibility](https://www.apache.org/licenses/GPL-compatibility.html).

The root directory `LICENSE` preserves the full text of GPLv3 unmodified, as applicable to the above GPLv3 files and combined programs; it does not disqualify independent proprietary parts expressly marked as Apache-2.0. The SPDX annotation in the code defines the scope together with this document; the scope declaration of this document applies to self-owned files such as JSON and binary geometry data that do not support annotations. Third-party original statements always take precedence over the default scope of this project.

The capture and diagnostic scripts use an adjacent `<filename>.license` to save the SPDX annotation, allowing the script itself to maintain verified bytes and hashes, and avoid invalidating the original compatibility record simply because of the license annotation. These annotation files are also distributed with source code and installation packages.

`app/Fonts/`, `packaging/languages/`, `third_party/`, Python, Frida, iced-x86, and .NET runtime downloaded during build are not part of the default Apache-2.0 native scope. There is no relicensing of this third party content.

When distributing the installation package, you need to retain LICENSE, LICENSE-APACHE-2.0, LICENSING.md, NOTICE, THIRD-PARTY-NOTICES.md and each component license; at the same time, provide the complete source code and build script of the corresponding version according to GPLv3. The source code tag of this project's Release corresponds to the installation package version.

The original license authorization for the previously released version continues to be valid. This license adjustment does not revoke existing users’ rights obtained under the original license, nor does it grant third-party trademark or proprietary codec authorization.
