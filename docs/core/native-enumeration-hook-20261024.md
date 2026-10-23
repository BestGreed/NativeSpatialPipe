# 额外端点枚举迁移到原生 MinHook

2026-10-24，替换 `tools/pipeline-init/manager.py` 的 Frida 注入路径，移除产品用 `agent.js`。不改变七种模式、两组额外枚举开关、FSA 活跃内存标签或 HT/EDID 显式入口；不修改 PCM 捕获、渲染、DSP 或 ASIO。

## 实现边界

- `Enum-Hook.c/.h`：C DLL，在 AudioSrv 原三处函数返回成功后，只修改指定端点和指定格式的结果；字段偏移和有效期与原脚本一致。原始 HRESULT、非目标结果和 LastError 保留。
- `native_enum.py`：Python 控制适配器，通过 `nsp_enum_control.exe` 调用 DLL 导出；不导入 Frida，不加载脚本。
- `Native-Hook-Control.c`：与 PCM 捕获复用一份原生 LoadLibrary/远程导出调用源码，以 `NSP_ENUMERATION_CONTROL` 构建枚举控制器；进程目标校验分开，枚举只接受 System32 svchost 内的 System32 AudioSrv 或明确测试宿主。
- MinHook 固定为 `c3fcafdc10146beb5919319d0683e44e3c30d537`。Python 和控制器均核对原 AudioSrv SHA-256；DLL 再核对三个函数序言。系统更新哈希不匹配时明确拒绝，不扫描猜测新地址。
- 原生 watchdog 监听监督进程句柄及五秒租约。停止时关闭结果修改入口、禁用三处 Hook、等待在途结果修改并核对原始 32 字节。保留禁用 trampoline 与固定的小 DLL，避免已进入原函数的线程返回已释放代码。
- 异常退出后的审计只使用 ReadProcessMemory；仍核对服务 PID 创建时间，不把旧字节写回任何新进程。旧脚本产生的三处原字节证据也可只读审计，无需恢复 Frida。

仍有两类拦截位置：AudioSrv 枚举与 audiodg 捕获。这是两种职责和目标进程，不是两套注入运行时。二者现在统一为原生控制器和 MinHook，准备监督仍在控制平面，不进入逐包音频链。

## 构建与验证

```powershell
powershell -File tools/pipeline-init/Build-EnumHook.ps1 -TestFixtures
python -m unittest discover -s tools/pipeline-init -p 'test_*.py' -v
```

隔离测试共 54 项通过（其中新增 7 项原生 Hook 测试），覆盖三处真实 MinHook 拦截的字段结果、格式/端点范围、原函数失败、短数组、反复启停及逐字节恢复，以及跨进程控制、租约过期和监督进程被终止后的自动恢复。EDID 与现有内存标签测试仍通过。

显式实机入口 `Validate-NativeEnumeration.py --output <JSON>` 要求管理员权限，拒绝已有枚举常驻。只开启两组枚举、查询四个额外模式、撤销并回验；不切换模式、不启动捕获、不写 EDID、不重启音频服务。

本轮实机结果：`build/native-enumeration-20261024/live.json`，通过。AudioSrv PID 4184 上原生 Hook ready、共享内存标签存活，四个模式查询均支持；正常停止后三处原字节全部恢复，独立 ReadProcessMemory 审计再次确认三处恢复，前后 active/default 都是 DTS:X HT，支持读数保持一致。测试结束无活跃枚举常驻。这台设备在测试前就已支持四个模式，因此该实机验证不能作为“不支持→支持”的负正对照；限定字段修改由原生隔离夹具验证。
