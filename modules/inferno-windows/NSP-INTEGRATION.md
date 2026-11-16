# Inferno Windows：NSP 内置专用集成

2026-10-28 起，NSP 直接收录此目录的源码，Cargo 使用仓库内路径，不在构建时拉取可变分支或修改外部 fork。

## 来源与许可

- 原始 Inferno：<https://gitlab.com/lumifaza/inferno>；GitHub 镜像 <https://github.com/teodly/inferno>。
- 自维护 Windows fork：<https://github.com/BestGreed/inferno-windows>。
- 本次导入来自 NSP 本地 Windows 集成工作树，HEAD `0e770093b85c6f8b1fa6b1db0c4b7c7756ed9a8e`，包含其后未提交适配；不是公开 Windows 分支某一提交的原样副本。
- `NSP-SOURCE-MANIFEST.json` 保存导入时各仓库提交、状态、来源及 190 个文件的原始 SHA-256；它是导入快照清单，后续维护以 NSP Git 历史为准。
- 原子模块 searchfire、usrvclock-rs、alsa-sys-all 已展开为普通源码，提交记录见清单。不包含嵌套 Git 元数据，不再需要子模块初始化。
- Inferno 与 Windows 时钟包声明 `GPL-3.0-or-later OR AGPL-3.0-or-later`；保留 `inferno_aoip/LICENSE`、`LICENSE-GPL`、`LICENSE-AGPL` 原文。根项目 Apache-2.0 不覆盖本目录。
- searchfire、usrvclock-rs 各自保留 MIT/Apache 声明，alsa-sys-all 及其余包以各自 Cargo 和许可证文件为准；vendor/netdev 保留 MIT 许可证及 `PATCH.md`。不要统一改写第三方授权。

## 专用适配边界

内置版本保留 Windows QPC/PTPv1、组播/控制、保活与 TX 调度，以及 NSP 所需 `PacketSource`、发送成功后 commit、多流游标、每包帧数配置和现有统计。NSP 的池块、DSP、选流、FSA 与用户配置仍在 NSP 自有模块，不反向搬入此目录。

本目录保留原 workspace 和 Linux 相关源码以保持来源完整；NSP 构建只选择所需包。Windows 测试宿主仍是实验入口；250 µs TX、长期时钟漂移补偿和完整 Dante 遥测没有因此完成。

## 构建与维护

产品入口：`tools/aoip/Build-NativeOutput.ps1`。时钟和独立收发诊断脚本也引用本目录。构建不再应用 `native-packet-source.patch`，该补丁仅保留为历史参考。

升级通用 Windows fork 时显式比较来源提交与 NSP 专用差异，完成构建和回归后再更新；不自动覆盖本目录。原 `work/inferno-aoip` 暂留作历史工作树，本目录不依赖它。ROAR 与编译工具链等其他外部依赖不在本次迁移范围。
