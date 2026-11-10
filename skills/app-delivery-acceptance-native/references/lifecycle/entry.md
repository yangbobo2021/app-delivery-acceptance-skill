# 验收生命周期

以用户所需的操作和停止点为准。[需求基线与测试场景](../definitions/entry.md)、[用例准备](../case-preparation/entry.md)和[执行](../execution/entry.md)各自处理本领域。以下操作都使用实际验收 ID 与当前项目根。

## 分析、设计与版本

用 [操作工具](../../scripts/operations.mts) 通过 stdin 接收 JSON。每项带唯一 `request_id` 和 `definitions_sha256`，指定场景时加 `scene`。先记录用户确认的需求基线。需新建场景时，根据基线和项目约定编写候选文件，保留原义务及相邻场景。仅设计时按用户要求交付候选。任务包含保存场景时，用记录工具的 `source` 和兼容命令 `freeze` 保存变更。

| 操作 | 实际输入及停止点 |
| --- | --- |
| `analyze` | `requirement_ids` 及基于原文写成的 `analysis`。保留组别、必要条件、范围及待确认项。只交分析。 |
| `assess-scenarios` | 实际 `scene`、独立 `reviewer_session` 和 `review_file`。让 Reviewer 按原样保存 `{"scenes":{"SC1":{"status":"passed","reason":"实际依据"}}}` 到约定意见路径。 |
| `reverse` | 本机已有的提交或 PR 分支 `revision`。工具保存实际 Git 差异，Agent 据此给需求候选和待确认问题。将候选纳入需求基线前，取得用户确认。 |
| `change-plan` | `previous_sha256`、`intent`（`follow_history`、`complete_change_acceptance`、`design_only`）。交付受影响范围和拟复验项。 |
| `rebind` | 用户已选 checkout 的实际完整 `revision` 和需绑定的 `files`。默认核对当前 HEAD。用户授权建立隔离候选时，传 `workspace: true`，工具创建并核对新的 Git worktree，保留原工作区和旧记录。将实际 `rebind` ID 交给运行准备。 |
| `project` | 真实的 `runtime`、`coverage` 或 `coverage_basis`、可选的 `rebind`，以及实际 `test_roots` 和 `guide`。基于已验证环境写运行指南。 |
| `source-status` | `reference` 如 `requirements:R1`、`source_status`、实际观察 `basis`。区分 `read_failed` 和 `deletion_confirmed`，保留原义务。 |

## 手工观察、交付判断与知识

| 操作 | 实际输入及停止点 |
| --- | --- |
| `manual` | `scene`、实际 `product_version`，可选 `match`；`cases` 是 TC ID 到实际动作数组。每项含 `action/expected/observed/performed_by/performed_at/receipt_reference/outcome/evidence`。最后一项是实际证据路径到 SHA-256 的映射。记录图像后请独立 Reviewer 实际查看。 |
| `delivery-judge` | 完整选定 `requirement_ids`，独立 `reviewer_session` 和意见文件 `review_file`。按[启动 Reviewer 前更新 PLAN](../execution/reviewer.md)，并分别保留需求结论条目。Reviewer 基于 [结果视图](../results/entry.md) 和原始证据返回 `{"status":"passed","reason":"实际依据"}`，也可判 `blocked` 或 `failed`。缺同版证据时交付实际限制。 |
| `risk` | 选定 `scene`、实际非通过复审 `finding/finding_sha256`、用户接受原话 `user_quote/source_reference`、`remaining_impact`。保留原判定。 |
| `knowledge` | 指定真实 `validated_receipt`，以及从它提炼的 `files` 路径到 Markdown 正文。附可移植的条件、操作和限制，并从 `INDEX.md` 链接全部文档。 |
| `backfill` | 用户允许的 `authorized_paths` 及 `files` 候选映射，每项含 `base_sha256` 和拟提交 `text`。返回差异与候选文件供核对。 |
| `coverage-recompute` | `reports` 数组，每项含正式执行 `receipt` 与原始报告 `path`。用兼容的原采集工具和数据重算，保留原报告和业务结果。 |

手工观察的 `outcome` 为 `observed_success`、`observed_failure` 或 `uncertain`。只记确实发生的行为和观察。未知或无图像不授予对应通过。

## 归档、恢复、结束与清理

用 [生命周期工具](../../scripts/lifecycle.mts) 的对应操作。每项通过 stdin 提交实际请求。

- `archive` 接收 `request_id` 和明确的 `files`（项目相对路径到 SHA-256）。仅选定本验收可共享的结果和附件，交付保留原字节的 ZIP 及清单。
- `recover` 接收 `operation`、`attempt`、`action`。先用 `inspect`。已存在有效结果时复用。处理中断时核对进程和外部副作用；实际核对后才可用 `reconcile` 或 `supersede`，传 `external_effects` 的实际 `status`（`none_proved` 或 `reconciled`）与 `basis`。仍活动或没有可靠阶段结果时说明具体阻塞。新执行使用新请求 ID。
- `finish` 接收 `request_id`，从结果视图读取当前有效的逐需求判定，保留通过、失败和受阻。缺有效判定时，只有提供用户明确允许结束的 `allow_unfinished` 原话才能收尾。旧判定缺少场景证据绑定时保留原收据，重新取得当前判定后再收尾。先保存快照，再清除匹配的当前指针。
- `cleanup` 接收 `request_id`、实际 `authorization`、已验证的 `archive/archive_sha256` 和允许清理的本机 `files` 路径列表。工具先核对已归档字节、非活动状态和范围，再清理指定文件。按用户要求停止。
