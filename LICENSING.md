# 许可范围

根目录 LICENSE 的 Apache-2.0 适用于 NSp 原创代码和文档，不覆盖另有声明的来源代码。本次只同步开发源码，不发布二进制组合包，也不统一改写逐文件授权。

| 范围 | 许可与声明 |
|---|---|
| NSp 原创核心、公共转换/输出及控制代码 | Apache-2.0 |
| LIRA 与下游公共支持代码 | 保留原 Apache-2.0 与作者声明，见 modules/audio-backends/LICENSING.md |
| ASIO 设备集成、设备配置、布局兼容与 C ABI，以及附带 SDK 头文件 | GPL-3.0-only 范围，见模块逐文件声明及 UPSTREAM-LICENSE、third_party/asio/LICENSE.txt |
| FSA 集成源码 | tools/fsa-integration/LICENSE（Unlicense）；图标另有独立声明 |
| ROAR、MinHook、libsamplerate、Cargo 依赖与模型素材 | 各自许可及来源声明，见 THIRD_PARTY_NOTICES.md |

含 GPL ASIO 部分的组合程序按 GPL-3.0-only 对待；其中原 Apache 文件仍保留原授权。不能将整个 alpha-dev 描述成纯 Apache。运行时动态加载的形式不作为免除 GPL 义务的依据。发行构建、完整依赖清单与对应源码材料留待发行节点整理。

未导入 Inferno 网络实现；其未来许可评估不等于本分支已增加该依赖。旧完整 SpatialASIO 工程、WPF、字体和安装器不在当前源码树，历史 Git 中仍保留原始提交。
