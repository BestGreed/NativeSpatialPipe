# NSp Zig 调度层

当前技术栈与文档权威入口见[架构](../../docs/core/current-architecture.md)。Zig 负责默认调度和公共 DSP；Python 仍用于准备控制与独立遥测，PowerShell/WinRT 仍执行系统互操作。下方 DEV16 分轮日志只作历史证据，其中“未接线”、固定布局、旧 WPF 或有限播放策略不构成当前待办，不得重新引入产品链。

2026-10-24 playback location: `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` is now the default. The old installation-adjacent default is copied without replacing existing user data; explicit custom paths remain supported. See [migration](../../docs/core/playback-user-location-20261024.md).

## 当前实现（2026-10-24）

正式 host 通过 `nsp_pipeline_create_live` 初始化 ROAR/OLR 或 LIRA，支持七种输出布局、池内 DSP 与池块租借。`recording.pcm`、`recording.telemetrySnapshots` 默认均关闭；旁录使用独立线程与 32 块专用录音池，播放交付不执行文件 IO。遥测候选流通过 `nsp_pipeline_progress` 内存计数选取，不依赖录音文件。旧桥缺少 live 导出时明确失败。

独立 `web-host` 随 FSA 启动，默认 `8767`，仅查询状态、校验/保存 JSON，不提供运行控制。默认播放 JSON 为 `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`，显式自定义 `PlaybackConfig` 路径继续有效；旧安装目录默认文件迁移为用户副本，保留原件且不覆盖已有目标。默认保存及修订锁不写安装目录。保存后下次启动会话生效；后端状态目录保持不变。

默认完整半径映射 `scaled/1`。ASIO 会话内常驻，无输入时静音；旧首包/重缓冲超时字段保留但不生效。模块运行库从 DLL 目录加载。已完成有限实机启停，14/16 路实际接线、6DoF 插槽启用及长期稳定性另验。

参见[布局](../../docs/core/output-layout-presets-20261023.md)、[旁录与 ABI](../../docs/core/recording-isolation-20261024.md)、本轮验证（开发参考未随公开源码提供）。下方 N05–N12 的“未部署/未接线”只描述当时状态。

2026-10-23 启动修复：默认渲染桥、preflight 和 Web 候选路径统一为 `build/roar-native/release/nsp_roar_bridge.dll`，支持当前默认完整半径映射。无 Hook 身份记录的启动失败须通过进程退出、端点恢复及实时 Hook 基线证据后才解除锁定，原失败报告保留。DTS:X HT 实机启动/停止闭环通过，见 修复与验证（开发参考未随公开源码提供）。

2026-10-23：已修正 [ASIO 会话寿命](../../docs/decisions/asio-session-lifetime-20261023.md)：输入缺席时持续静音回调，不再触发首包等待、重缓冲超时或次数退出。显式停止仍有排空期限，设备和捕获/传输故障仍须安全退出。旧超时 JSON 字段保留解析兼容，新的有效策略通过 `effectiveOutputLifecycle` / `playbackPolicy` 报告。此更新替代下文历史轮次中的有限播放策略。

**当前状态（2026-10-23，alpha-dev）：** N03–N12 已完成限定开发集成，FSA 参数已接通，ROAR／OLR 与 LIRA 共用公共输出和 ASIO；DVS 12 路数字回送及两组 Wwise HT 实测已完成。池内 DSP／bypass 和池块租借已接通，临时 WPF 已弃用。ASIO 已改为会话内常驻送流；独立 Web API Host 和图形配置页已实现，仅读状态、读写配置，不提供运行控制。D15 与 OG27QH EDID 驱动重载实机回归等仍单独跟踪。以下按轮次排列的“尚未接线／未做真实播放”描述仅对应当时阶段，不能覆盖当前状态。

