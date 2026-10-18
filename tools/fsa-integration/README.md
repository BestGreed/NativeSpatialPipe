# Sonic NSp / FSA 集成候选版

2026-10-18。基于本机 `Final_Spatial_Audio-published` 的 `decba9b`，来源为
[BestGreed/Final_Spatial_Audio](https://github.com/BestGreed/Final_Spatial_Audio)。
`native/` 是该提交的源码集成副本，原许可证见 `LICENSE`；未修改原仓库或
`<existing FSA installation>` 已安装文件。上游原始构建说明仍保留，本页描述本次新增行为。

## 构建与运行

集成版 EXE 现嵌入 `requireAdministrator` 启动清单，双击时由 Windows 请求管理员授权；已经在管理员上下文中启动时无需再次提权。测试用 EXE 不携带这份提权清单。

仅更新 FSA 可给构建命令增加 `-FsaOnly`，不会重建或覆盖正在使用的 Hook／遥测 DLL。构建直接生成并检查正式的 `build/fsa-integration/bin/FinalSpatialAudio.exe`。如果 EXE 被占用则构建失败，不关闭进程，也不生成 pending 副本。

在 NSp 仓库根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/fsa-integration/Build-Integration.ps1
```

产物是 `build/fsa-integration/bin/FinalSpatialAudio.exe`。构建脚本准备独立运行目录，
第一次复制已安装 FSA 的规则和快照，写入 `[NSP]` 的 Python 与 ControlScript 绝对路径。
以后构建保留候选版的配置。音频 DLL、Python、工具链仍使用本机 NSp 仓库，
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

见 [集成验收记录](../../docs/fsa-nsp-integration-20261018.md)。受控验收及真实 Sonic 切换、捕获、正常停止、急停和重新启动均已通过。仍未替换安装版，长期性能与跨系统兼容验收另计。

新 Hook 单独输出到 `build/native-hook-managed-v2/nsp_managed_hook_v2.dll`，避免覆盖已驻留的旧构建。
目标内已有旧 `nsp_native_hook.dll` 时，必须经原控制器核实停止、原指令恢复与资源释放，才允许复用；旧 Hook 活动或无法核验时拒绝混用。目标中不同构建也拒绝按错误导出偏移调用。
遇到此情况保持未知/故障状态，不能通过删除状态文件假装完成停止。
