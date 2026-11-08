# 测试驱动开发

确认需求来源与修改权限。用[定义能力](definitions/entry.md)冻结原文与本次测试场景。准备真实运行环境，探测失败先保存日志，按授权修复并重新探测。用项目测试规范编写一个能区分缺失行为的测试。此时保持待实现的产品文件字节。

用 [operations.py](../scripts/operations.py) 的 `tdd-red <AT-ID>` 跑实际测试。以下字段通过 stdin 传入，路径相对 Git 根。

```json
{"request_id":"red-1","scene":"SC1","definitions_sha256":"实际定义摘要","runtime":"实际就绪收据","case_id":"TC-new","product_files":["app.py"],"test_files":["tests/check.py"],"command":["python3","tests/check.py"],"coverage_policy":"off","coverage_basis":"用户实际授权"}
```

覆盖采集要求沿用[环境能力](case-preparation/environment.md)。若要求采集，用真实 `coverage` 收据代替关闭字段。先看原始输出确认失败发生在业务断言。请[独立 Reviewer](execution/reviewer.md)用 `review` 记录 `target_operation: "tdd-red"`，仅对可信的预期失败给 `expected_failure`。TDD 记录不使用用例匹配的检查项 ID。两次 Reviewer 的 JSON 都省略 `covers` 或填写空数组，实际断言写进 `reason`。只交测试时以红灯意见、证据、用例与运行方式为终点。

用户授权实现时，取得上述意见后修改目标产品文件。保留同一测试内容和运行命令，用新版运行条件取得就绪收据。调用 `tdd-green <AT-ID>`，输入 `request_id`、`scene`、`definitions_sha256`、`runtime`、`red_review`、`regression_command` 及同样适用的覆盖字段。工具从红灯记录复用原测试与命令，核对测试内容并执行项目回归。测试需要实质修订时，先在原产品版本上重新取得可复核红灯。

绿灯与回归均通过后，交给新上下文 Reviewer，`target_operation` 用 `tdd-green`。交付代码改动、红灯、绿灯、回归及独立意见的路径。PLAN 中只在各项真实结果落盘后勾选。
