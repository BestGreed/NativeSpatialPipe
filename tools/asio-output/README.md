# ASIO 输出模块接入检查

DEV16 N07已接通可选NSP软件输出路径并通过假驱动回调录音测试。接口现位于 `modules/audio-backends/src/asio_output_c.h`，Windows x64、ABI v1（新增hold导出）；独立 CMake 目标为 `NSP.AsioOutput`。完整 SpatialASIO 工程已移出主线；最新状态及验证见 目录抽离记录??????????????，下文早期未实测描述仅为历史。

N04 宿主边界核对见 调用合同??????????????。N05/N07 已由专用控制/输出工作线程独占句柄，渲染线程通过可靠PCM交接，不直接调用设备 ABI。N07增加hold导出，原配置/状态结构不变。

## 离线检查

```powershell
python tools/asio-output/Test-DeviceModule.py --output build/dev16-asio-c-abi-step2
```

N04 增补检查：`python tools/asio-output/Test-DeviceModule.py --output build/dev16-n04-asio-boundary --host-abi`，增加12声道/480帧包与 Zig 0.13 的实际 C 头导入、动态导出调用检查。

默认使用仓库已有 LLVM-MinGW，也可用 `--compiler` 指定同类工具链。脚本编译/运行假驱动的设备、旧入口和 C ABI 测试；C 测试文件单独按 C11 编译。还生成真实分支 DLL，仅加载检查导出、版本和非法配置拒绝，不枚举或打开真实 ASIO 设备。脚本不调用捕获器、不修改 FSA、不部署文件。

## 最小调用合同

1. 同一工作线程创建并初始化配置，`struct_size=sizeof(nsp_asio_config)`、`abi_version=1`，输出句柄初值为 NULL。驱动 ID 为 UTF-8 `asio:` 标识。源/物理通道均从 0 开始，源 PCM 固定 48 kHz、interleaved f32，最多 32 声道。配置数组/字符串在 open 返回前复制。
2. `open` 创建驱动及现有队列，并在当前线程持有 STA COM 初始化；调用线程已是 MTA 时会明确失败，不隐式迁移线程。N08 实机发现 DVS 注册为 Apartment，MTA 激活会因私有接口无法跨 apartment 封送而返回 `E_NOINTERFACE`，因此修正 N04 的线程模型。模块不创建工作线程，所有 handle 调用由创建线程串行执行，不能放音频回调里；现有专用控制线程通过 `service` 处理驱动消息。
3. `start` 允许先启动静音回调；调用方按 `get_stats.minimum_prime_frames` 和自己的有界缓冲策略准备数据，再 `prime` 一次。Prime 前源时间不推进。接口只提供所需帧数，不替上层确定预缓冲时限或调度。
4. `submit` 同步复制 PCM；`first_frame` 从 0 连续递增，调用方保证至少 `frames*source_channels` 个可读样本。未接受的参数/样本不计入提交帧数。现有队列容量保留，溢出是故障，不主动丢旧；没有新增缓冲优化。
5. `service` 使用零事件等待超时，沿用现有驱动消息处理、填充和消费后清理；驱动调用/Windows消息分发仍可能阻塞，不承诺调用时长有界或实时安全。由专用工作线程安排执行节奏，不能放协调循环或音频回调。数据异常返回 INVALID，运行/驱动异常返回 DEVICE_ERROR 并锁定故障；故障后只允许观察和收尾。
6. 正常输入结束先 `finish_input`，继续服务至 `get_stats.drained`，再 `stop`。正常排空与故障时提前停止分开。stop 沿用下游已有有界等待，可能阻塞；不代表所有已提交但尚未渲染的源 PCM 必然已排空。
7. 启动过的设备必须 stop 成功后才能 close；失败时保留 handle，不假报已释放。close 成功将调用方 handle 清为 NULL。空 handle close 幂等；stopped handle 不可 restart，要重新 open。不得使用已关闭或伪造 handle。

接口不跨边界暴露 C++ 容器、分配器或异常；返回码见头文件。`last_error` 是调用线程的即时错误文本，下一次普通 API 调用会覆盖，调用方复制到自有缓冲；不跨模块释放内存。get_stats 对错误/停止状态仍可使用，但不是故障自动恢复入口。

新 C 入口禁用自动 reset 重开：驱动重置/采样率变化等请求在 service 中返回故障，由宿主停止并由用户重新启动。旧 C++ 构造路径保留原有 reset 行为供兼容回归；不要把旧行为当作 NSP 设备策略。N06 已取消新 C 入口的生产PCM硬截断与浮点设备格式硬截断；整数设备格式仍做必要范围饱和。新入口不导出设备音量/trim/delay，值保持中性，公共处理器在renderer后、设备提交前执行。旧入口保留校准及clamp兼容行为。

当前不提供设备枚举/控制面板或重复公共调音的设备C API。软件PCM/公共处理/owner接线和有界重缓冲见 N07交付??????????????：`nsp_asio_hold`保持驱动打开并暂停源消费，恢复前先填充ring；生产入口的缺样本不再隐式跳过源帧。最小宿主参数、EOF和报告见N07记录。验证：`python tools/asio-output/Test-Playback.py`，仅自有OLR/假驱动，不依赖DVS内部订阅。真实设备与时钟测量仍在N08。

N08 真实驱动工具：`Test-RealDevice.py --driver 'asio:{CLSID}'` 仅查询；加 `--play-silence` 后才执行实际 OLR→公共处理→owner→设备的静音正常/受控欠载用例。`--build-only` 只构建。先读取能力，再按设备当前率播放，不修改 Dante 路由；详见 N08记录??????????????。静音成功不代表物理通道或长期漂移已验证。

N08 实测修正：设备start/stop期间在owner线程持有下游既有 `AudioScheduling` 请求（音频调度与1ms定时，按原能力尽力申请、成对释放）。流式适配的默认预缓冲目标为设备minimum加一个480帧源交付周期；显式配置仍不低于minimum。缓冲门槛与新增等待会反映在报告，不等于固定端到端延迟；重复欠载、超时和设备故障的退出策略不变。

持续运行测试可加 `--case normal --seconds 60`；时长限3..120秒，默认仍为两项各3秒。报告每秒采样一次回调、缓冲和源位置，并记录供给间隔，供区分缓冲积累与测试源调度抖动。统计是宿主观测，不是DAC时钟/声学延迟标定。

用户已配置网络输出1..12→输入25..36时，可用 `Test-RealDevice.py --driver 'asio:{CLSID}' --network-return`。独立测试DLL在同一会话录下设备送出/网络回送，逐路发送−40dBFS信号，按统一偏移比较12路；原始OLR输出同时保留。仅适配当前48k/Int24LSB，要求至少36路输入，不更改路由。正式设备C ABI不增加输入功能。结果与离线复算命令见N08记录；这是数字回送验证，不是扬声器声学测试。

本机已确认布线：`L C R Rs Ls Rrs Lrs LFE Ltr Ltf Rtf Rtr`。正式host须加 `--asio-channel-order dvs-speakers`；通用默认 `olr` 不变。网络探针默认使用该布线，显式参数为 `--output-order dvs-speakers`。分析先辨认OLR各路实际落到哪个设备输出，再比较送出/回送，避免仅编号匹配却扬声器语义错误。

独立探针会开启ROAR渲染MMCSS、在供给线程注册，并检查实时sink writer的MMCSS注册/优先级/解除。`renderer-summary.json` 保存实际线程结果；网络测试构建另观测ASIO owner请求结果。驱动自有回调仅记录线程ID与Win32优先级，不能将其推断成MMCSS身份证明。
