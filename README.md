# 交付验收 Skill

项目目标是在 Codex 中按用户的验收目标记录需求基线、补齐测试场景、匹配或补齐用例、执行指定范围、独立复审并展示证据。主 Skill 根据实际结果协调各能力，通过 Codex 原生 PLAN 展示进度、分支与返修。

当前候选版支持三类任务。完成开发后可指定版本交付验收。需求已确认时可只交测试和预期失败，或在授权下完成测试驱动实现。维护期可只读盘点当前证据，也可仅复跑已授权的既有检查。原版 0.4.17 的固定提交是功能对照。新版支持创建验收、记录需求基线、维护测试场景、匹配及补齐用例、测试和覆盖准备、真实执行、独立复审、返修及结果展示。核心链路在 Codex 中用 LUNA 和 SOL 验证。生命周期与兼容范围见阶段验收。

- [架构与数据约定](docs/design.md)
- [功能对照清单](docs/parity.md)
- [实施顺序与阶段验收](docs/delivery-plan.md)
- [31 个入口的对照任务和验收结果约定](tests/baseline/behaviors.json)
- [定义与存储阶段验证结果](docs/stage-1-results.md)
- [匹配与环境准备阶段验证结果](docs/stage-2-results.md)
- [执行、报告与生命周期验证结果](docs/stage-3-to-6-results.md)

## 试用当前候选版

需要 Node.js 24.12+ 和能加载项目 Skill 的 Codex。运行 Skill 自身无需编译或安装 npm 依赖。把 [Skill 目录](skills/app-delivery-acceptance-native) 放到试用项目的 `.agents/skills/app-delivery-acceptance-native`。Git 项目先核对 `.acceptance-local/native-v1/` 已实际被忽略。启用原生计划工具的 Codex 会在执行中显示进度。

在该项目的 Codex 对话中输入：

```text
$app-delivery-acceptance-native 请在这个项目只新建验收 AT-demo。现在没有需求，创建后就停止。
```

随后可在同一个项目继续：

```text
$app-delivery-acceptance-native 在 AT-demo 将 R1 记录为需求基线。需求原话是「预约取消后应保留原预约的审计记录。」完成记录后停止。
```

新版写入 `.acceptance/native-v1/` 和 `.acceptance-local/native-v1/`。记录需求和场景时核对实际选段及来源。需求基线以用户确认的原文为准，测试场景可按需补充或修订，记录快照保留变更前后的内容供追溯。

从已有测试场景继续时，可提出「只匹配 SC1 的已有用例，交付缺口」或「为 SC1 补齐用例、正式执行并独立复审」。按项目约定准备实际测试和覆盖工具，Python 项目可通过该项目的解释器使用 coverage.py，其他项目可配置现有采集和验证入口。需要实时查看时可以打开本机只读查看器，也可以导出 HTML 和原始证据快照。

三类任务的隔离夹具、题面和判据在 [测试矩阵](tests/fixtures/three-modes/matrix.json)。使用 `tests/e2e_three_modes.py --help` 可在本机 Codex 中复验，`tests/audit_three_modes.py` 从本机原生轨迹和产物核对结果。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

将 `ACCEPTANCE_REFERENCE` 设为原版固定提交的 checkout 路径时，测试会另外核对 31 个入口，并用原版 Node 工具实际创建冻结记录和验证目录共存。CI 使用固定提交运行这两项测试。

TypeScript 迁移可用 [双向对照测试](tests/e2e_runtime_parity.py) 重验。取本仓库 Python 基线提交 `a5c9637` 中的 Skill 脚本目录作为 `--python-scripts`，使用安装了 coverage.py 的测试解释器。测试分别由两版生成收据，再让另一版读取、重放、导出并验证归档。

原生 Codex 检查需要已登录的 Codex 和已启用的计划工具。该检查在独立临时项目中运行，保留计划事件及工具结果。

```sh
python3 tests/e2e_definitions.py --model gpt-6-luna
```

可单独复跑场景和用例阶段。每个 `--stage` 都使用新的临时项目和 Codex 对话，从固定输入开始，不依赖上一个阶段生成的材料。

```sh
python3 tests/e2e_scenario_design.py --stage scenarios
python3 tests/e2e_scenario_design.py --stage design
python3 tests/e2e_scenario_design.py --stage implementation
python3 tests/e2e_scenario_design.py --stage review
```

| 阶段 | 固定输入 | 回归判据 |
| --- | --- | --- |
| `scenarios` | 需求与一条现有验收点 | 保留原始依据，补足遗漏边界，场景正文不混入账号或命令，完成范围核对后停止 |
| `design` | 已记录的场景和分组判据 | 逐项落实为可执行的自然语言检查，落盘后停止 |
| `implementation` | 已确认的用例设计、就绪的测试环境和有缺口的旧用例 | 匹配与调试绑定同版设计，只改授权测试。正常实现通过，三个独立缺陷副本分别被检出，调试后停止 |
| `review` | 一条合格场景及一条混入操作步骤的场景 | 独立复审能通过前者并指出后者应返回场景阶段 |

出现新问题时，把最小复现材料放在最先偏离的阶段，给它固定的上游输入，断言该阶段的实际收据、业务结果和停止点。脚本的校验或状态问题加到 `tests/test_records.py` 或 `tests/test_operations.py`，随 CI 运行。需要真实 Codex 判断的用例按阶段本机复跑，临时目录保留轨迹、收据和实际测试输出。

已有的用例匹配与测试环境检查也可分别通过 `tests/e2e_preparation.py --case match` 和 `--case environment` 运行。正式执行的复审门槛、证据失效和最终判定由 `tests/test_operations.py` 的固定收据测试覆盖。

已安装支持子进程采集的 coverage.py 时，可进一步验证完整业务：

```sh
python3 tests/e2e_workflow.py --case full --model gpt-6-luna --coverage-python /path/to/python
```
