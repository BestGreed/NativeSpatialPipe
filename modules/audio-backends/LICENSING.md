# 抽离模块来源与许可

来源：原 `SpatialASIO/` 的 0.2.7-preview.14 快照和 DEV16 模块化改动。移动目录不改变原授权；代码中的 SPDX 和第三方原始声明保持不变。

- LIRA、自有公共支持及多数测试：保留文件原 Apache-2.0 声明，许可全文 [UPSTREAM-LICENSE-APACHE-2.0](UPSTREAM-LICENSE-APACHE-2.0)。
- ASIO 接入、设备配置、布局适配和设备 C ABI：保留 GPL-3.0-only 声明，全文 [UPSTREAM-LICENSE](UPSTREAM-LICENSE)。
- `third_party/asio`：保留 SDK 原声明及本次沿用的 GPLv3 分支；`third_party/libsamplerate` 保留自身 LICENSE/ORIGIN。
- 含上述 GPL 部分的组合发行边界沿用此前决定，不能因为模块迁移便将它标成纯 Apache。发行目标与材料整理仍后置，本次不发布。

`UPSTREAM-LICENSING.md`、`UPSTREAM-NOTICE`、`UPSTREAM-THIRD-PARTY-NOTICES.md` 是原完整工程的来源快照，其中 GUI/字体/安装器路径不代表这些内容仍在本模块。当前源码集以本目录实际文件和 `source-manifest.json` 为准。
