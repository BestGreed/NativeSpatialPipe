# Native Spatial Pipe · alpha-dev

Windows 空间音频对象流捕获、原生渲染与实时可视化。2026-10-22 开发集成源码，不是已注册的 Spatial Sound Provider，也不是 Dolby/DTS 码流解码器。

**当前进度：N03–N12 限定集成已完成，后续池内 DSP／bypass 与池块租借已接通；临时 WPF 已弃用。** 本轮工作区清理及源码分支同步已完成，完整进度和未完成项见 [alpha-dev 说明](docs/alpha-dev.md)。

```text
C Hook / capture → 共享内存 → Rust 接收与公共转换
 → ROAR（本轮验证 OLR）或 LIRA → Rust interleaved 输出池
 → 池内 DSP / bypass → 池块租借 → ASIO
旁路：现有文件记录 → Python 遥测 → Web 可视化
控制：FSA 原生端点管理 → Zig 会话协调 / 后端控制线程
```

ASIO 设备内部缓冲、SRC 和时钟匹配继续保留。输出池移除了 Zig PCM 队列与 scratch 的两次复制；中性 DSP 自动跳过，或显式 `outputProcessing.bypass=true`。配置字段见 [播放 JSON](docs/core/playback-json-configuration-20261022.md)，默认模板和 schema 位于 tools/zig-scheduler。

本轮限定验证覆盖 DVS 48 kHz / 128 帧、12 路网络数字回送、经典 Wwise 的 Atmos HT＋ROAR/OLR 与 DTS:X HT＋LIRA；不能据此声称全部驱动、声学端到端延迟或长期稳定性通过。新输出池实机已确认出声，但一次 OutputTimeout 收尾仍待排查。旧性能数字不自动代表此版本。

## 构建与入口

先按 [依赖与核心构建](docs/build.md)准备固定 MinHook、ROAR、Rust、LLVM/MinGW 和 Zig 0.13.0。源码仓库不附带工具链、私有录音、Wwise 工程或商业 codec DLL。

```powershell
./tools/Build-Alpha.ps1 -LlvmBin C:/toolchains/llvm-mingw/bin -Zig C:/toolchains/zig-0.13.0/zig.exe
```

构建核心、LIRA、ASIO DLL 与 Zig 后端，不打开驱动、不注入系统。现有 FSA 接入见 [集成说明](tools/fsa-integration/README.md)，后端运行与可选参数见 [调度说明](tools/zig-scheduler/README.md)。构建不会部署或重启 FSA。需要管理员权限的实际 Hook 操作应由使用者明确启动。

原生音频/管理链没有 .NET 运行时依赖；Python 仍用于遥测与测试。完整 SpatialASIO、WPF 临时界面及安装工程未导出。独立 Web API 控制 host 只是后续方向，尚未实现；现有遥测保持只读。

## 当前边界

当前仍有连续 PCM/遥测落盘，活跃 12 通道 Float32 PCM 约 8.29 GB/h；D15 解耦和默认记录策略尚未实现。配置、会话数据通常位于 `%LOCALAPPDATA%/NativeSpatialPipe`。源码分支不含这些运行数据。

保持捕获传输故障严格退出；ASIO 暂时欠载允许有界恢复，设备故障安全停止。LIRA 保留其房间投影、裁剪及平滑等原有行为；公共层不替它定义声音风格。完整半径 OLR 映射默认 scaled/reference=1，是软件单位合同，并非物理房间标定。

## 许可与贡献

[LICENSE](LICENSE) 中 Apache-2.0 只覆盖原创 NSP 范围。LIRA 保留原 Apache 声明；ASIO 设备代码及所附 SDK 头文件具有 GPL-3.0-only 范围，含它们的组合程序不能标为纯 Apache。详见 [许可范围](LICENSING.md)、[第三方声明](THIRD_PARTY_NOTICES.md)及 [模块来源](modules/audio-backends/LICENSING.md)。FSA 源码与图标分别适用自身声明。

感谢 hikari-taritsu（rmj）的 Spatial ASIO / LIRA 原始贡献。独立公开历史保留原作者/授权声明，来源与导出范围见 [alpha-dev 说明](docs/alpha-dev.md)和 [源码映射](docs/core-source-manifest.json)。公开 main 保持旧基线，本轮只更新 alpha-dev。

[文档索引](docs/README.md)
