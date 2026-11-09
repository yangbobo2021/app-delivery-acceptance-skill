# 测试驱动开发

确认需求来源与修改权限。按[场景范围](definitions/entry.md#交付场景推导)先冻结已有需求和验收点，再逐需求补齐缺失场景并记录范围。以需求组织 TDD。先取得运行环境就绪收据，再准备该需求下的验证项。在实现前保存各相关缺失行为的红灯或基线。然后统筹实现与回归，按场景和用例分别保存证据。

对行为缺失的场景完成红灯、独立复审、实现、同版绿灯与独立复审。若现有实现已满足场景，先匹配用例，再用 `debug` 和 `"purpose":"tdd-baseline"` 单独记录该场景的基线结果。全量回归作为附加证据。产品版本变化后，在最终版本核对受影响场景。按[截图与落盘核对](results/entry.md#截图与落盘核对)记录验证项的实际结果，核对当前需求下的所有必需验证项，再形成该需求的结论。

只随全量回归运行的场景也要落盘对应的最终用例。用[用例匹配](case-preparation/entry.md)将实际测试声明与场景关联。已绑定红灯或绿灯收据的用例沿用其 ID。回归套件收据只证明该套件的结果，逐用例执行列以对应的单独收据为准。

用 [operations.py](../scripts/operations.py) 的 `tdd-red <AT-ID>` 跑实际测试。以下字段通过 stdin 传入，路径相对 Git 根。

```json
{"request_id":"red-1","scene":"SC1","definitions_sha256":"实际定义摘要","runtime":"实际就绪收据","case_id":"TC-new","product_files":["app.py"],"test_files":["tests/check.py"],"command":["python3","tests/check.py"],"coverage_policy":"off","coverage_basis":"用户实际授权"}
```

取得新测试实际声明后，把 `source` 返回的选段加进红灯请求的 `source`。测试包含多个声明时按所代表的验证项选择，后续正式匹配沿用该身份和来源。

覆盖采集要求沿用[环境能力](case-preparation/environment.md)。若要求采集，用真实 `coverage` 收据代替关闭字段。先看原始输出确认失败发生在业务断言。请[独立 Reviewer](execution/reviewer.md)用 `review` 记录 `target_operation: "tdd-red"`，仅对可信的预期失败给 `expected_failure`。TDD 记录不使用用例匹配的检查项 ID。两次 Reviewer 的 JSON 都省略 `covers` 或填写空数组，实际断言写进 `reason`。只交测试时以红灯意见、证据、用例与运行方式为终点。

用户授权实现时，取得上述意见后修改目标产品文件。保留同一测试内容和运行命令，用新版运行条件取得就绪收据。调用 `tdd-green <AT-ID>`，输入 `request_id`、`scene`、`definitions_sha256`、`runtime`、`red_review`、`regression_command` 及同样适用的覆盖字段。工具从红灯记录复用原测试与命令，核对测试内容并执行项目回归。测试需要实质修订时，先在原产品版本上重新取得可复核红灯。

绿灯与回归均通过后，交给新上下文 Reviewer，`target_operation` 用 `tdd-green`。交付代码改动、红灯、绿灯、回归及独立意见的路径。PLAN 中只在各项真实结果落盘后勾选。
