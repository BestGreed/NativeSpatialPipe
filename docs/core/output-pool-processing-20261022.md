# 公共输出池 DSP 与租借交接

2026-10-24 更新：本文描述的“writer 先写盘再处理/交付”已被[独立可选旁录](recording-isolation-20261024.md)替代。当前实时交付不执行文件 IO；PCM 旁录与遥测存档默认关闭，流选择改为读取内存进度。下文保留当时改造边界作为历史说明。

后续运行配置：已按用户要求另建成套 FSA 副本并以管理员权限启动，仅管理入口就绪，未自动启用 Hook 或播放，见 FSA 输出链路记录（开发参考未随公开源码提供）。下文隔离验收结论不扩大为本次真实 DVS 播放已验。

2026-10-22，DEV16。用户本轮明确授权优化公共输出级路径；范围仅为 DSP 前移、可配置跳过及移除 Rust 池到 ASIO 提交边界之间的两次 PCM 复制。不恢复此前已终止的 ASIO 驱动调度、时钟、SRC、回调 ring 或延迟优化，不恢复已弃用的临时 WPF 入口。

## 实际路径

旧：`renderer → Rust interleaved 池 → Zig PCM 队列（复制）→ scratch（复制、DSP）→ ASIO submit`

新：`renderer → Rust interleaved 池块 → 池内原地 DSP / bypass → 有界池块描述符 → ASIO owner 直接提交池地址 → 归还池块`

ROAR/OLR 和 LIRA 共用此路径。Zig 不再分配 32 块 PCM 队列或 scratch，只保留 32 项指针/帧数/归还函数描述符。标准 480 帧 × 12 声道 Float32 的原两处 staging 存储共 760,320 字节已移除；这不是整机内存实测差值，也不是全链路零复制声明。

ASIO owner 通过现有 C ABI 同步提交借用地址，ASIO 模块直接读取 Rust 池内存。**ASIO 内部仍复制到自己的 PCM 队列，随后进行设备时钟/SRC/ring/格式处理。** 本轮不改这些算法，也不让硬件回调操作 Rust 池或等待渲染器。它不是新增驱动线程主动调用 Rust 的 pull 接口；达到的是设备提交边界直接借用池块。

## DSP 位置与配置

沿用 Zig `output_processing.zig` 算法，通过进程内 C ABI 回调，在 Rust 输出 writer 独占的可写池块上执行。没有将算法再移植一份 Rust 实现，也没有新增处理线程。计算发生在发布池块之前；输出设备线程只提交，不再处理一次 DSP。

在完整播放 JSON 的 `outputProcessing` 中设置：

```json
{
  "outputProcessing": {
    "bypass": true
  }
}
```

- `bypass: true`：跳过全部公共 DSP，包含根字段 `master`、逐声道 trim、校准 delay 和 limiter/前视；保存的参数不丢失，下一次关闭 bypass 后重新生效。
- `bypass: false`（默认）：按配置处理；若 master/trim 全为 1、delay 全为 0 且 limiter 关闭，则自动跳过样本处理。
- 跳过时不扫描/改写 PCM，不分配 delay、lookahead、peak 历史，不生成 DSP 尾帧。块布局、顺序、生命周期检查及 ASIO 提交的非有限样本检查保留。
- 不热切换：仍在停止后修改 JSON、启动新会话。无效配置即使 bypass 开启也会被拒绝。
- 声道语义、48 kHz 源帧单位及 DSP 在物理路由之前的顺序保持不变；未加入新的声学算法。

`validate-playback` 的 `effectiveOutputProcessing` 会显示 `bypass`、`effectiveBypass`、`placement` 和有效前视；实际输出报告提供同样的旁路/位置状态，以及 `outputHandoff` 的池块接收/归还计数和 DSP 尾帧数。

**历史（10/22，已替代）：** 当时 writer 先写文件再处理/交付的行为仅作为迁移证据保留。**当前（10/24）：** renderer PCM 可选旁录仍位于公共 DSP 前，但写入独立录音池和线程；播放交付在输出池中执行 DSP 后发布租借描述符，不等待磁盘。遥测仍为独立旁路。见[旁录解耦](recording-isolation-20261024.md)，不能因旧名称 writer 而重新把磁盘 IO 接回播放线程。

## 所有权与结束行为

Rust 在初始化时创建有界池和稳定的块 token，不逐包分配租借描述符。新增 `nsp_pipeline_set_pool_output` 入口，配置在首次输入提交之前完成，与旧同步 sink 互斥；旧 sink 导出继续保留供已有调用者使用，新 host 不静默退回旧复制路径。

1. Rust 独占块，原地处理后发布只读 PCM 地址和 token。
2. 发布成功，设备侧持有租借；发布失败，所有权仍归 Rust，调用方不得归还该 token。
3. owner 从描述符取出块，调用既有同步 `nsp_asio_submit`，返回后归还一次；即使 submit 报错也归还当前块。
4. 故障立即归还所有尚在描述符队列中的块。已经取出、正在 submit 的块由 owner 归还，防止重复释放或提前重用。
5. pipeline 销毁后仍可能存在待播放块；Stream 单独持有 bridge DLL 加载引用，归还函数和块 token 的生命周期不依赖 Pipeline 实例。worker 退出且全部块归还后才卸载此引用。

LIRA 的原 512 帧 renderer 尾音先处理完，再由公共 DSP 在池块中排空自己的校准/前视尾音。`receivedFrames` 保持 renderer 帧数，DSP 额外尾帧单独计数，`submittedFrames` 包含两者。结束时可复用已有空闲块生成尾帧，不创建 scratch。

实时块队列满仍明确失败，不丢旧块续播。只有非实时 EOF 尾帧允许等描述符空位；以既有 `drainTimeoutMs` 约束整段尾帧的发布等待，故障/超时退出并归还资源。没有增加正常块的等待聚合目标。

## 验证与候选构建

复现入口：`python tools/desktop/Test-OutputPool.py`。在 `build/output-pool-direct-20261022/` 构建成套候选，使用自有捕获夹具和原有模拟 ASIO DLL，不打开真实端点、WPF 界面或 DVS。

本轮通过：6 项池块/超时检查、8 项 DSP 检查、8 项配置检查；8 条实际 host 软件路径的回调 PCM 对照（两 renderer、DSP、默认中性及显式旁路、最长 48,000 帧校准＋4,800 帧前视尾音）；12 组播放/重缓冲/超时/设备故障回归，均检查池块接收与归还计数一致；9 类错误配置启动前拒绝；原生 FSA JSON 转发通过。非界面 C# ClientProbe 的 bypass 转发及会话归属检查也通过，不构成恢复维护临时桌面。

证据：完整隔离验收（开发参考未随公开源码提供）、host PCM 对照（开发参考未随公开源码提供）、客户端转发回归（开发参考未随公开源码提供）。原 ASIO 模拟 DLL 与 LIRA DLL 哈希保持不变。

新 host 与含新增 pool ABI 的 bridge 应成套使用。候选 bridge 位于 `build/output-pool-direct-20261022/bridge/release/nsp_roar_bridge.dll`，播放 JSON 的 `bridgeDll` 应指向该成套构建；老 bridge 缺少导出时明确失败。未替换当前运行的 FSA、宿主或模块；本轮结果仅证明隔离软件路径，真实设备播放/持续压力及性能测量仍需后续有界验证。
