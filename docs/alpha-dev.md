# alpha-dev 源码范围与来源

## 当前进度（2026-10-22）

| 范围 | 当前状态与边界 |
|---|---|
| N03 公共输入转换 | 已接通，保留完整坐标和属性；不合入下游旧捕获器或游戏普通 PCM 补全 |
| N04–N08 ASIO 与公共输出 | 已接通设备 C ABI、控制线程、DSP、可靠播放、静音预启动和有界欠载恢复；DVS 48 kHz／128 帧及 12 路数字回送已有限验证 |
| N09 ROAR／OLR 映射 | 默认完整半径 scaled/reference=1，保留 direction 兼容；软件单位合同不等于物理房间标定 |
| N10 LIRA | 独立模块接通公共输入／输出，保留原房间投影、裁剪、平滑及 512 帧固有延迟；未与 OLR 混为同层模式 |
| N11–N12 FSA／控制集成 | 限定集成及两组经典 Wwise HT 实测已完成；临时 WPF 后续弃用，完整下游目录已移出，FSA 保留为实际管理入口 |
| 后续输出池优化 | DSP 在 Rust 输出池块内处理或跳过，ASIO owner 租借同一块提交，移除 Zig PCM 队列与 scratch 两次复制；设备内部缓冲未删除 |
| 工作区与分支整理 | 清理 51 个中间载荷、归类 74 篇文档；私有和公开 alpha-dev 已同步，rmj 原始贡献独立保留；本次不发布二进制包 |

目前待推进：D15 记录与实时供数解耦；FSA 等待首包及错误反馈；一次已出声会话的 OutputTimeout 收尾排查；Web API host 合同与前端适配论证；后续原生网络输出评估。Inferno 尚未导入。ASIO 内部传递、调度和延迟优化保持停止，不因本次整理重启。

本轮导出复验只覆盖离线构建和有限测试：Zig 后端、DSP／流／布局与控制测试、假 ASIO 驱动和 C ABI、LIRA 独立编译通过；其中控制线程结合假 DLL 的生命周期项因未设置 NSP_ASIO_CONTROL_TEST_DLL 跳过 1。没有重跑全套实机或长期性能，也不把旧性能数字视为当前版本验收。

## 来源与公开范围

本分支在 NativeSpatialPipe 独立公开 main 历史上更新，不合并私有开发仓库 Git 历史。core-source-manifest.json 的 sourceRevision 是私有开发快照，逐文件 sourceSha256 为原始来源，sha256 为公开适配后的内容（仅归一化 CRLF）。公开路径和构建说明适配单独记录。

集成来源包括原 NSP 主线、hikari-taritsu（rmj）的 Spatial ASIO preview.13 / preview.14 两轮原始贡献、本地公共输入/渲染/设备/控制接线与后续输出池改进。原作者逐文件声明及模块 upstream notice 保留；来源仓库为 https://github.com/hikari-taritsu/spatial-asio 。私有主仓库中的原始贡献提交为 1fbfeac 与 ac09d468211af2ca12cbc00408db117a0830ba2a，已另存贡献分支；公开分支不承诺能用这些私有提交执行 git-show 对照。

导出：原生捕获与传输、Rust 接收/ROAR 适配、LIRA 与 ASIO 抽离模块、Zig 控制/输出与 DSP、FSA 原生集成、现有遥测/可视化、必要许可及可独立运行的测试源码。

不导出：录音与 profiler 原始流、研究档案/设备私有配置/凭据、完整下游工程、WPF/字体/安装器、C# 历史界面测试、依赖私有 Git baseline 或私有录音的迁移对照。开发证据链接若未随源码提供会改为文字说明，不伪造公开测试材料。

已接通有限播放路径，不代表发行已准备完成。二进制分发、完整依赖材料、长期设备验证、D15 和 Web API 控制服务仍后置。没有导入 Inferno 实现，也没有恢复已终止的 ASIO 内部延迟优化。

当前源代码可独立运行的离线测试包括 tools/asio-output/Test-DeviceModule.py（假驱动，显式 --compiler）、tools/zig-scheduler 的 DSP/流/布局测试及现有原生夹具。涉及真实驱动的测试应显式配置设备；不要把默认研究路径当成已存在资源。
