# NSp 前置空间管线初始化

入口为 `pipeline.py`，供 FSA、NSp 启动脚本和独立命令行调用。所有命令输出 JSON，失败返回非零。当前为本仓库集成模块，需要现有 Windows Python、`work/OemEndpointProbe.exe` 和 `work/oem-hook-deps/work/frida/package`。枚举 hook 和 EDID 写入需要管理员权限；模块不自行弹 UAC。

## FSA 接入

运行 `tools/fsa-integration/Build-Integration.ps1 -FsaOnly` 生成 `build/fsa-integration/bin/FinalSpatialAudio.exe`。配置脚本填写 `[NSP]PipelineInitScript`，默认前置模式仍为 Sonic。

FSA 主菜单提供七种空间模式；点击会实际切换并读回验证，同时更新 NSp 的前置格式。耳机／扬声器的四个扩展项只在对应 Enum 开启且实际可用时显示；关闭枚举或失去可用状态后直接移除。NSp 运行时选择其他模式，会先等待捕获停止并验证恢复，再执行切换。**高级 → NS Extreme** 沿用 FSA 自绘侧展开菜单的材质、字体、DPI、定位和交互：

| 子项 | 含义 | 配置字段 |
| --- | --- | --- |
| Expand Online | 退出 NSp/FSA 后继续维持枚举常驻 | PersistEnumerationHook |
| Enum OLR | 耳机模式枚举 | HookHeadphones |
| Enum OBR | 扬声器模式枚举 | HookSpeakers |
| Rec Hook | 关闭自动常驻并撤销枚举 hook | 停用 PersistEnumerationHook 并 recover |

### NSp Lock

**高级 → NSp Lock** 在工作线程立即读取当前实际启用的空间格式，保存 `LockedFrontend`、`LockEndpoint`、`LockFormatGuid`、`LockHookMask`，再提交 `NSpLock=1`。空间音频关闭、格式未知或首选端点不匹配时拒绝锁定，不从旧的 FrontendFormat 猜测。

依赖枚举的耳机格式补齐 Expand Online 和 Enum OLR，扬声器格式补齐 Expand Online 和 Enum OBR；Sonic 和家庭影院没有新增枚举需求。若对应枚举未开启、没有活跃 hook，服务原生声明仍支持当前格式，则记录零新增 hook 需求，不为原生已可用的 OEM／已许可模式额外注入。保留用户已经开启的其他组；健康且范围匹配的持久常驻直接复用。锁定期间这三个依赖开关由快照维持，Rec Hook 仍可恢复；再次启动会重申所需依赖。取消 NSp Lock 解除路由约束，保留当前会话和枚举设置，避免撤销仍在使用的依赖。

主菜单的七种独立空间模式合并为唯一 NSp 入口：

| 前置格式 | 锁定后的入口 |
| --- | --- |
| Windows Sonic | Sonic NSp |
| Dolby 耳机 | Dolby Atmos B NSp |
| Dolby 扬声器 | Dolby Atmos L NSp |
| Dolby 家庭影院 | Dolby Atmos HT NSp |
| DTS 耳机 | DTS:X B NSp |
| DTS:X Ultra | DTS:X L NSp |
| DTS 家庭影院 | DTS:X HT NSp |

勾选先锁定；点击唯一入口或自动规则命中空间模式时启动 NSp。锁定下所有空间格式规则（包括原来不可用的空间格式目标）均路由至快照格式 + NSp，优先级仍由原规则解析器决定。Stereo/5.1/7.1 不重定向，命中时先核验捕获停止，再应用 PCM；之后再次命中空间规则可自动回到同一前置管线。自动模式在锁定的 NSp 运行期间仍可使用。明确手动停止会暂停自动规则，防止立即自动重启。

锁定配置可跨 FSA 重启保留，但不自动启动捕获。硬件端点变化时保留快照并禁用该 NSp 入口，需解锁后在新端点重新锁定。锁定本身不替代实际 active GUID、hook 心跳、端点身份和恢复检查。

NSp 首次完整验证成功后，对应前置格式的托盘图标以缓存帧旋转一圈（约 720ms），结束后停止计时器；后续状态轮询不重复播放。遵循 Windows 客户端动画开关，失败或仅收到启动请求时不播放成功提示。NS Extreme 的展开箭头在所有菜单层级均绘制。

自绘菜单中的开关操作保持菜单展开，勾选立即更新。移除的模式在约 160 毫秒内横向滑出、淡出并收拢行高；收拢期间这些行已经不能点击或被键盘选中。移除期间重新启用会取消移除，关闭菜单会清理动画计时器。关闭 Windows 菜单／客户端动画时立即移除；高对比度和屏幕阅读器仍走系统菜单，无自绘动画。

例如：

```ini
[NSP]
FrontendFormat=DTSXUltra
PersistEnumerationHook=1
HookHeadphones=1
HookSpeakers=1
```

