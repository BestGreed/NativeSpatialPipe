# 第三方许可

Spatial ASIO 自有原创代码与项目作者提供的 Spatial Pip 组件采用 Apache-2.0；四个 ASIO 接入文件保留 GPL-3.0-only。包含 GPLv3 部分的完整组合程序按 GPL-3.0-only 分发，并保留 Apache-2.0 的声明与 NOTICE。具体范围见 [LICENSING.md](LICENSING.md)，全文见 `LICENSE` 和 `LICENSE-APACHE-2.0`。以下第三方材料保留其原始归属与许可，不属于自有代码的默认 Apache 授权范围。

| 组件 | 许可/用途 | 来源 |
|---|---|---|
| Steinberg ASIO SDK 接口头文件 | 选择 GPLv3 分支；接口定义 | https://www.steinberg.net/developers/ |
| Python 3.12.10 embedded x64 | PSF License；捕获宿主运行时 | https://www.python.org/downloads/release/python-31210/ |
| Frida 17.23.3 未修改 Windows wheel | wxWindows Library Licence 3.1；用户态捕获 | https://github.com/frida/frida/tree/17.23.3 |
| iced-x86 1.21.0 未修改 Windows wheel | MIT；离线核对捕获相关函数指令与结构偏移 | https://github.com/icedland/iced |
| .NET / Windows Desktop 10.0.8 | MIT 及各自第三方 notices；桌面运行时 | https://github.com/dotnet/runtime / https://github.com/dotnet/wpf |
| Inno Setup 安装器与简体中文消息 | Inno Setup License；安装器 | https://github.com/jrsoftware/issrc |
| Source Han Sans CN Regular 2.005 | SIL Open Font License 1.1；内置简体中文界面字体 | https://github.com/adobe-fonts/source-han-sans/tree/2.005R |
| libsamplerate 0.2.2 高质量 sinc 系数表 | BSD-2-Clause；自适应重采样滤波系数 | https://github.com/libsndfile/libsamplerate/tree/0.2.2 |

ASIO 许可原文：`third_party/asio/LICENSE.txt`（源码）、`Licenses/ASIO.txt`（安装版）。
libsamplerate 系数表来自提交 `c96f5e3de9c4488f4e6c97f59f5245f22fda22f7`，原表未改动；流处理、系数插值和时钟控制由本项目实现。许可位于 `third_party/libsamplerate/LICENSE` 和安装版 `Licenses/libsamplerate.txt`。
Python 许可：`Tools/NativeSpatial/python/LICENSE.txt`。
Frida 许可：`Tools/NativeSpatial/deps/frida-17.23.3.dist-info/licenses/COPYING`，该版本源代码与构建脚本可从上方对应标签获得。
iced-x86 许可随其 wheel 保留在 `Tools/NativeSpatial/deps/iced_x86-1.21.0.dist-info/`。
.NET 许可与第三方 notices 随安装版置于 `Licenses/`。安装器中文消息的作者与来源保留在 `packaging/languages/ChineseSimplified.isl`。

思源黑体原文件来自 Adobe 官方仓库，提交 `6c709ca72d3d7c46ab42ebecc1a26e7d69595a37`，`SubsetOTF/CN/SourceHanSansCN-Regular.otf`，未修改；SHA-256 `e2bc8a2e7f37474b774fff8db758681ece40bb6947a90d571bce9dd60671a8e4`。许可位于 `third_party/SourceHanSans-LICENSE.txt` 和安装版 `Licenses/SourceHanSans-LICENSE.txt`。英文字标使用系统自带 Bahnschrift，不分发该字体文件。

ASIO 是 Steinberg Media Technologies GmbH 的商标。本项目没有以任何第三方品牌作背书，也不包含音频接口厂商驱动。

Windows 端点准备整合参考并改编自 BestGreed/Native_Spatial_pipe 的
`a62cf460386d8f9890c8ce1b194a22f140096fe9`：pipeline-init 与 edid-probe。
保留其来源与原始归属，整合说明位于 `Tools/EndpointSpatial/README.md`。
此本地预览安装包不包含 Dolby / DTS 编解码器或 OEM 驱动；实际格式由已安装的
Windows 空间音频组件提供。临时许可查询响应适配不构成授予 Dolby 产品授权。
