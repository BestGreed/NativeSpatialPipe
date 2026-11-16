# 原生捕获模块

正式 C 实现与构建入口集中在本目录：Native-Hook、Native-Hook-Control、Native-Capture、Native-Send。Zig 使用本目录的 Native-Hook.h，枚举 Hook 复用同一控制器源码。

原 endpoint-probe 的 Python 兼容入口、分析和自有测试夹具仍留在原目录。构建脚本通过明确路径引用夹具；产物目录、DLL 名称、ABI 和默认构建参数保持原值。构建不会自动注入系统进程。
