# 测试与覆盖环境

先确认项目实际测试语言、项目规定的依赖、产品启动路径和用户权限。为就绪探测选一个有代表性且范围受控的命令，绑定影响运行的产品、启动脚本和配置。环境准备的观察与正式验收记录分别交付。

用 [操作工具](../../scripts/operations.mts) 执行 `runtime <AT-ID>`，通过 stdin 提交：

```json
{"request_id":"runtime-1","definitions_sha256":"<当前记录快照摘要>","files":["app/booking.py","tests/check_s1.py"],"command":["<实际 Python>","tests/check_s1.py"],"timeout":60}
```

只有真实命令正常结束且绑定文件仍适用，才可使用 `ready` 收据。核对 stdout 中的业务或运行信号，退出 0 只能表示命令正常返回。

隔离候选工作区使用已完成的 `rebind` 请求 ID。运行命令从实际工作区执行，文件绑定、覆盖目录和产品观察位置使用被测项目根的相对路径。已有用例候选修改授权时，修改指定工作区的测试文件。

对 Python 项目，`coverage <AT-ID>` 使用项目确认的解释器及 coverage.py。使用支持 `patch = subprocess` 的安装版本。必要的依赖准备须符合项目约定和权限。用真实会启动被测进程的探测命令，指定该进程中的应命中源码行，避免只观察测试父进程。

```json
{"request_id":"coverage-1","definitions_sha256":"<当前记录快照摘要>","runtime":"runtime-1","source_roots":["app"],"command":["<含 coverage.py 的 Python>","tests/check_s1.py"],"observe":{"path":"app/booking.py","line":35},"timeout":60}
```

记录工具实际运行 coverage.py，合并其进程数据并保存原生 JSON。指定产品行实际命中、分支采集已启用且探测正常完成时才交付就绪。对未支持的工具链，保留项目发现和实际缺口。用户要求覆盖时，用例生成与正式执行等待就绪收据。

其他语言可使用项目现有的成熟工具链。提交 `adapter`，包含应绑定的 `files`、实际工具版本命令 `version`、采集启动前缀 `prefix`、原生报告相对路径 `report` 和项目验证命令 `verify`。在参数数组中以独立参数使用 `{output}`、`{report}`、`{product}`、`{line}`。工具在新目录调用前缀加真实业务 argv，读取实际报告，再交项目验证命令调用成熟工具的数据 API。验证命令输出 `{"observed":true,"metrics":{...}}`；命中和统计来自该工具原生数据。源码、适配文件和工具版本都参与后续核对。
