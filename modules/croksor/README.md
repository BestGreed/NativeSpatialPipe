# Croksor

Croksor 取意于 Crossover，负责 NSp 的实时音频处理和输出交接。Rust crate 名为 `nsp-croksor`，Windows 产物为 `nsp_croksor.dll`。

| 源码位置 | 职责 |
| --- | --- |
| `src/input/` | 捕获数据解释、统一输入对齐、截止时间和渲染唤醒 |
| `src/render/` | 渲染后端分派、ROAR OLR 输入适配、LIRA DLL 适配及布局定义 |
| `src/dsp/` | 公共输出处理、限幅、可选上混和 LSAC 适配 |
| `src/output/` | 池块租借与归还、输出路由和 AoIP 交接 |
| `src/observability/` | 遥测 tap 和录音旁路 |
| `src/packet.rs` | 实时处理的装配、worker 和既有 C ABI |

使用 [Build.ps1](Build.ps1) 单独构建。ROAR 1.1 的固定版本及补丁由构建入口校验，Inferno 使用仓库内置源码。产品配置中的 `bridgeDll` 应指向本次构建的 `nsp_croksor.dll`。C ABI 导出沿用现有名称。
