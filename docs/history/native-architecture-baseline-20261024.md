> Historical snapshot archived 2026-10-24. Superseded by [current architecture](../core/current-architecture.md). Paths, mandatory recording, performance numbers and build status below describe their original dates only.

# 当前原生管线

2026-10-24 当前路径：C 捕获/共享内存 → Rust 接收/公共输入 → ROAR（当前接入 OLR）或 LIRA → Rust interleaved 输出池 → 池内 DSP/bypass → 池块租借 → ASIO。两后端共享七种输出布局，设备内部队列、SRC 与时钟匹配保留。阻塞设备操作归专用控制线程，端点/会话管理归原生 FSA，独立 Zig Web Host 只读状态及校验/保存配置。Python 仍承担遥测/测试，原生链不依赖 .NET。

正常运行不写 PCM/快照大文件，显式开启时分别进入专用有界旁录和独立 JSONL 线程；播放交付不执行文件 IO。遥测选流读取 renderer 内存计数。D15 的容量/保留策略及安装目录迁移仍待做。见旁录（开发参考未随公开源码提供）、布局（开发参考未随公开源码提供）、播放配置（开发参考未随公开源码提供）与合并状态（开发参考未随公开源码提供）。

下文保留早期架构证据；旧图中的输出队列和后台文件路径不代表当前播放 PCM 路径。

历史说明（2026-10-19）：当时完成 Python/Zig 各一分钟 Wwise 对照，Python 遥测仍在启动链中，完整运行记录当时默认保留。记录默认值现已由 10/24 方案覆盖；历史测量不自动代表当前构建的全部性能。下文 10/16–10/17 基线保留证据，不作为当前全部能力说明。

> **2026-10-17 目标重新核定：** 当前产品范围以实现目标基线（开发参考未随公开源码提供）为准：FSA 用户端点按规则匹配软件，异步 Web 服务提供管理面板与现代音频可视化；IAMF/OAR 的 OBR＋OLR、高性能原生对象管线与渲染、FPS 低延迟实战验收；Lighthouse 光学扬声器布局输入与原生 IKO 均为实验性支持；产品输出仅 ASIO 或原生局域网流。下文旧路线及排期若有冲突以新基线为准，历史实测结论保持不变。


2026-10-16。

核心性能边界止于ROAR渲染完成；目标P99＜2ms，EXP-018三线程MMCSS随机负载三轮1.436–1.493ms已达标。DSP转维护，保留individual，暂停grouped通用化。后台文件写盘为验证设施；声卡缓冲、驱动和实际播放属于个性化适配，不纳入核心模块调优验收。三线程 MMCSS＋individual 已接入 Run-Native.py 默认配置，沿用 EXP-018 的已验收调度策略；本次仅修复控制入口，不改捕获、接收或渲染实现，不重复性能验收。

```mermaid
flowchart LR
 A[Windows对象提交] --> B[MinHook C快照复制]
 B --> C[C捕获worker / MMCSS]
 C --> D[有界共享内存和事件]
 D --> E[Rust接收 / MMCSS]
 E --> F[原生渲染队列]
 F --> G[元数据与ROAR DSP / MMCSS]
 G --> H[有界输出队列]
 H --> I[原生后台文件写盘]
```

Zig 或保留的 Python 控制层创建进程/映射、选择模式并汇总结果；核心原生接收路径不经 Python 逐包接收 PCM。Python 仍运行旁路遥测。Wwise＋WAAPI 是实验输入控制，不是产品运行依赖。系统 audiodg 只加载原生 Hook、捕获和发送模块；MinHook 驻留模块在 Hook 停止后保留 trampoline。

## 运行与构建

当前捕获入口：`python tools/endpoint-probe/Run-Native.py F:/TMPE/<new-run> <audiodg-pid> --duration 90`。调用前须完成系统版本核验和 H16/Sonic 配置；入口不自行切换端点。默认启用捕获、接收、渲染三条自有线程 MMCSS（显式设置 NSP_RENDER_MMCSS=1）；`--normal-scheduling` 明确关闭三条线程的 MMCSS，为普通调度对照，`--full-timing` 使用独立诊断 DLL。该入口限定单流和 480 帧，不启用原始 NSP 录制。

H16/Wwise 一分钟实验使用 `Run-CaptureScheduler.ps1`；它包含端点设置/恢复和完整负载校验。全节点普通/双 MMCSS 对照由 `Run-FullLatencyComparison.ps1` 编排。启动时检查单 Wwise 实例及 WAAPI 监听归属。

构建脚本：`Build-NativeHook.ps1`、`Build-NativeCapture.ps1`、`Build-NativeSend.ps1`、`Build-CaptureScheduler.ps1` 位于 `tools/endpoint-probe`；两个 Rust 构建入口分别为 `tools/native-receiver/Build.ps1` 和 `tools/roar-bridge/Build.ps1`。使用固定 Cargo.lock 和离线缓存。标准 DLL 与 `*-full-timing` 诊断 DLL 使用不同输出目录，当前已验证标准 DLL 保留。

## 测量边界

| 节点 | 当前可观测项 |
|---|---|
| Hook / 快照 | callbackMs、captureToCopy |
| 源 worker / 交接 | ownedCopyToSend、sendToAcquire |
| Rust receiver | descriptorParse、nativeSubmit、captureToSubmit |
| 原生渲染队列 | queueWaitMs |
| 渲染内部 | decodeMs、metadataMs、dspMs、workMs |
| 诊断输出 | writerQueueMs、bufferedWriteMs |
| 直接累计计时 | captureToRenderMs、captureToBufferedWriteMs |
| 结束写盘 | finalFlushSyncMs（结束时 flush＋sync） |

节点统计存在嵌套关系，不能直接相加分位数。直接累计计时传递同一捕获 QPC；慢包附 sequence 和捕获/渲染/写入时间戳，超过 256 条时保留总数并报告溢出。写盘耗时指缓冲写函数返回，不是逐包持久落盘。真实扬声器播放、设备缓冲和欠载反馈尚未接入，因此本项目当前无法报告完整声学端到端延迟。

## 接收器启动诊断（2026-10-17）

10/17 定位及修复（开发参考未随公开源码提供） 已定位受控测试中 AppContainer 的共享映射/事件访问拒绝。共享内存集成测试在非 AppContainer 环境运行；不要求为此更改系统音频配置或放宽共享区 DACL。新增 `nsp_receiver_start_error()` 在启动控制线程返回阶段/Win32 码，Python 报告实际 DLL 路径；标准 DLL 未覆盖，新诊断验证构建位于 `build/receiver-init-fixed`。正常调度/MMCSS 位配置及逐包路径保持不变。
