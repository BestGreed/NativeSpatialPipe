# Sonic NSp / FSA 集成候选版

2026-10-24 playback location: `%LOCALAPPDATA%/NativeSpatialPipe/playback.json` is now the default. The old installation-adjacent default is copied without replacing existing user data; explicit custom paths remain supported. See [migration](../../docs/core/playback-user-location-20261024.md).

## 当前进度（2026-10-24）

已接通 HT 手动准备、端点能力与 PCM 快照职责拆分、正常停止/急停反馈、ASIO 会话常驻和独立 Web API Host。原生 FSA 负责模式、Hook、端点互验及启停；Web 仅查状态、校验/保存配置。托盘连续三击打开 `http://127.0.0.1:8767/`。

HT Support 为手动 EDID 入口；普通启动/枚举不自动注入，缺少能力的 HT 模式按可用性隐藏。过滤和 HT 勾选状态由 FSA 枚举负责，符合条件且缺能力时才调用准备模块，写入后重载/回验；安装入口尚未实现。见EDID 菜单（开发参考未随公开源码提供）。

播放 JSON 已支持七布局、DSP、接线和两项 `recording` 开关。当前 Zig host 的 PCM/快照默认均不落盘；未填 `PlaybackConfig` 时不启用 ASIO，只运行捕获/遥测。旧 Python 对照和离线工具的录制合同单独保留。详见[播放配置](../../docs/core/playback-json-configuration-20261022.md)和[旁录隔离](../../docs/core/recording-isolation-20261024.md)。下面标日期段落为历史交付说明，不能覆盖本节。