公共 DSP 已前移到 Rust 输出池块，并支持 `outputProcessing.bypass` 及中性配置自动跳过。设备 owner 通过池块租借提交，移除 Zig PCM 队列和 scratch 复制；ASIO 内部算法不变。成套候选及验证见 [输出池记录](../../docs/core/output-pool-processing-20261022.md)。

DEV16 N11 曾增加独立桌面客户端和可选会话 `playback` 配置；旧 FSA 请求保持兼容，其新参数在 N12 接线。**临时 WPF 桌面已于 2026-10-22 弃用**，会话与端点管理以 FSA 为准；公共播放配置和控制接口继续保留。原因与历史边界见 已弃用临时桌面（开发参考未随公开源码提供） 和 N11交付记录（开发参考未随公开源码提供）。

DEV16 N10 已接入 LIRA（Lazy Immersive Rendering Architecture）：`--renderer lira --lira-renderer-dll <绝对DLL路径> --render-bridge-dll <本轮bridge路径>`。旧 `spatial` 参数保留兼容。默认仍为 OLR；LIRA 拒绝 OLR 专属 position 参数。共享公共输入、可靠输出与 ASIO 路径，保留 512 帧固有延迟及完整尾部；接口、构建与限定验证（开发参考未随公开源码提供）。本轮独立产物位于 `build/dev16-n10-renderer/`，未替换运行目录。

DEV16 N05 新增 `output_control.zig` 专用后端worker和 `asio_control.zig` 生命周期适配；不自动挂入当前会话。调用/代次/退出合同见 N05交付（开发参考未随公开源码提供）。独立完整检查：`python tools/asio-output/Test-ControlWorker.py`，仅模拟后端与假ASIO DLL；实时输出接线在N07。

N06新增 `output_processing.zig` 公共PCM处理器，配置与尾部合同见 N06交付（开发参考未随公开源码提供）。离线检查：`python tools/zig-scheduler/Test-OutputProcessing.py`。默认位一致直通，limiter默认关闭；实际播放调用留N07。

N07已通过 `output_stream.zig` / `output_playback.zig` 接入可靠PCM和owner服务循环，正式host提供显式ASIO启用参数；见 接线、参数与测试（开发参考未随公开源码提供）。`python tools/asio-output/Test-Playback.py`验证实际OLR到假ASIO回调的软件闭环；现阶段未部署、未做真实驱动播放。

N09：新DEV16 host默认 `--position-mode scaled --position-reference 1`，保持完整源半径数值，仅换轴、不额外缩放。`--position-mode direction` 保留旧方向基线；reference仅允许正有限值，direction下仅接受1。scaled需要配套新桥接DLL的增量导出，缺失时明确失败，不静默回退。详情与声音变化见N09记录（开发参考未随公开源码提供）。这不是物理房间标定；现用二进制未部署替换。N08实机与网络回送证据见对应记录。

迁移对象是原 Python 管理与编排层，现有 C Hook、C 捕获/共享内存和 Rust 接收/渲染 DLL 均保持不变。遥测仍按原顺序先启动，计算与 HTTP/WebSocket 服务继续使用原 Python 实现。本阶段没有把遥测从启动链中拆开。

## 已迁移

- `backend.zig`：常驻后端、单实例、配置与会话持久化、HTTP 控制及前端文件服务、子进程监控、停止超时和恢复锁定。
- `control.zig`：FSA 的 preflight/start/status/stop/emergency-stop、目标实例检查、关联状态文件与新鲜度协议。
- `native.zig`：直接调用现有管线与接收端 C ABI，调用原生 Hook 控制器；不经过 ctypes/Python 编排。
- `host.zig`：四条原生管线预热、遥测进程管理、原生接收端启动、Hook 生命周期、非实时遥测转发、可选快照存档及结束排空。
- `routing.zig`：按 renderer 内存进度选择遥测候选流，轮询 100 ms、失效 1 s、新候选两次增长后抢占；不受录音开关影响。
- FSA 默认 `[NSP] Scheduler=Zig`；`ControlExecutable` 可指定 Zig EXE，留空时按正式构建布局解析。`Python` 配置仅传给遥测服务。缺少 Zig 不隐式回退；旧管理路径必须显式设置 `Scheduler=Python`。

