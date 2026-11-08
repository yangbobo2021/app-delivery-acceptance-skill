# 交付验收 Skill

项目目标是在 Codex 中按用户的验收目标冻结依据、匹配或补齐用例、执行指定范围、独立复审并展示证据。主 Skill 根据实际结果协调各能力，通过 Codex 原生 PLAN 展示进度、分支与返修。

当前是**交付验收候选版**。原版 0.4.17 的固定提交是功能对照。新版支持创建与冻结、匹配及补齐用例、测试和覆盖准备、真实执行、独立复审、返修及结果展示。核心链路在 Codex 中用 LUNA 和 SOL 验证。生命周期与兼容范围见阶段验收。

- [架构与数据约定](docs/design.md)
- [功能对照清单](docs/parity.md)
- [实施顺序与阶段验收](docs/delivery-plan.md)
- [31 个入口的对照任务和验收结果约定](tests/baseline/behaviors.json)
- [定义与存储阶段验证结果](docs/stage-1-results.md)
- [匹配与环境准备阶段验证结果](docs/stage-2-results.md)
- [执行、报告与生命周期验证结果](docs/stage-3-to-6-results.md)

## 试用当前候选版

需要 Python 3.10+ 和能加载项目 Skill 的 Codex。把 [Skill 目录](skills/app-delivery-acceptance-native) 放到试用项目的 `.agents/skills/app-delivery-acceptance-native`。Git 项目先核对 `.acceptance-local/native-v1/` 已实际被忽略。启用原生计划工具的 Codex 会在执行中显示进度。

在该项目的 Codex 对话中输入：

```text
$app-delivery-acceptance-native 请在这个项目只新建验收 AT-demo。现在没有需求，创建后就停止。
```

随后可在同一个项目继续：

```text
$app-delivery-acceptance-native 在 AT-demo 冻结 R1。需求原话是「预约取消后应保留原预约的审计记录。」只冻结这条需求，然后停止。
```

新版写入 `.acceptance/native-v1/` 和 `.acceptance-local/native-v1/`。文件冻结按实际选段、来源摘要和当前版本核对后发布。用户明确授权的定义修改创建新版本并保留旧版。

从已冻结场景继续时，可提出「只匹配 SC1 的已有用例，交付缺口」或「为 SC1 补齐用例、正式执行并独立复审」。按项目约定准备实际测试和覆盖工具，Python 项目可使用 coverage.py，其他项目可配置现有采集和验证入口。需要实时查看时可以打开本机只读查看器，也可以导出 HTML 和原始证据快照。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

将 `ACCEPTANCE_REFERENCE` 设为原版固定提交的 checkout 路径时，测试会另外核对 31 个入口，并用原版 Node 工具实际创建冻结记录和验证目录共存。CI 使用固定提交运行这两项测试。

原生 Codex 检查需要已登录的 Codex 和已启用的计划工具。该检查在独立临时项目中运行，保留计划事件及工具结果。

```sh
python3 tests/e2e_definitions.py --model gpt-6-luna
```

已安装支持子进程采集的 coverage.py 时，可进一步验证完整业务：

```sh
python3 tests/e2e_workflow.py --case full --model gpt-6-luna --coverage-python /path/to/python
```
