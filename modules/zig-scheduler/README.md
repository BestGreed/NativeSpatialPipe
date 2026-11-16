# NSP 原生调度与配置宿主

更新于 2026-11-08。当前分工和验收状态见[活跃进度](../../docs/core/active-progress.md)。N05–N12阶段接线与旧部署说法移到[历史快照](../../docs/archive/pipeline-convergence-20261027/zig-scheduler-before-convergence.md)。

## 职责

Zig负责FSA控制协议、会话、所有者与停止证明、配置校验、ASIO owner控制、AoIP常驻协调和普通权限Web Host。PCM由C/Rust/C++原生模块处理；公共DSP在Rust writer，`output_processing.zig`仅保留配置/timing/ABI，不逐块回调处理PCM。

产品主入口是原生FSA，调度器只支持Zig。旧Python控制后端及其诊断已完整归档，FSA和游戏托管入口不再接受旧调度选择。Python/PowerShell仍分别用于准备/遥测和前端WinRT格式选择/恢复；不能把“Zig调度”说成整条管理链已原生化。

## 构建

- 既有ASIO宿主：`powershell -ExecutionPolicy Bypass -File modules/zig-scheduler/Build.ps1`，输出`build/nsp-product/nsp-scheduler.exe`。
- AoIP配套宿主/桥接：`powershell -File tools/Build-Product.ps1 -Python <python.exe>`。
- Zig固定0.13.0；ABI不匹配必须拒绝，host和bridge成套使用。Inferno 使用仓库内 modules/inferno-windows，C 头文件来自 modules/native-capture；ROAR 和工具链仍有本地准备依赖，并非发行安装器。网络诊断在 tools/network-diagnostics，不是 AoIP 产品启动步骤。

新版本先构建独立候选，正常关闭FSA/NSP后再替换使用；不热覆盖加载中的DLL。不同build目录不代表相同能力或已经部署。

## 运行入口

| 命令/模块 | 当前用途 |
| --- | --- |
| 控制CLI / `serve` | FSA会话、状态、受保护停止及恢复；主要实现`control.zig/backend.zig` |
| `host` | 捕获、渲染与输出；ASIO/WASAPI 使用会话宿主，统一 WASAPI 将四个空间入口和一路 PCM 接到同一个 ROAR 实例 |
| `media-foundation` | FSA 控制入口，按播放 JSON 调用 MFT 控制器，并写出用户目录的 `mft-status.json` |
| `standby-start` / `aoip-resident` | FSA拥有的AoIP常驻网络宿主，无PCM发静音 |
| `resident-session` | 控制等待器，将捕获会话挂入常驻宿主；不传PCM |
| `web-host` | 普通权限状态/配置/静态前端，默认127.0.0.1:8767，不暴露音频启停 |
| `validate-playback --playback-config <absolute-json>` | 不打开设备的配置校验与有效配置说明 |
| 后端`select-output`动作 | AoIP运行时选流，不等于后端热切换；需匹配owner/session |

配置见 [JSON Schema](playback.schema.json)、[模块开关](../../docs/core/playback-json-configuration-20261022.md#可选模块开关)与 [WASAPI 示例](playback.wasapi.example.json)。`validate-playback` 分别报告核心 `outputLayout` 和物理 `deviceOutputLayout`。修改输出路径后重新建立对应会话或 AoIP 常驻实例。AoIP 运行时选流使用独立接口。

通用用户目录为`%LOCALAPPDATA%/NativeSpatialPipe`，默认播放文件`playback.json`；明确的FSA INI PlaybackConfig可覆盖，当前AoIP候选使用同目录`output.json`。`--home`用于显式隔离，不能拿隔离测试状态冒充实际用户会话。

## 验证与未完成项

现有配置/路由/控制/输出池测试继续保留，具体命令及fixture见同目录测试、`tools/integration-tests`与`tools/aoip/Test-Configuration.py`。软件测试、FSA有限实网、用户确认出声分别记录，不相互替代。

P0仍包括固定hash/profile跨系统兼容、Hook稳定性、确定性捕获绑定、Inferno依赖闭包、输出fault诊断和常驻启动失败回收；WDM、长期SRC、配置loaded revision及遥测故障隔离尚未完成。顺序见[修复计划](../../docs/plans/nsp-pipeline-reliability-plan-20261027.md)。历史“未接N07/只待首轮真实音频”不再作为当前状态。
