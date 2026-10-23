# 当前架构与技术栈边界

2026-10-24。本页描述当前产品运行入口；历史设计、诊断工具和将来规划均不能替换本页的运行路径。核对细项见[技术栈与文档复审](stack-architecture-audit-20261024.md)。

## 音频数据路径

```text
Windows 对象提交 / audiodg
 → 原生 MinHook / C 快照、捕获 worker
 → C 有界共享内存与事件
 → Rust 接收、公共输入转换、预热 renderer 实例
 → ROAR（当前 OLR 模式）或 C++ LIRA
 → Rust interleaved 输出池
 → Zig DSP 回调在池块内原地处理 / bypass
 → 有界池块租借描述符
 → ASIO owner 同步提交池地址
 → C++ ASIO 内部队列、SRC、设备时钟、ring、物理声道转换
```

已移除的是 Rust 池与 ASIO 提交之间的 Zig PCM staging 队列及 scratch 复制；仍有有界描述符队列，也保留 ASIO 内部复制。不能称为全链路零复制或硬件回调直接拉取 Rust 池。公共 DSP 算法仍在 Zig，通过进程内 C ABI 回调执行，不是新增一份 Rust DSP。

PCM 录音位于公共 DSP 前，显式开启时复制到独立有界录音池，由专用线程写盘；队列满或磁盘故障不等待、不挟持播放池。对象/输出遥测使用独立 tap 和有界旁路，由独立 Python 服务计量、聚合并提供 HTTP/WebSocket。旁路仍有复制及发布成本，不等于完全无实时开销。快照存档由独立线程执行。PCM 和快照存档默认关闭，选流读取内存进度，不看录音文件增长。

## 模块、语言与运行依赖

| 模块 / 入口 | 技术与进程边界 | 不应混入的职责 |
|---|---|---|
| `tools/fsa-integration/native` | 原生 C / Win32 FSA；端点快照、规则、模式、会话启动和回验 | 不承载 DSP、ASIO 回调或第二套 Web 控制状态机 |
| `tools/pipeline-init` | Python 准备控制与枚举监督；PowerShell/WinRT 查询、选择、通知及显式 EDID 辅助 | 不接收逐包 PCM，不持续代替 FSA 管理端点，不自动写 EDID |
| `Enum-Hook.c`、`Native-Hook-Control.c` | C / MinHook；AudioSrv 枚举与 audiodg 捕获共用固定引擎和控制器源码 | 不加载 Frida/JS 引擎；两个目标进程的生命周期仍各自管理 |
| `tools/zig-scheduler` | Zig 调度、后端会话、专用设备控制线程、公共 DSP | 回调不执行阻塞设备打开/关闭、网络控制或文件录音 |
| `tools/native-receiver`、`tools/roar-bridge` | Rust 接收、适配、输出池和 ROAR 桥；进程内 C ABI | 公共输入不引入 LIRA 房间投影、裁剪或槽位 GC |
| ROAR / LIRA | ROAR 为 Rust 后端，当前接入 OLR；LIRA 为独立 C++ 后端 | OLR 是 ROAR 的模式，不与 LIRA 并列为后端；ROAR OBR 尚非当前接通模式 |
| `modules/audio-backends` | 抽离的原生 C++ LIRA 和 ASIO；独立模块 C ABI 留在本目录 | 不依赖完整 Spatial ASIO 工程、WPF 或 .NET 10 |
| `tools/telemetry/standalone.py` | 独立 Python 服务，可加载 C 计量 DLL（也有 Python 参考实现）；历史、HTTP/WebSocket（默认 8766） | 不负责音频控制，不在 renderer 中计算全套计量 |
| `nsp-scheduler.exe web-host` | 独立 Zig 进程，随 FSA 启动；状态、配置校验/保存、前端静态资源（默认 8767） | 保存不自动应用，无启停、模式、Hook、EDID 或急停 API |
| `tools/visualization-frontend` | 浏览器原生 JS / HTML / CSS / WebGL；默认浏览器打开 | Node 用于开发测试；产品不要求 Node 服务、WebView2 壳或 .NET 桌面 |

Python 不只用于遥测，也仍用于准备模块。PowerShell 的 WinRT/Add-Type 辅助依赖 Windows 自带的托管互操作环境；显式 EDID `plan` / HT Support 预检及 `inspect` 还调用旧 `work/OemEndpointProbe.exe`（C# / 系统 .NET Framework），并非仅历史测试代码。因此“原生音频 DLL 无 .NET”不等于“所有辅助脚本完全不经过 CLR”；不要求安装 .NET 10/WPF。该探针可由 `tools/endpoint-probe/Build-EndpointProbe.ps1` 单独构建，不进入音频热路径，也不把可选 ClientProbe/WwiseProbe 加回默认依赖。LLVM-MinGW 构建的 LIRA/ASIO 要带匹配的 `libc++.dll`、`libunwind.dll`；ROAR 的 unwind 依赖也应随其构建材料核对。

## 配置、生命周期与尚未实施项

播放配置默认为 `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`，兼容显式自定义路径。旧安装目录默认文件只迁移为用户副本，不覆盖已有目标。Web 保存配置供下次会话读取，不做热应用。FSA 配置及后端状态的存储合同没有因此统一迁移。

ASIO 随 NSp 会话常驻，无输入时静音送流；不再因首包等待或反复欠载自动结束会话，旧超时字段仅兼容解析。捕获/传输严重故障及设备故障仍应安全退出。音频自有工作线程和设备回调的 MMCSS 注册各有实现边界，不能据此声称整个进程硬实时。

七种输出布局已接通：5.1.2、5.1.4、7.1.2、7.1.4、7.1.6、9.1.4、9.1.6。自定义 6DoF 插槽仅预留；原生 AoIP / Inferno 移植仍是评估路线，未成为默认输出。WDM 补全不是当前产品输入链，OBR 名称的耳机捕获模式不等于已接入 ROAR OBR 渲染。

详细合同：[输出池](output-pool-processing-20261022.md)、[旁录](recording-isolation-20261024.md)、[布局](output-layout-presets-20261023.md)、[准备枚举](native-enumeration-hook-20261024.md)、[Web](../decisions/web-configuration-host-20261023.md)。[10/16–10/19 原架构页](../history/native-architecture-baseline-20261024.md)只保留历史证据，不作为当前依赖或性能承诺。
