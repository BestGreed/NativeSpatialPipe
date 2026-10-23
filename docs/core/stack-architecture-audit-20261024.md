# 技术栈、运行入口与活跃文档复审

2026-10-24，alpha-dev。范围为本地产品入口、构建清单及活跃说明的对应关系，不新增运行框架，不扩展设备/声学性能验收。当前架构唯一入口为[当前架构](current-architecture.md)。

## 核对结果

| 核对对象 | 实际入口 / 证据 | 结论 |
|---|---|---|
| FSA 默认调度 | `Build-Integration.ps1`、`native/nsp_playback.h`、Zig `backend.zig` | 默认 Zig；Python 调度仅显式兼容对照，播放配置不能静默交给旧 Python |
| 端点准备与枚举 | `pipeline.py` → `manager.py` → `native_enum.py` → 原生控制器 / DLL | Python 控制仍存在；AudioSrv 枚举已无 Frida import / agent.js，和捕获统一 MinHook |
| 默认会话启动辅助 | `backend.zig` → `Run-GameTelemetry.ps1`、`host.zig` → `standalone.py` | PowerShell/WinRT 和 Python 遥测仍是产品辅助依赖，不得误标为只有测试使用 |
| C++ 模块 | `modules/audio-backends`、LIRA/ASIO 构建及本轮 DLL 导入 | 原生，不加载 .NET 10/WPF；运行库随模块部署，不能称完全无运行库 |
| PCM / DSP | `host.zig`、Rust `recording.rs`、`output_stream.zig` / `output_playback.zig` | 池内 DSP、描述符租借；旁录独立且默认关闭，不恢复写盘中转或 Zig PCM scratch |
| Web / 遥测 | Zig `web_host.zig` 与 Python `service.py`、前端 package.json | 两个独立服务职责；浏览器 JS，Node 仅开发测试；无 Web 会话控制 |
| Windows 托管辅助 | PowerShell 的 System.Runtime.WindowsRuntime / Add-Type | 现有系统互操作，不是 .NET 10 桌面依赖；“所有代码全原生”描述不成立 |
| 显式 EDID 预检 | `edid.plan` / `inspect` → `work/OemEndpointProbe.exe` | 既有 C# / Windows .NET Framework 辅助，不仅是测试；补齐源码及独立构建入口，未迁移为原生 |
| 旧 C# 客户端 | `tools/control-client`、历史 ClientProbe / WwiseProbe | 可选夹具，已标记历史；不参与默认产品依赖，不导出为新前端 |
| 网络与 WDM | 规划、研究和历史夹具 | 原生 AoIP / Inferno 未接入；WDM 完整流补全未进入核心，不混入依赖或当前流程 |

本轮没有新增语言、包管理器、Web 框架或常驻服务。保留技术栈为 C / Rust / Zig、抽离模块的 C++、准备与遥测的 Python、系统 PowerShell/WinRT、浏览器 JS。不能用“没有新增技术溢出”掩盖既有辅助依赖；它们已逐项写明。

公开导出复验额外发现 `tools/edid-probe/edid_probe.py` 漏出，导致 EDID 测试导入失败。已补入只读解析器、纯合成测试，以及现有 C# 预检源码和独立构建说明；不导出机器专属 OG27 覆盖脚本，不自动构建/执行探针或写 EDID。这是已有依赖闭包的修复，不是增加新的产品技术栈。该遗留预检原生化仍是后续工作，不能宣称准备层已全部原生化。

## 文档纠偏与历史边界

- 当前架构页重写为现行链；原写盘图及早期延迟数据移至历史页。历史测量不继承为本轮性能结论。
- 修正枚举说明的反接：Enum OLR → 扬声器 / HookSpeakers，Enum OBR → 耳机 / HookHeadphones。FSA 菜单与测试已如此实现，本轮不改菜单代码。
- 更新 FSA 与依赖说明：Python 同时用于准备和遥测；播放配置默认从用户目录解析，不能仅凭 INI 字段空白就断言不启用 ASIO。
- 早期 FSA Sonic-only/强锁定暂停全部规则、合并期 writer/PCM staging、同进程遥测、WebView2 壳和 WPF 路线，分别标注为历史或已弃用；不因仍可查阅就恢复为待实施任务。
- Web 控制、自动 EDID、旧有限 ASIO 播放超时不是当前路线。已实施的是只读状态及配置保存、显式 HT Support、会话内常驻输出。
- 未来 ROAR OBR、6DoF、原生网络输出、容量/保留策略、正式安装与发行仍保留为未完成项，不以文档清理宣称完成。

## 验证与发布范围

枚举迁移的准备模块 54 项回归、AudioSrv 实机启停/恢复及只读字节审计已通过。共享捕获控制器原构建模式也编译通过。此次复审另外完成公开原生枚举 DLL/控制器构建、公开准备模块 54 项和 EDID 解析 15 项回归、旧 Framework 预检探针编译，以及文档编码/链接与清洗导出哈希核对。预检探针仅编译，未执行。没有为文档更新重启 FSA、开启 Hook、写 EDID 或重做音频质量/延迟验收。

核心开发分支实际名为 `alpha-dev`（用户所称 dev），公开仓库亦为 `alpha-dev`；不额外创建同义 `dev`。同步目标为核心 `alpha-dev` / `main` / `open-source` 与公开 `alpha-dev` / `main`。公开源码沿用独立提交历史和来源 manifest；不推入私有实验、机器配置、原始 PCM 或历史研究负载。
