# NSp Zig 调度层

迁移对象是原 Python 管理与编排层，现有 C Hook、C 捕获/共享内存和 Rust 接收/渲染 DLL 均保持不变。遥测仍按原顺序先启动，计算与 HTTP/WebSocket 服务继续使用原 Python 实现。本阶段没有把遥测从启动链中拆开。

## 已迁移

- `backend.zig`：常驻后端、单实例、配置与会话持久化、HTTP 控制及前端文件服务、子进程监控、停止超时和恢复锁定。
- `control.zig`：FSA 的 preflight/start/status/stop/emergency-stop、目标实例检查、关联状态文件与新鲜度协议。
- `native.zig`：直接调用现有管线与接收端 C ABI，调用原生 Hook 控制器；不经过 ctypes/Python 编排。
- `host.zig`：四条原生管线预热、遥测进程管理、原生接收端启动、Hook 生命周期、非实时遥测转发、快照记录及结束排空。
- `routing.zig`：迁移现有遥测文件长度选流策略，轮询间隔 100 ms、失效时间 1 s、新候选两次增长后抢占。
- FSA 支持 `[NSP] ControlExecutable`。配置此项时直接调用 Zig EXE，`Python` 配置仅传给遥测服务；未配置时保留原 Python 路径，便于对照和回退。

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

本次实现和测试没有修改用户正在运行的会话、FSA 配置或 Hook DLL。不要为了验证而自动重启现场后端。

在用户明确选择的停机窗口，先通过原入口正常停止 NSp 并确认指令与端点恢复，然后退出旧控制后端。FSA 退出也要确认完成，再修改正式构建目录上一级的 `final-spatial-audio.ini`：

```ini
[NSP]
ControlExecutable=C:\path\to\NativeSpatialPipe\build\zig-scheduler\nsp-scheduler.exe
Python=<现有遥测 Python 的绝对路径>
```

重新启动正式 FSA 后，菜单仍然使用原接口。`ControlScript` 可以保留作回退，但配置 `ControlExecutable` 时不执行它。原 Python 管理后端还活着时，Zig 会拒绝接管，不会主动终止它。

也可只启动独立、无音频的控制服务用于查看界面（不会启用 Hook）：

```powershell
build/zig-scheduler/nsp-scheduler.exe backend-start --home build/zig-trial-state --port 18765 --telemetry-port 18766 --python <python.exe绝对路径>
build/zig-scheduler/nsp-scheduler.exe request --home build/zig-trial-state --action status
build/zig-scheduler/nsp-scheduler.exe request --home build/zig-trial-state --action exit
```

`--home` 应使用绝对路径或从同一工作目录执行；FSA 正式接入使用原 `%LOCALAPPDATA%/NativeSpatialPipe` 状态目录。独立 UI 预览的端口不自动改写前端已保存的遥测地址。

## 验证范围和限制

隔离测试覆盖本地 HTTP、鉴权、单实例、FSA 原生入口与关联磁盘状态、配置保存、故障恢复证据，以及普通/FullTiming 两种自有 fixture 音频流程。每轮 250 包、128 动态对象加 12 静态声道，输出 12 声道，确认 Hook 恢复、捕获与传输库释放、接收和渲染排空、快照记录。

详细结果生成在 `build/zig-scheduler/fixture-validation.json` 和 `full-timing-validation.json`。fixture 不是游戏或设备播放验收，不代表完成真实游戏延迟、长时间稳定性或 CPU 对照测试。原生管理程序的 PE 导入检查也应与现有 DLL 依赖分开理解。

两种模式还分别对照原 Python 控制接口提交的相同输入，前 8 个渲染块的实际 PCM 字节完全一致。原有主管线队列容量 64、其余管线容量 8、MMCSS 设置及遥测 1 MiB UDP 发送缓冲均保留。

`Benchmark-Control.py` 仅比较独立空闲目录中的 FSA 状态查询：本机最终 20 次采样中位耗时约 Python 98.9 ms、Zig 23.1 ms。它包含程序启动与磁盘状态写入，不是核心音频或遥测计算性能结论。摘要位于 `docs/scheduler.md`。

原生 Rust 的处理块绑定回收问题未在此次迁移中修改。旧 hookGeneration=1 的故障恢复需先由原控制入口完成；新托管会话使用 generation=2，Zig 对不支持的旧恢复状态返回未知并保持锁定。

Python 现在仅在此新正式路径中承载遥测服务，实验/测试脚本仍可以使用 Python。新实现是可对照验证的候选路径；真实会话切换需在用户授权的停机时机执行。

## 一分钟实测与存储排期（2026-10-19）

已完成专用 Wwise 工程 Python/Zig 各一分钟对照，每轮 6,000 次位置更新，128 动态对象 + 12 bed 声道，捕获/接收/渲染检查及 Hook/端点恢复通过。宿主加遥测整机 CPU 为 3.47%→2.92%，该组顺序对照下降约 15.8%；宿主包含原生 DSP。渲染 work P99 为 1.447→1.435 ms，不是设备端到端延迟。此结果不替代长时、异常恢复或跨机器验收。

开发阶段继续完整录音及约 10 Hz 的新快照记录，支持审查和回溯。12 通道 Float32/48 kHz PCM 约 8.29 GB/h，加快照已观察约 10–12 GB/h；默认用户状态目录的 `sessions/` 也承载这些大体积数据。D15 落盘治理待音频接口实际出声后实施，当前不自动关闭记录或清理。正式部署前应理解这一存储行为；完整记录仍是当前遥测文件增长选流的依赖。
