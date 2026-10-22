# Spatial ASIO 许可范围

自 0.2.7-preview.14 起，本项目按下列范围分发。这里的“项目”仅指 SpatialASIO 目录，不覆盖父仓库其他工程。

| 范围 | 许可 |
|---|---|
| 本项目自有原创代码、文档、测试、构建与安装脚本（以下例外除外） | Apache-2.0，全文见 LICENSE-APACHE-2.0（开发参考未随公开源码提供） |
| ASIO 接入实现：`src/asio_backend.cpp`、`src/asio_backend.h`、`src/asio_format.cpp`、`src/asio_format.h` | GPL-3.0-only，全文见 LICENSE（开发参考未随公开源码提供） |
| DEV16 模块抽离新增的 ASIO 配置与旧布局适配：`src/asio_device_config.h`、`src/asio_layout_adapter.cpp` | 沿用 ASIO 接入实现的 GPL-3.0-only；不扩大其他独立原创部分的许可范围 |
| DEV16 ASIO 设备 C ABI：`src/asio_output_c.h`、`src/asio_output_c.cpp` | GPL-3.0-only；设备接入的派生接口，不改变独立 NSP 核心的声明 |
| `third_party/asio/` | 保留 Steinberg 原声明；本发行选择其 GPLv3 授权分支，见 [SDK 许可](third_party/asio/LICENSE.txt) |
| 包含上述 GPLv3 部分的完整组合程序 | 整体按 GPL-3.0-only 分发，同时保留 Apache-2.0 及其他组件的声明、许可与 NOTICE |
| 其他第三方材料、字体、运行时 | 保留各自原始许可，见 THIRD-PARTY-NOTICES.md（开发参考未随公开源码提供） |
| `tools/EndpointSpatial/` 中来自 BestGreed/Native_Spatial_pipe 的原始部分 | 保留上游归属与既有授权范围，本次不替上游代码新增 Apache 授权；本项目独立新增的贡献采用 Apache-2.0，来源见该目录 README |

可独立使用的 Apache-2.0 自有部分继续可按 Apache-2.0 使用。完整组合版本不是 `Apache-2.0 OR GPL-3.0-only` 的二选一授权，不能据此把 ASIO SDK 或整个含 GPLv3 代码的程序改为仅 Apache-2.0 分发。Apache Software Foundation 对两者组合方向的说明见 [Apache 2.0 与 GPLv3 兼容性](https://www.apache.org/licenses/GPL-compatibility.html)。

根目录 `LICENSE` 保留未经修改的 GPLv3 全文，适用于上述 GPLv3 文件及组合程序；它不取消明确标注为 Apache-2.0 的独立自有部分的授权。代码内的 SPDX 标注与本文件一同明确范围；JSON、二进制几何数据等不支持注释的自有文件适用本文件的范围声明。第三方原声明始终优先于本项目的默认范围。

捕获与诊断脚本使用相邻的 `<文件名>.license` 保存 SPDX 标注，使脚本本身保持已验证的字节和哈希，避免仅因许可证注释失效原有兼容性记录。这些旁注文件同样随源码和安装包分发。

`app/Fonts/`、`packaging/languages/`、`third_party/`、构建时下载的 Python、Frida、iced-x86、.NET 运行时均不属于默认 Apache-2.0 自有范围。没有重新授权这些第三方内容。

分发安装包时需保留 LICENSE、LICENSE-APACHE-2.0、LICENSING.md、NOTICE、THIRD-PARTY-NOTICES.md 及各组件许可；同时按 GPLv3 提供对应版本的完整源码和构建脚本。本项目 Release 的源码标签与安装包版本对应。

此前已发布版本的原有许可授权继续有效。本次许可调整不撤回既有用户在原许可下获得的权利，也不授予第三方商标或专有编解码器授权。