2026-10-18。基于本机 `Final_Spatial_Audio-published` 的 `decba9b`，来源为
[BestGreed/Final_Spatial_Audio](https://github.com/BestGreed/Final_Spatial_Audio)。
`native/` 是该提交的源码集成副本，原许可证见 `LICENSE`；未修改原仓库或
`<existing FSA installation>` 已安装文件。上游原始构建说明仍保留，本页描述本次新增行为。

## 构建与运行

DEV16 N12增加可选 `[NSP] PlaybackConfig=<绝对JSON路径>`，使用与桌面相同的单会话播放合同。`renderer=roar, mode=olr` 或 `renderer=lira, mode=null`，并显式指定渲染桥、ASIO模块、驱动等；留空时仅捕获/遥测，录制仍默认关闭。`Configure-Scheduler.ps1 -PlaybackConfig <路径> -ControlExecutable <开发host>` 可写入指定INI；未提供PlaybackConfig参数时保留既有值，显式空值清除。只在启动时传递，停止/急停不依赖该文件。此能力要求Zig，不能静默转给旧Python调度。N12构建与测试使用隔离目录，执行本页旧的正式构建/配置命令仍应按部署时机安排。详见N12范围、证据及剩余实机步骤（开发参考未随公开源码提供）。

2026-10-19：本目录为 NSp 专用集成源码，尚未回传 FSA 独立仓库主线。默认 `[NSP] Scheduler=Zig`，直接调用 Zig 调度程序，`Python` 仅传给遥测；`ControlExecutable` 留空时按正式构建布局解析，缺少 Zig 不隐式回退。集成构建会更新调度配置并保留规则、快照，但不会重启服务；旧路径须显式 `Scheduler=Python` 或构建参数 `-LegacyPythonScheduler`。部署、启动顺序与验证见[Zig 调度说明](../zig-scheduler/README.md)。

集成版 EXE 现嵌入 `requireAdministrator` 启动清单，双击时由 Windows 请求管理员授权；已经在管理员上下文中启动时无需再次提权。测试用 EXE 不携带这份提权清单。

仅更新 FSA 可给构建命令增加 `-FsaOnly`，不会重建或覆盖正在使用的 Hook／遥测 DLL。构建直接生成并检查正式的 `build/fsa-integration/bin/FinalSpatialAudio.exe`。如果 EXE 被占用则构建失败，不关闭进程，也不生成 pending 副本。

在 NSp 仓库根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/fsa-integration/Build-Integration.ps1
```

产物是 `build/fsa-integration/bin/FinalSpatialAudio.exe`。构建脚本准备独立运行目录，
第一次复制已安装 FSA 的规则和快照；每次构建以 `Configure-Scheduler.ps1` 写入默认 Zig 选择及 EXE、遥测 Python 和旧对照脚本路径。
以后构建保留其他规则和快照。完整构建先编译 Zig，`-FsaOnly` 要求已有 Zig 产物。音频 DLL、Python、工具链仍使用本机 NSp 仓库，
这不是可脱离仓库安装的发布包。FSA 使用原有单实例互斥量，安装版运行时候选版不会另开实例。

运行请使用 `tools/fsa-integration/Start Sonic NSp.cmd`，由 Windows UAC 提权并直接启动 Sonic NSp。也可右键以管理员身份运行候选 EXE 后选择 Sonic NSp。
备用命令为 `Start-SonicNSp.ps1 -Action Start|Stop|EmergencyStop`。

菜单显示 **Sonic NSp**，状态图标使用 **FSA 应用自己的图标**。
初始化期间使用旋转的立体声图标；仅在 Sonic、端点及 Hook 验证成功后切换为 FSA 图标。动画帧按当前 DPI 预生成，成功、失败或停止后关闭动画计时器，常驻就绪状态不持续刷新图标。
选中后暂停普通规则，先预检目标访问权限、保护级别、构建和依赖，再恢复并核验 Sonic，保存到独立的 Sonic 快照槽，显式启动 NSp。预检不通过时不切换音频。
运行期间其他受支持音频模式保持可选：点击后先停止 NSp，核实 Hook 禁用、原指令恢复及后端收尾完成，再应用所选模式。停止失败或状态未知时保留锁定。自动规则切换及意图捕获仍暂停；停止和急停保持可用。

## 持久化与状态

本机用户状态位于 `%LOCALAPPDATA%\NativeSpatialPipe`：

| 文件 | 作用 |
|---|---|
| `config.json` | 原子保存音频配置；重启恢复设置，不自动启动音频 |
| `session.json` | 会话 ID、输出目录、FSA 所有者和端点、状态与错误 |
| `owner.lock` | 控制服务进程独占锁；不同端口也不能重复管理同一用户状态 |
| `runtime.json` | 当前后端进程、地址及本地命令令牌；正常退出删除 |
| `hook-status.json` / `.ini` | 实际查询结果；INI 带请求关联 ID 与时间，由 FSA 磁盘模块回读 |
| `sessions/<id>/observed/hook-session.json` | Hook 目标 PID 与进程创建时间，用于防止 PID 复用误操作 |

配置和状态分开：`idle → starting → running → stopping → stopped`；
异常进入 `faulted`，控制服务重启发现未结束会话进入 `recovery-required`。
重连页面仅恢复遥测订阅，不启动 Hook。没有数据可处于等待/过期，不能据此反复挂钩。

FSA 只有在 **Sonic 选择、端点身份及 Hook 工作状态都通过回读** 时显示 Sonic NSp。
停止确认需要 Hook 禁用且原指令恢复，或原目标进程实例已退出；后者明确记录为
`targetExited`，不伪造指令恢复读数。下游收尾和端点恢复尚未完成时继续锁定。
新会话不会继承上次的运行状态。故障不自动重挂；用户显式停止且恢复报告完整后才能解锁。

## 常驻、退出与急停

FSA 管理的会话使用 `duration=0`，取消固定 20 分钟截止和累计捕获字节预算；
原定时实验仍可使用有限时长。音频侧队列仍有界。常驻并不表示已通过长时间性能验收。

Hook 的保护线程持有 FSA 和 `Sync-Hook.py` 进程句柄，任一退出即触发禁用和排空。
窗口/浏览器退出不停止音频。显式退出 FSA 先请求音频停止、等待报告，必要时急停；
FSA 进程最终退出仍有句柄保护。控制服务可常驻，音频会话不因服务重启自动恢复。

“停止 NSp”通过持久停止文件通知原编排器，按生产者停止、下游排空、端点恢复顺序收尾。
“急停 NSp”从界面独立派发适配器，绕过正在等待的仲裁线程；先通知原生 Hook 停止，
再查询禁用/原指令恢复，停止文件让编排器继续完成下游收尾。停止超时保留故障，
不会杀死 `audiodg.exe` 或强制销毁仍在使用的接收器。

命令行备用入口：

```powershell
python tools/backend/fsa_control.py status
python tools/backend/fsa_control.py stop
python tools/backend/fsa_control.py emergency-stop
python tools/backend/backend.py status
python tools/backend/backend.py exit
```

`exit` 如正在停止音频，HTTP 返回 409 表示退出已请求；音频确认停止后控制服务自行退出。
启动端点和 FSA PID 由 FSA 显式传入，不使用历史实验端点作为 FSA 路径的默认值。

## 验证与边界

见 [集成验收记录](../../docs/scheduler.md)。受控验收及真实 Sonic 切换、捕获、正常停止、急停和重新启动均已通过。仍未替换安装版，长期性能与跨系统兼容验收另计。

新 Hook 单独输出到 `build/native-hook-managed-v2/nsp_managed_hook_v2.dll`，避免覆盖已驻留的旧构建。
目标内已有旧 `nsp_native_hook.dll` 时，必须经原控制器核实停止、原指令恢复与资源释放，才允许复用；旧 Hook 活动或无法核验时拒绝混用。目标中不同构建也拒绝按错误导出偏移调用。
遇到此情况保持未知/故障状态，不能通过删除状态文件假装完成停止。

DEV16 的 `[NSP] PlaybackConfig` 文件可统一设置公共 DSP、ROAR/OLR 半径、物理路由与既有 ASIO 输出策略。修改后在下一次启动生效，端点互验继续归 FSA。详见 [播放 JSON 配置及示例](../../docs/core/playback-json-configuration-20261022.md)。旧 host 不识别新增字段，应使用对应的新构建；本轮未替换现有 FSA 实例。
## Web 配置入口（2026-10-23）

FSA 启动时创建独立的 Zig Web API Host，地址 `http://127.0.0.1:8767/`，不自动弹浏览器。托盘连续三击左键用默认浏览器打开原可视化首页；首页新增“配置”入口。单击/双击待多击窗口结束后显示原菜单，右键立即显示菜单。Web 仅查询状态、校验并保存现有 JSON，不提供启停、Hook、EDID 或其他控制。管理员 FSA 使用普通用户令牌启动 HTTP Host；服务失败不影响 FSA。详见 [实现边界与使用说明](../../docs/decisions/web-configuration-host-20261023.md)。
