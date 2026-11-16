# NSP 原生音频后端

2026-11-08 的产品路径通过 `outputBackend: "wasapi"`、`outputLayout: "7.1.4"` 与 `lsac.enabled` 接通 48 kHz 双声道设备。ROAR 或 LIRA 渲染 7.1.4，LSAC 下混和等响补偿在公共 DSP 之前。固定源支持 ROAR 或 LIRA，统一输入使用 ROAR OLR。见 [模块边界](../../docs/core/lsac-module-integration.md)和 [WASAPI 示例](../zig-scheduler/playback.wasapi.example.json)。

2026-11-03：WASAPI 已接入 Zig/FSA 常驻链路，支持 JSON `wasapi.mode="shared"|"exclusive"`，缺省 shared；`periodFrames` 缺省480，仅独占使用。独占通过新增原生 `nsp_wasapi_open_v2` 协商48kHz stereo，优先Float32，否则24/32/16-bit PCM；同一Rust Float32池在最终写设备缓冲时转换。事件独占每次写完整设备buffer，alignment失败会重新激活client；申请失败明确报错，不静默改回共享。shared旧ABI保持兼容。构建入口 `Build-Wasapi.ps1` 或CMake。长期异步设备时钟漂移补偿仍未实现。

本机Realtek静音测试支持24有效位/32位容器，480帧10ms和240帧5ms均200块/96000帧完整写出、唯一回收且无溢出，共享回归通过。当前FSA选择exclusive/240帧，DSP和master保持；这是单次设备buffer周期，不是端到端延迟，事件独占另有双缓冲。证据 `build/audio-backends/wasapi-exclusive-v1`、`build/lsac/wasapi-exclusive-001`。

WASAPI 逐块诊断通过 `Build-Wasapi.ps1 -DiagnosticTrace -TargetDirectory build/audio-backends/wasapi-trace` 单独构建。设备侧打点在 `src/wasapi_pool.cpp` 和 `src/wasapi_trace.h`，统一入口和渲染侧打点在 `modules/croksor/src`。从 [端点诊断读取器](../../tools/endpoint-probe/Read-WasapiTrace.cpp) 构建的 `Read-WasapiTrace.exe <调度进程 PID>` 可在会话运行期间读取本机共享环中的时间戳和帧号。诊断 DLL、CSV 与报告归入 `build/`。

2026-11-02 的独立宿主实验中，`src/wasapi_pool.cpp` 增加 48kHz Float32 双声道 WASAPI shared 输出。它通过原生 C ABI 接收 Rust 输出池，在固定容量 FIFO 内复制一次后立即归还租借，设备线程再写入 WASAPI 缓冲；不经过 Python PCM 回调，不修改默认端点。FIFO 和 Rust writer 均有有界背压，尚未实现长期异步时钟漂移补偿。该轮使用独立验证宿主。

历史实机验证使用 `tools/lsac/Run-NotebookJoc.py` 使用电影与电视 → MFT JOC 解码 → 标准 Windows 空间对象提交 → NSp audiodg 捕获 → ROAR OLR 2.0 → Rust 池 → 笔记本 WASAPI → EqAPO。`build/lsac/notebook-joc-live-008/report.json` 记录通过的有界试播及撤销状态；默认诱饵 DTS:X HT 端点保持不变，临时 MFT 注册和控制器在退出时撤销。`Test-WasapiPool.py` 为独立设备消费/租借压力验证，`Test-RoarStereo.py` 为不打开设备的真实 OLR Stereo 验证。当前 FIFO 曾达到 64 个 480 帧块，不能把这轮结果宣称为低延迟或已完成音画同步验收。

2026-10-24：两后端支持 5.1.2、5.1.4、7.1.2、7.1.4、7.1.6、9.1.4、9.1.6 的共享声道顺序，唯一预设表为 `contracts/output-layouts.json`。LIRA 新增 `nsp_spatial_create_layout`，旧 create 默认 7.1.4；512 帧固有延迟及房间行为保持独立。ROAR 6DoF 槽仅预留。见[布局合同](../../docs/core/output-layout-presets-20261023.md)。

公共 DSP、录音和遥测归档归 NSP 宿主/桥接层；ASIO 只承担设备职责，会话内常驻。旁录默认关闭，开启后也不在输出交付或设备回调线程写盘。设备 DLL 须配套 `libc++.dll`、`libunwind.dll` 并从模块目录加载。见[旁录隔离](../../docs/core/recording-isolation-20261024.md)。

只保留从 Spatial ASIO 抽离且供 NSP 使用的模块：LIRA C ABI/核心、ASIO 设备 C ABI/核心、必要公共头文件、系数/SDK及隔离测试。两套生产 DLL 分别构建，不链接旧完整引擎。

这里不含旧 `main.cpp`、捕获器、普通 PCM/WDM 补全、完整流 HT、Python 捕获管理、WPF、字体、安装器或发行工程。兼容布局适配仅供既有接口/测试使用，不进入两个生产 DLL。

- LIRA：`powershell -File tools/spatial-renderer/Build.ps1`。
- ASIO 独立模块及 C ABI：`python tools/asio-output/Test-DeviceModule.py --host-abi`（模拟驱动，不打开真实设备）。
- CMake：本目录提供 `NSP.SpatialRenderer`、`NSP.AsioOutput`，以及非默认的兼容布局静态目标。
- 公共宿主/池块验收：`python tools/integration-tests/Test-OutputPool.py`。

DLL 导出名和现有配置字段保留，目录迁移不改变声音、PCM 协议或驱动策略。来源和许可见 [LICENSING.md](LICENSING.md)；完整下游工程在工作区外保留，不作为主线构建依赖。`source-manifest.json` 保存抽离时逐文件哈希，后续修改以版本历史为准。

本模块的生产构建及运行均无 .NET/WPF 依赖。仓库另保留的 C# 客户端仅用于可选控制测试，不链接或加载进本模块，也不作为新 Web API host 的实现依赖。
