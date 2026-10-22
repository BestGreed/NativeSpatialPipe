# DEV16：渲染后端与模式边界更正

2026-10-21，回溯修正 N03–N11 的术语、配置和实际分发。此前把 OLR 与 LIRA 放在同一 renderer 枚举中，混淆了后端和模式；这不是仅改界面文案。

## 统一合同

```text
NSP 捕获与传输 → 公共原始输入
  → ROAR 后端 → OLR 模式及其输入适配 → PCM
  或 LIRA 后端 → 专用输入适配与自主核心 → PCM
→ 公共输出处理 → ASIO 设备输出
```

ROAR 还支持其他模式；本次开发集成仅接通 OLR，不声明其他模式已接入 NSP 的新播放路径。LIRA 的首版没有对外子模式选择，不为它虚构 OLR 或 native 模式。

公共输出职责独立于模式，但本次交接布局仍限定为已接通的 48 kHz、12 声道 7.1.4；这不意味着 ROAR 的其他输出布局或耳机模式已获得同样的接口适配与验证。

| 范围 | 归属与既有轮次的准确含义 |
|---|---|
| 捕获原始属性、PCM、完整 XYZ/gain 字段 | N03 公共输入合同，不先经过 OLR 类型/坐标校验 |
| `olr_input.rs`、类型转换、坐标换轴、direction/scaled/reference、gain passthrough | ROAR 的 OLR 适配；N09 的默认半径映射改变限于该路径，不推及 ROAR 其他模式或 LIRA |
| 身份关联、parked、房间投影、负高度裁剪、量化/平滑、512 帧前视和尾部 | N10 LIRA 专用适配与核心，未成为公共规则或 ROAR 补丁 |
| 可靠 writer/sink、主音量/trim/delay/limiter | 各后端后的公共交接与输出处理；N06/N07 的首轮 OLR 验证不使它们成为 OLR 模块 |
| ASIO 驱动、设备时钟、Prime/欠载恢复，后端控制 worker | N04–N08 的公共设备与协调能力，独立于渲染后端及模式 |
| FSA、临时桌面 | 会话控制入口；传递后端及其模式，不拥有 renderer 的声音算法 |

## 配置、分发与报告

- 新配置使用 `renderer: "roar", mode: "olr"`，或 `renderer: "lira", mode: null`。ROAR 省略 mode 时沿用当前 OLR 默认。LIRA 不接受 OLR 模式；未接入的 ROAR 模式明确拒绝，不能静默回退。
- host 使用 `--renderer roar --renderer-mode olr` 或 `--renderer lira`。旧 `--renderer olr`／JSON `renderer: "olr"` 只作为 ROAR／OLR 的兼容简写；旧 `spatial` CLI 别名仍指向 LIRA。
- 运行报告统一给出 `renderer: "roar", rendererMode: "olr"` 或 `renderer: "lira", rendererMode: null`。Rust 内部分发区分 `Backend::Roar(RoarMode::Olr(...))` 与 `Backend::Lira(...)`，不再定义第三个 OLR 后端。
- 界面分别显示渲染后端及当前模式。协议中的 `mode` 是渲染模式，`spatialFormat` 是 Windows 捕获端点格式，两者没有对应绑定关系。
- `channelOrder: "olr"` 仍是既有 12 声道排列名称，两个后端均可输出此排列；它不是后端选择。DLL 名、C ABI 符号、既有 `roar-714.f32` 文件名保留兼容，不能凭这些名称推断数据由谁渲染。

N03/N07/N08/N09 现有证据只证明 ROAR／OLR 路径及其相应公共接口；N10 的原版对照只证明 LIRA 抽离。历史报告与录音不改写，不把局部成功扩大为整个 ROAR 已验收。硬件、端点切换及 N12 其他组合验证仍按原计划推进。

## 更正复验

- Rust 桥、Zig host、WPF 桌面及控制探针独立构建通过。
- 三项配置测试通过：ROAR／OLR 与 LIRA、旧 olr 简写、错误模式及非法配置拒绝。
- 两种 HT 的隔离控制链通过：分别验证 ROAR／OLR 与 LIRA 的后端/模式转发、原有 owner/停止边界及旧 FSA 无播放字段请求。HT 与 renderer 的测试配对只是覆盖用例，不是绑定关系。证据：`build/dev16-n11-desktop/runs/1792589836545868700/checks.json`；同目录 `desktop.png` 已目视检查后端和模式分层。
- `tools/roar-bridge/Test-RendererBoundary.py`：ROAR／OLR direction、scaled 两组对 N09 旧 DLL 逐字节一致，LIRA 对 N10 原版对照录音逐字节一致；新报告 renderer/rendererMode 正确。证据：`build/dev16-renderer-boundary/1792589833697004600/checks.json`。

这些是层级更正的限定复验，不据此替代 N12 余下组合验证或声学/实机验收；没有新增渲染能力或部署。
