# NSP 原生音频后端

只保留从 Spatial ASIO 抽离且供 NSP 使用的模块：LIRA C ABI/核心、ASIO 设备 C ABI/核心、必要公共头文件、系数/SDK及隔离测试。两套生产 DLL 分别构建，不链接旧完整引擎。

这里不含旧 `main.cpp`、捕获器、普通 PCM/WDM 补全、完整流 HT、Python 捕获管理、WPF、字体、安装器或发行工程。兼容布局适配仅供既有接口/测试使用，不进入两个生产 DLL。

- LIRA：`powershell -File tools/spatial-renderer/Build.ps1`。
- ASIO 独立模块及 C ABI：`python tools/asio-output/Test-DeviceModule.py --host-abi`（模拟驱动，不打开真实设备）。
- CMake：本目录提供 `NSP.SpatialRenderer`、`NSP.AsioOutput`，以及非默认的兼容布局静态目标。
- 公共宿主/池块验收：`python tools/desktop/Test-OutputPool.py`。

DLL 导出名和现有配置字段保留，目录迁移不改变声音、PCM 协议或驱动策略。来源和许可见 [LICENSING.md](LICENSING.md)；完整下游工程在工作区外保留，不作为主线构建依赖。`source-manifest.json` 保存抽离时逐文件哈希，后续修改以版本历史为准。

本模块的生产构建及运行均无 .NET/WPF 依赖。仓库另保留的 C# 客户端仅用于可选控制测试，不链接或加载进本模块，也不作为新 Web API host 的实现依赖。
