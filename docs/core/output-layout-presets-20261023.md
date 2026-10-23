# 输出布局预设与 ROAR 6DoF 插槽

2026-10-23：已接入当前 `alpha-dev` 工作区。布局选择属于渲染输出配置，与捕获端点的 12 声道 bed 和动态对象数量无关。

## 入口与生效范围

Web 前端的「配置 → 播放与公共 DSP → 扬声器布局」提供以下七项，也可直接修改播放 JSON 的 `outputLayout`。省略字段仍为 `7.1.4`，配置版本保持兼容。保存不自动应用，下次启动 NSp 会话时读取。

| 简写 | JSON 值 | 输出声道数 |
|---|---|---:|
| 512 | `5.1.2` | 8 |
| 514 | `5.1.4` | 10 |
| 712 | `7.1.2` | 10 |
| 714 | `7.1.4` | 12 |
| 716 | `7.1.6` | 14 |
| 914 | `9.1.4` | 14 |
| 916 | `9.1.6` | 16 |

七项均适用于 `renderer: "roar", mode: "olr"` 和 `renderer: "lira", mode: null`。选择会贯通实际渲染器、Rust 输出池、公共 DSP、输出设备路由和遥测；不是仅修改界面标签。设备输出声道必须足够，既有 12 路 DVS 网络订阅不会自动增加到 14/16 路。

## 声道顺序与接线

唯一配置表为 [`contracts/output-layouts.json`](../../contracts/output-layouts.json)，通过 `tools/Generate-OutputLayouts.py` 生成各语言的表。两个后端使用相同输出顺序；算法和房间行为仍各自保留。

| 布局 | 渲染器标准输出顺序 |
|---|---|
| 5.1.2 | L R C LFE Ls Rs Ltf Rtf |
| 5.1.4 | L R C LFE Ls Rs Ltf Rtf Ltr Rtr |
| 7.1.2 | L R C LFE Ls Rs Lrs Rrs Ltf Rtf |
| 7.1.4 | L R C LFE Ls Rs Lrs Rrs Ltf Rtf Ltr Rtr |
| 7.1.6 | L R C LFE Ls Rs Lrs Rrs Ltf Rtf Ltr Rtr Ltm Rtm |
| 9.1.4 | Lw Rw C LFE Lrs Rrs L R Ls Rs Ltf Rtf Ltr Rtr |
| 9.1.6 | Lw Rw C LFE Lrs Rrs L R Ls Rs Ltf Rtf Ltr Rtr Ltm Rtm |

9.x 沿用 ROAR 原生 9.1.6 的顺序。两顶声道预设沿用 ROAR 的前顶声道对，不改成中顶声道。物理接线与上表分开，由 `routing.physicalChannels` 指定；界面显示从 1 开始，JSON 索引从 0 开始。

`outputProcessing.trim`、`delayFrames`、`routing.physicalChannels` 及自定义源排列的长度必须与所选布局一致；省略校准数组时使用中性值。DSP 始终按渲染器标准声道顺序处理，之后才映射设备输出。

在表单切换布局时，按声道名称保留校准和既有设备接线，新增声道使用中性校准并分配未占用输出；切换后排列回到标准顺序。直接编辑完整 JSON 不会被偷偷重排，长度不符会拒绝保存。旧 `dvs-speakers` 预设仅适用于 7.1.4，其他布局使用标准或自定义路由。

## ROAR 扩展与预留插槽

所钉住的 ROAR 版本原生没有 7.1.6、9.1.4。本次通过独立的 `olr-output-layouts.patch` 增加实际目标扬声器几何；对象直接渲染至该几何。NSP 固定 7.1.4 bed 按同名扬声器映射到新增布局，不先渲染 9.1.6 再丢弃声道。扩展仅保证 NSP 当前 bed 与对象输入，不宣称覆盖上游的所有 IAMF/HOA 输入组合。

额外预留 `roar-custom-6dof`，以及以下 ROAR 专属配置元数据：

```json
"roarCustomLayout": {
  "version": 1,
  "measurementFile": null
}
```

`measurementFile` 非空时需为绝对路径。目前仅保存槽位元数据，不读取测量、不应用几何、不启动实时追踪；界面中的该布局不可选，运行时主动拒绝选择它。后续需单独定义测量合同、坐标系、校准和布局更新边界，不能把预留槽位视为已实现 6DoF。

## 验证与交付边界

- ROAR/LIRA × 七布局 × bed/对象共 28 组离线实际渲染通过，覆盖输出尺寸、有效音频、遥测包和输出池归还；另有四组旧 7.1.4 音频逐字节兼容对照通过。
- Zig 配置 10 项、输出池交接 7 项通过；七布局遥测的快照、历史与协议校验通过。原有遥测 33 项和协议 15 项回归通过。
- 配置页面测试覆盖七种布局切换、按声道名称迁移、物理路由防冲突以及预留插槽；Web API 14 项检查通过，含七布局保存回读和预留布局拒绝。
- 已替换当前本机默认调度器、ROAR 桥接库与 LIRA 库，并重新启动 FSA；未改用户当前布局、驱动或网络订阅，未自动启动播放。

本轮未进行七种布局的扬声器实播、听感验证或新的延迟压测；不据此宣称 14/16 路物理网络已连通。诊断原始文件名 `roar-714.f32` 为兼容旧工具暂保留；非 7.1.4 时必须依报告的 `outputLayout`/`outputChannels` 解读，不能根据文件名推断尺寸。

验证记录见 `experiments/output-layouts-20261023`（开发参考未随公开源码提供）。