七种 `FrontendFormat`：`WindowsSonic`、`DolbyAtmosForHeadphones`、`DolbyAtmosForSpeakers`、`DolbyAtmosForHomeTheater`、`DTSHeadphoneX`、`DTSXUltra`、`DTSXForHomeTheater`。

FSA 先检查捕获启动条件，再准备前置模式，然后启动 NSp。格式参数贯穿原生 Zig 调度器、兼容 Python 调度器和 `Run-GameTelemetry.ps1`。设置发生变化时重建对应枚举 hook；相同设置的 NSp 再次启动复用常驻，不重复卸载。勾选分组后保留初始化启动入口；这只允许发起初始化，实际支持验证绕行仍必须等到匹配的内存存活标记出现。

## 独立参数调用

在仓库根目录的 PowerShell 中，`$endpoint` 使用 MMDevice 的传统 ID，例如 `{0.0.0.00000000}.{...}`：

```powershell
$endpoint = '{0.0.0.00000000}.{7209e4d4-431d-479b-bdba-8dc78c174b60}'
python tools/pipeline-init/pipeline.py inspect --endpoint $endpoint
python tools/pipeline-init/pipeline.py ensure --endpoint $endpoint --persist --groups headphones speakers
python tools/pipeline-init/pipeline.py prepare --endpoint $endpoint --format DTSXUltra --persist --groups headphones speakers
python tools/pipeline-init/pipeline.py status
python tools/pipeline-init/pipeline.py verify-hook --endpoint $endpoint --format DTSXUltra
python tools/pipeline-init/pipeline.py recover
```

`ensure` 只维持枚举，不切模式；`prepare` 切换并核对实际 active/default GUID。`headphones` 对应 Dolby 耳机和 DTS Headphone:X；`speakers` 对应 Dolby 扬声器和 DTS:X Ultra。家庭影院通过原生声明／EDID 路径使用，不包含在这两个 hook 分组中。

目标必须是当前首选 multimedia 渲染端点。常驻与 FSA/NSp 分离，调用者退出后继续运行。首选端点改变时旧常驻恢复并退出；FSA 更新端点后可按用户偏好重新建立。FSA 每八秒检查存活，AudioSrv 实例变化时常驻重新核对身份和版本后尝试附着。它不重启音频服务，不管理 NSp 捕获 hook。此处“持久化”指进程生命周期独立；没有安装开机自启任务。

非持久测试可直接调用：

```powershell
python tools/pipeline-init/manager.py start --endpoint $endpoint --groups headphones --owner-pid $PID
```

这种常驻随指定 owner 进程退出而恢复。`prepare` 也接受 `--owner-pid`；非持久化且指定 hook 分组时必须提供。FSA 非持久配置以自身为 owner，并在 NSp 停止确认后主动恢复。同一端点、分组和持久化设置的重复启动是幂等操作；不同配置要求先恢复，FSA 菜单已经处理该步骤。

## EDID 初始化与恢复

产品触发边界以 2026-10-23 决策（开发参考未随公开源码提供）为准：EDID 写入仅限后续安装流程及显式手动入口，常驻检查／`prepare` 不自动写入；成功覆盖不会随 FSA／NSP 退出而撤回。

FSA 已接入 NS Extreme → HT Support 手动入口（开发参考未随公开源码提供）。本地过滤与已有 HT 枚举控制菜单状态，不为状态查询创建外部进程。只有用户点击适用且尚无 HT 能力的端点才执行 `pipeline.py edid-enable --endpoint <id> --output <新目录>`：通过 ContainerId 绑定唯一活动显示器，核对显卡父设备，写入后重载该显卡并用新进程回验。此命令会实际写入和重载，不用于只读诊断；失败结果和恢复事务保留在输出目录。

2026-10-23 回归进展见 OG27QH EDID 准备回归（开发参考未随公开源码提供）。新增事务与驱动重载编排测试，实际写入读回、驱动重载、新进程能力与 active/default 查询分别验收；已有声明的设备不能充当“从不支持到支持”的正负对照。

首次使用先调用 `inspect`。它返回 GPU 音频端点判断、两个家庭影院模式的支持查询、`recommendEdidInitialization` 和活动显示器候选。FSA 或其他前端可据此提示初始化。声音端点和显示器必须由调用者明确配对，不能只靠“第几个设备”推断。

```powershell
python tools/pipeline-init/pipeline.py edid-plan --endpoint $endpoint --monitor-instance 'HKC27A7\5&26f91d2b&0&UID4357' --profile both --output experiments/my-edid-plan
python tools/pipeline-init/pipeline.py edid-apply --plan experiments/my-edid-plan/edid-plan.json
python tools/pipeline-init/pipeline.py edid-reload --plan experiments/my-edid-plan/edid-plan.json --adapter-instance 'PCI\实际显卡实例ID'
python tools/pipeline-init/pipeline.py query --endpoint $endpoint --format DTSXForHomeTheater
python tools/pipeline-init/pipeline.py edid-restore --plan experiments/my-edid-plan/edid-plan.json
```

