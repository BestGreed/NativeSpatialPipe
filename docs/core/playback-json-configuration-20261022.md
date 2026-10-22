# DEV16 合并功能与播放 JSON 配置

本轮将已经实现的控制项接到同一份播放配置，不扩展 ASIO 调度/延迟优化，也不新增界面控件。配置由 `desktop_playback.zig` 统一解析、校验，再交给实际 host。旧配置省略新增字段时保留原默认行为。

后续同日更新：公共 DSP 已前移至 Rust 输出池块，新增 `outputProcessing.bypass`，Zig PCM 队列和 scratch 改为池块租借交接。详见 [输出池处理与所有权](output-pool-processing-20261022.md)；ASIO 内部缓冲/时钟算法不变。

## 入口与生效范围

- FSA：在自己的 INI 中设置 `[NSP] PlaybackConfig=<播放 JSON 的绝对路径>`；使用 Zig 调度器，启动时读取并提交会话。端点准备、锁定与实时互验继续由 FSA 管理。
- 控制客户端：`start-session` 请求中的 `playback` 对象使用同一字段集。C# `PlaybackSettings` 保留为现有客户端实现及测试参考；临时 WPF 桌面已弃用，不再为其增加 DSP 等高级设置控件。公共 JSON 与控制协议继续使用。
- 直接 host / start：`--playback-config <绝对路径>`。
- JSON 是独立的播放配置，不是控制后端的全局状态文件；不要编辑 `session.json` 或遥测文件来修改设置。

字段在新会话启动时生效。修改前停止当前会话，修改后重新启动；不会热更新运行中的 DSP 或设备。会话保存解析后的配置及默认值，输出报告保存实际处理和路由设置。

候选检查命令（不会启动端点、Hook 或 ASIO）：

```powershell
& './build/json-feature-controls-20261022/nsp-scheduler.exe' validate-playback --playback-config 'D:/path/to/playback.json'
```

此命令检查字段、范围、数组长度和组合约束，并打印默认值及有效声道顺序；不保证 DLL 存在、导出完整或驱动支持某采样率/缓冲尺寸。运行时继续检查这些条件。文件使用 UTF-8，支持 UTF-8 BOM；不支持 UTF-16。未知字段报错。

示例：[中性 ROAR/OLR 配置](../../tools/zig-scheduler/playback.example.json)。先替换 DLL 路径和驱动 CLSID。编辑器可关联 [JSON Schema](../../tools/zig-scheduler/playback.schema.json)；不要向播放文件插入 `$schema` 字段，运行时严格拒绝未知字段。最终以运行时校验为准。

## 功能与字段对应

| 已合并能力 | JSON 字段 | 默认值 / 约束 |
|---|---|---|
| 渲染后端与模式 | `renderer`, `mode` | `roar` / `olr`；LIRA 用 `lira` / `null`。旧 `renderer: "olr"` 仅兼容简写 |
| 公共 Rust 桥接库 | `bridgeDll` | 必填绝对路径 |
| LIRA 独立渲染库 | `rendererDll` | LIRA 必填绝对路径；ROAR 不使用 |
| ASIO 输出库与驱动 | `asioDll`, `driver` | 必填绝对路径；驱动为 `asio:<CLSID>` |
| 捕获流选择 | `sourceStream` | `0`，允许 0–3；是捕获流编号，不是设备声道 |
| 设备采样率与缓冲 | `sampleRate`, `bufferFrames` | `48000` / `0`；采样率 8000–384000，缓冲 0–65536；0 沿用驱动选择，最终由设备确认 |
| 公共输出主音量 | `master` | `1`，线性倍数 0–16；保留原根字段，不迁入嵌套对象 |
| 公共 DSP 显式跳过 | `outputProcessing.bypass` | `false`；true 跳过 master/trim/delay/limiter，保留配置但本会话不应用；中性配置自动跳过 |
| 逐声道 trim | `outputProcessing.trim` | 12 个线性倍数，默认全 1，每项 0–16 |
| 扬声器校准延迟 | `outputProcessing.delayFrames` | 12 个整数，默认全 0，每项 0–48000，按源 48 kHz 帧计 |
| 可选峰值 limiter | `outputProcessing.limiter.enabled` | `false` |
| limiter 峰值上限 | `outputProcessing.limiter.ceiling` | `1`，范围 (0, 1]，线性满幅 |
| limiter 释放时间 | `outputProcessing.limiter.releaseMs` | `50`，0–10000 ms |
| 可选前视 | `outputProcessing.limiter.lookaheadFrames` | `0`，0–4800；只在 limiter 开启时生效，增加 frames / 48000 秒延迟 |
| ROAR/OLR 半径映射 | `positionMapping.mode`, `positionMapping.reference` | `scaled` / `1`；reference 有限且大于 0。兼容 `direction` 要求 reference=1。LIRA 禁用此对象 |
| 命名声道顺序 | `channelOrder` | `olr`；也可 `dvs-speakers` 或 `custom` |
| 自定义源声道顺序 | `routing.sourceChannelIndices` | 仅 `custom` 可设置且必须提供，12 个 0–11 索引；允许显式重复源声道 |
| 物理输出选择 | `routing.physicalChannels` | 默认 0–11；12 个不重复的 0–255 索引，设备打开后还需符合实际通道数 |
| 设备时钟匹配 | `clockSync` | `true`；由设备输出模块执行，renderer 不再补偿同一误差 |
| 有界启动预缓冲 | `prebufferFrames` | `0` 自动；显式值 0–48000，按源帧计且不低于设备最低 Prime 门槛 |
| 启动超时 | `startupTimeoutMs` | `5000`，正整数 |
| 重缓冲超时 | `rebufferTimeoutMs` | `1000`，正整数 |
| 最大重缓冲次数 | `maxRebuffers` | `3`，非负整数；0 不允许恢复续播 |
| 收尾排空超时 | `drainTimeoutMs` | `5000`，正整数 |

