# ASIO 模块依赖部署与启动修复

2026-10-23：布局更新后的实际 FSA 启动暴露了 ASIO 模块部署问题。失败会话 `<development-session>` 在 Hook 注入前退出，`host-stderr.txt` 为 `NSp Zig scheduler: FileNotFound`；外围随后报告 `Observer not ready` 和未确认正常停止。这些是主机启动失败的后果。

## 原因与修复

`NSP.AsioOutput.dll` 存在，但其隔离目录缺少 LLVM-MinGW 的 `libc++.dll` 与 `libunwind.dll`。原先的模块测试通过给子进程追加编译器 PATH、给 Python 添加 DLL 目录来加载，未覆盖 FSA 的独立运行环境。同时，Zig 的默认 `DynLib.open` 不保证从目标 DLL 旁搜索其依赖，单纯补文件仍不能作为可靠修复。

- 将上述两个匹配构建工具链的运行库部署到 ASIO DLL 同目录。
- ASIO 适配器使用 `native_library.zig`，将模块路径转为绝对路径，按 `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS` 加载。失败时输出模块路径与 Win32 错误码，不扩大全局 PATH，不修改 ASIO 实时处理或缓冲算法。
- `Test-DeviceModule.py` 的构建产物同时携带运行库，避免后续移动产物时再次丢失依赖。开源构建入口 `Build-Alpha.ps1` 本来已有对应复制步骤。
- 新增不打开设备的隔离加载回归：移除编译器 PATH，工作目录与 DLL 目录不同；缺依赖时明确失败，依赖齐全但使用旧加载方式仍失败，新加载方式可解析真实 ASIO ABI。

## 实测结果

隔离加载回归通过；输出控制线程测试六项通过，一项需显式提供假驱动 DLL 的测试跳过。使用安全退出与恢复证据清理旧失败会话后，替换默认调度器并重新启动 FSA，保留用户的播放 JSON、DTS:X HT、ROAR/OLR、7.1.4 及 DVS 接线。

新会话 `<development-session>` 实际进入 `running`，FSA 为 `nspCode=0`、`NSp frontend: verified`，无启动错误；会话保持开启等待音频。此结果确认实际设备模块及驱动启动成功，不代表已验证本轮扬声器听感。

上一轮七布局离线渲染和配置测试没有打开实际 ASIO 设备，不能替代上述运行环境检查。原始失败日志保留，没有通过删除状态文件绕过退出回验。

验证材料见 `experiments/asio-startup-fix-20261023`（开发参考未随公开源码提供）。