`--profile dtsx` 补充 LPCM、DTS、DTS-HD；`atmos` 补充 LPCM 和带对象能力标志的 MAT；`both` 包含两者。保留已有 CRU 元数据、非音频块和显示时序。计划保存前后 EDID、全部已有覆盖值以及目标块原值；写入前记录事务意图，并核对设备 EDID 哈希、当前块和写后读回。空间不足、布局未知、校验和错误或别人修改了该块时停止。

应用只写指定 CTA 覆盖块。显示驱动重载是独立显式操作，可能短暂黑屏；恢复 EDID 后同样需要重载才能让驱动重新枚举。`edid-restore` 恢复本事务触及的块，不删除其他 CRU 设置。`recover` 仅恢复运行中的枚举 hook，不撤销已应用 EDID。EDID 声明和 active 成功均不证明物理设备能够解码或播放。

## 内存存活契约

共享映射为 `Local\NSp-PipelineInit-v1`，2048 字节，布局对应 `model.py` 和 FSA 的 `native/pipeline_init.h`：

| 偏移 | 类型 | 内容 |
| --- | --- | --- |
| 0 | uint32 | seqlock 序号，奇数表示写入中 |
| 4 | uint32 | magic `0x4950534e` |
| 8 | uint32 | 版本 1 |
| 12 | uint32 | 状态：0 停止、1 启动／重试、2 就绪、3 撤销中 |
| 16 / 20 | uint32 × 2 | 常驻 PID；flags bit0 在线、bit1 持久化 |
| 24 / 32 | uint64 × 2 | GetTickCount64 心跳；进程创建 FILETIME |
| 40 / 44 | uint32 × 2 | AudioSrv PID；七种格式的位掩码 |
| 48 | WCHAR[512] | 绑定的传统 endpoint ID |

FSA 必须同时核对一致序号、版本、就绪状态、心跳不超过三秒、PID 存活及创建时间、端点和所选格式位。INI 勾选项或磁盘中的 `ready` 字样不能授权绕行。只有当前格式对应的活跃标记，才能放宽该格式的支持预检；实际设置结果、active GUID、端点身份和捕获恢复检查仍须通过。

## 恢复和版本边界

hook 目前严格绑定 AudioSrv.dll SHA-256 `487e51035a76323032acd5996511c221c449cd244051d3b0e057eec70538ca97`（本机 10.0.26100.8875）。Windows 更新后若哈希不符会拒绝附着，需要重新分析地址。仅修改指定端点、指定格式的许可查询结果，不修改服务文件或内核驱动。

常驻续租间隔约半秒；代理在五秒未续租后自行撤销。`recover` 请求退出并逐项比对被拦截函数的原始字节。宿主异常退出时，恢复命令等待租约到期，再只读核查同一 AudioSrv 进程实例；不会向新 PID 写入旧内存快照。无法确认恢复时返回失败，保留 `%LOCALAPPDATA%/NativeSpatialPipe/pipeline-init/resident.json` 证据。

## 验证记录

`experiments/pipeline-init-20261019` 保存本机验证：同一常驻 PID 下四种耳机／扬声器模式均设置成功，调用者退出后仍在线，FSA 原生 C 读取器确认存活；首选端点变化后常驻退出，三处函数原始字节恢复核对通过。`owner-exit-fixed/result.json` 验证了非持久 owner 退出，即使测试端仍保留进程句柄也能正确撤销。测试结束后恢复原始模式和默认端点，没有留下运行中的枚举 hook。

`fsa-seven-modes-fixed/result.json` 记录 FSA 自身七个切换入口的实机验证：七项 active/default GUID 均匹配，同一常驻 PID 完成，之后恢复原音频状态和配置。修复了要求 AudioSrv SYNCHRONIZE 权限导致部分上下文无法检查服务身份的问题；现在只用 PROCESS_QUERY_LIMITED_INFORMATION 查询创建时间和退出码，仍能识别被保留句柄的已退出 owner。

通用 EDID 计划已在现有 OG27QH 覆盖上执行只读检查，前后结果相同；没有为这次模块验证再次重载显卡。单元测试覆盖 EDID 保留／分组／校验和／拒绝条件及陈旧、错端点、错进程和撕裂内存标签。还通过调度器回归和 FSA 构建检查。弹出菜单测试覆盖七个模式和四个子项命令、键盘操作及子菜单几何；输入桌面不可用时会明确跳过鼠标轨迹用例。尚未做长时间驻留、AudioSrv 强制崩溃和七种前置模式各自的完整 NSp 音频质量验证。