DSP 的 trim、校准延迟和前视沿用源侧 **48 kHz / 12 声道**，不随设备 `sampleRate` 改单位。时间/计数采用 u32；每声道数组必须正好 12 项，不能以短数组补默认。嵌套对象可以只提供部分字段，省略项采用默认值。

## 声道与处理顺序

两种 renderer 交到公共输出层的标准顺序为：

`L R C LFE Ls Rs Lrs Rrs Ltf Rtf Ltr Rtr`

`trim` 和 `delayFrames` 始终按这个顺序填写。公共 DSP 在渲染后、设备路由前执行；`master` 是输出音量，不是对象属性 gain。limiter 是跨通道关联的采样峰值限制，不承诺 true-peak 限制。

路由数组的同一索引表示同一个输出槽：`sourceChannelIndices[i]` 选择处理后的标准源声道，发送到 `physicalChannels[i]`。两种索引均从 **0** 开始，设备面板的输出 1 对应物理索引 0。调换路由不改变 DSP 数组的语义。

`dvs-speakers` 使用此前确认的实际接线：

`L C R Rs Ls Rrs Lrs LFE Ltr Ltf Rtf Rtr`

对应源索引 `[0,2,1,5,4,7,6,3,10,8,9,11]`。物理输出仍默认 0–11；若只改物理插口，可以保留该命名顺序，单独设置 `physicalChannels`。任意源顺序则使用 `custom`。例如：

```json
{
  "channelOrder": "custom",
  "routing": {
    "sourceChannelIndices": [0,2,1,5,4,7,6,3,10,8,9,11],
    "physicalChannels": [0,1,2,3,4,5,6,7,8,9,10,11]
  }
}
```

这是补充到完整配置的片段。修改物理输出前按实际接线确认扬声器。

## 半径、恢复与未新增的开关

`scaled` 使用源坐标 / reference 进入 OLR，保留距离信息；reference 的含义是源坐标中对应 OLR 半径 1 的尺度，不凭 JSON 自动推导房间米制大小。`direction` 仅作为旧映射对照。配置显式提供 `positionMapping` 时不得再用位置命令行参数重复覆盖；LIRA 保持自身原版空间行为。

输出欠载仍按既有有界重缓冲策略处理；设备断开、报错或重置请求安全停止，不自动换设备。捕获传输断序/损坏仍是致命错误，不能通过这些输出字段改成丢包续播。

以下没有伪造配置入口：对象 gain 额外乘法、ROAR 槽位 GC、LIRA 房间投影/负高度裁剪开关、捕获检查点恢复、端点轮询服务、Inferno 网络发送。LIRA 原有行为继续封装在专用后端。捕获初始化及 FSA 互验边界保持既定方案。

## 版本与验证边界

新 host 接受旧播放 JSON；旧 host 不认识本轮新字段。新客户端与 host 应成套构建使用，现有已运行测试实例未被替换。这里的兼容是省略新字段时的行为兼容，不是向旧程序发送新字段的兼容。

验证入口：`tools/desktop/Test-PlaybackJson.py` 和 `tools/desktop/Test-Desktop.py`。前者使用自有捕获夹具、真实 host、原有模拟 ASIO 驱动，将回调 PCM 与离线 DSP 对照，并检查原生 FSA 转发；后者检查 C# 字段序列化及会话归属/停止行为。真实驱动听感、GUI 高级控制项和持续性能验证不属于本次配置接线验收。

本轮结果：7 项配置检查、7 项公共 DSP 检查通过；旧配置、ROAR/DSP、LIRA/DSP/自定义路由三条回调 PCM 与离线处理逐字节一致；9 类错误配置在会话创建前被拒绝；原生 FSA 转发与 C# 客户端回归通过。证据为 JSON 接线结果?????????????? 和 桌面控制回归??????????????。均为隔离夹具验证，不表示已部署到现有运行实例。