启动顺序：首条原生管线准备 → Python 遥测就绪 → 其余三条管线与原生接收端准备 → Hook 启用。遥测启动失败会阻止 Hook 启用。

退出顺序：确认 Hook 关闭并恢复指令 → 等待已捕获包接收 → 接收端停止并确认线程退出 → 遥测桥接/记录线程退出 → 渲染排空并读取最后 tap → 销毁管线 → 关闭遥测。无法确认 Hook/接收端停止时保持资源，不强杀现场进程。

空间格式的设置和恢复仍使用现有 `Run-GameTelemetry.ps1`（Windows 自带 PowerShell/WinRT）；它的原生分支直接启动 Zig host，不执行 `Sync-Hook.py`。现阶段仅迁移正式托管模式，历史 Frida、离线实验和原 Python 测试工具保留。

## 构建

固定使用 Zig 0.13.0，避免编译器标准库版本漂移。编译器仅用于构建，运行时无需安装 Zig。

- 官方下载：<https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip>
- ZIP SHA-256：`d859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158`
- 默认工具链位置：`work/zig-bootstrap/zig-windows-x86_64-0.13.0/zig.exe`，也可给构建脚本传 `-Zig`。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/zig-scheduler/Build.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/fsa-integration/Build-Integration.ps1 -FsaOnly
python tools/zig-scheduler/Test-Scheduler.py
```

输出：`build/zig-scheduler/nsp-scheduler.exe`。已有原生 DLL 及其依赖仍按当前目录布局保留；本阶段没有重新打包或重新构建音频核心。

## 现场切换

默认路径现已统一为 Zig；集成构建会迁移 FSA 的调度配置并保留规则/快照，但不会重启现场进程。不要为了验证而自动重启现场后端；已有 Python 管理服务必须在明确授权的停机窗口正常退出后再接管。

在用户明确选择的停机窗口，先通过原入口正常停止 NSp 并确认指令与端点恢复，然后退出旧控制后端。FSA 退出也要确认完成，再修改正式构建目录上一级的 `final-spatial-audio.ini`：

```ini
[NSP]
Scheduler=Zig
ControlExecutable=C:\path\to\NativeSpatialPipe\build\zig-scheduler\nsp-scheduler.exe
Python=<现有遥测 Python 的绝对路径>
```

重新启动正式 FSA 后，菜单仍然使用原接口。`ControlScript` 可保留供显式 `Scheduler=Python` 对照，但默认 Zig 不执行它。原 Python 管理后端还活着时，Zig 会拒绝接管，不会主动终止它。游戏托管脚本和单独一分钟测试也默认 Zig，Python 对照须显式选择；标准单流研究脚本保留其历史测量边界。

也可只启动独立、无音频的控制服务用于查看界面（不会启用 Hook）：

```powershell
build/zig-scheduler/nsp-scheduler.exe backend-start --home build/zig-trial-state --port 18765 --telemetry-port 18766 --python <python.exe绝对路径>
build/zig-scheduler/nsp-scheduler.exe request --home build/zig-trial-state --action status
build/zig-scheduler/nsp-scheduler.exe request --home build/zig-trial-state --action exit
```

`--home` 应使用绝对路径或从同一工作目录执行；FSA 正式接入使用原 `%LOCALAPPDATA%/NativeSpatialPipe` 状态目录。独立 UI 预览的端口不自动改写前端已保存的遥测地址。

## 验证范围和限制

DEV16 新播放路径的渲染选择分两层：`--renderer roar --renderer-mode olr`，或 `--renderer lira --lira-renderer-dll <绝对路径>`。默认 ROAR／OLR；旧 `--renderer olr` 仅为兼容简写。JSON 播放配置对应 `renderer: "roar", mode: "olr"` 或 `renderer: "lira", mode: null`，报告分别记录 `renderer` 和 `rendererMode`。本轮不接入 ROAR 其他模式；`--asio-channel-order olr` 仅指定既有声道排列。职责及历史范围见[边界更正](../../docs/core/renderer-backend-boundary-20261021.md)。

隔离测试覆盖本地 HTTP、鉴权、单实例、FSA 原生入口与关联磁盘状态、配置保存、故障恢复证据，以及普通/FullTiming 两种自有 fixture 音频流程。每轮 250 包、128 动态对象加 12 静态声道，输出 12 声道，确认 Hook 恢复、捕获与传输库释放、接收和渲染排空、快照记录。

详细结果生成在 `build/zig-scheduler/fixture-validation.json` 和 `full-timing-validation.json`。fixture 不是游戏或设备播放验收，不代表完成真实游戏延迟、长时间稳定性或 CPU 对照测试。原生管理程序的 PE 导入检查也应与现有 DLL 依赖分开理解。

两种模式还分别对照原 Python 控制接口提交的相同输入，前 8 个渲染块的实际 PCM 字节完全一致。原有主管线队列容量 64、其余管线容量 8、MMCSS 设置及遥测 1 MiB UDP 发送缓冲均保留。

`Benchmark-Control.py` 仅比较独立空闲目录中的 FSA 状态查询：本机最终 20 次采样中位耗时约 Python 98.9 ms、Zig 23.1 ms。它包含程序启动与磁盘状态写入，不是核心音频或遥测计算性能结论。摘要位于 `docs/scheduler.md`。

原生 Rust 的处理块绑定回收问题未在此次迁移中修改。旧 hookGeneration=1 的故障恢复需先由原控制入口完成；新托管会话使用 generation=2，Zig 对不支持的旧恢复状态返回未知并保持锁定。

Python 现在仅在默认托管路径中承载遥测服务，实验/测试脚本仍可以使用 Python。Zig 已是默认调度；真实会话切换需在用户授权的停机时机执行。

## 一分钟实测与存储排期（2026-10-19）

已完成专用 Wwise 工程 Python/Zig 各一分钟对照，每轮 6,000 次位置更新，128 动态对象 + 12 bed 声道，捕获/接收/渲染检查及 Hook/端点恢复通过。宿主加遥测整机 CPU 为 3.47%→2.92%，该组顺序对照下降约 15.8%；宿主包含原生 DSP。渲染 work P99 为 1.447→1.435 ms，不是设备端到端延迟。此结果不替代长时、异常恢复或跨机器验收。

开发阶段继续完整录音及约 10 Hz 的新快照记录，支持审查和回溯。12 通道 Float32/48 kHz PCM 约 8.29 GB/h，加快照已观察约 10–12 GB/h；默认用户状态目录的 `sessions/` 也承载这些大体积数据。D15 落盘治理待音频接口实际出声后实施，当前不自动关闭记录或清理。正式部署前应理解这一存储行为；完整记录仍是当前遥测文件增长选流的依赖。

DEV16 合并功能的统一 JSON 字段、默认值、声道顺序与只读校验入口见 [播放配置说明](../../docs/core/playback-json-configuration-20261022.md)。新增 DSP、半径、自定义路由和既有输出策略字段；使用同批构建的客户端及 host，现有运行实例未替换。
## Web configuration host (2026-10-23)

`nsp-scheduler.exe web-host --fsa-config <absolute FSA INI> --owner-pid <FSA PID>` runs the separate loopback-only configuration/status server on `http://127.0.0.1:8767/`. FSA starts it without opening a browser; triple-click the tray icon to open the existing frontend. The new configuration page edits the existing playback and backend JSON contracts, validates and atomically saves with a revision check. Saving never starts/restarts a session or opens an audio device. Playback changes apply on the next session; backend defaults require backend restart. Runtime control is intentionally not exposed. See [the implemented contract](../../docs/decisions/web-configuration-host-20261023.md).
