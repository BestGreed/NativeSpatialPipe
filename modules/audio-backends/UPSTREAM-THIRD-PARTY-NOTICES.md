# third party license

Spatial ASIO's own original code and the Spatial Pip component provided by the project author use Apache-2.0; the four ASIO access files remain GPL-3.0-only. The complete combined program, including the GPLv3 portion, is distributed under GPL-3.0-only and retains the Apache-2.0 Notice and NOTICE. See [LICENSING.md](LICENSING.md) for the specific scope, and `LICENSE` and `LICENSE-APACHE-2.0` for the full text. The following third-party material retains its original attribution and license and is not covered by the default Apache license for own code.

| Components | License/Use | Source |
|---|---|---|
| Steinberg ASIO SDK interface header file | Select GPLv3 branch; interface definition | https://www.steinberg.net/developers/ |
| Python 3.12.10 embedded x64 | PSF License; capture host runtime | https://www.python.org/downloads/release/python-31210/ |
| Frida 17.23.3 Unmodified Windows wheel | wxWindows Library License 3.1; User mode capture | https://github.com/frida/frida/tree/17.23.3 |
| iced-x86 1.21.0 unmodified Windows wheel | MIT; offline verification captures relevant function instructions and structure offsets | https://github.com/icedland/iced |
| .NET / Windows Desktop 10.0.8 | MIT and respective third-party notices; Desktop runtime | https://github.com/dotnet/runtime / https://github.com/dotnet/wpf |
| Inno Setup installer and Simplified Chinese messages | Inno Setup License; Installer | https://github.com/jrsoftware/issrc |
| Source Han Sans CN Regular 2.005 | SIL Open Font License 1.1; built-in Simplified Chinese interface font | https://github.com/adobe-fonts/source-han-sans/tree/2.005R |
| libsamplerate 0.2.2 High-quality sinc coefficient table | BSD-2-Clause; adaptive resampling filter coefficients | https://github.com/libsndfile/libsamplerate/tree/0.2.2 |

ASIO license original text: `third_party/asio/LICENSE.txt` (source code), `Licenses/ASIO.txt` (installation version).
The libsamplerate coefficient table comes from submission `c96f5e3de9c4488f4e6c97f59f5245f22fda22f7`, and the original table has not been changed; stream processing, coefficient interpolation and clock control are implemented by this project. The license is located in `third_party/libsamplerate/LICENSE` and the installation version `Licenses/libsamplerate.txt`.
Python license: `Tools/NativeSpatial/python/LICENSE.txt`.
Frida license: `Tools/NativeSpatial/deps/frida-17.23.3.dist-info/licenses/COPYING`, the source code and build script of this version are available from the corresponding tag above.
The iced-x86 license remains with its wheel at `Tools/NativeSpatial/deps/iced_x86-1.21.0.dist-info/`.
.NET licensing and third-party notices are included with the installation at `Licenses/`. The author and source of the installer's Chinese messages remain in `packaging/languages/ChineseSimplified.isl`.

The original file of Siyuan Helvetica comes from the official Adobe warehouse, submitted `6c709ca72d3d7c46ab42ebecc1a26e7d69595a37`, `SubsetOTF/CN/SourceHanSansCN-Regular.otf`, unmodified; SHA-256 `e2bc8a2e7f37474b774fff8db758681ece40bb6947a90d571bce9dd60671a8e4`. License is in `third_party/SourceHanSans-LICENSE.txt` and installation version `Licenses/SourceHanSans-LICENSE.txt`. The English logo uses Bahnschrift that comes with the system and does not distribute the font file.

ASIO is a trademark of Steinberg Media Technologies GmbH. This project is not endorsed by any third-party brand, nor does it include audio interface manufacturer drivers.

Windows endpoint ready integration reference and adapted from BestGreed/Native_Spatial_pipe
`a62cf460386d8f9890c8ce1b194a22f140096fe9`: pipeline-init and edid-probe.
Preserving its provenance and original attribution, integration instructions are located at `Tools/EndpointSpatial/README.md`.
This local preview installation package does not contain Dolby/DTS codecs or OEM drivers; the actual format is determined by the installed
Provided by Windows Spatial Audio component. Temporary license query response adaptation does not constitute the grant of a Dolby product license.
