# 独立 Reviewer

每次启动 Reviewer 前，先定稿完整交接范围，再更新 PLAN。

1. 交接逐项列出需求、场景、验证项、版本或实际执行记录及证据路径。同一用例有两次执行时分别列出两个记录。需求级复审列出各需求及关联场景。
2. 用同一批目标生成简短的 PLAN 条目，例如「独立复审两次播放续签结果」。单独调用 `update_plan`，标记该项为唯一进行中条目，等待返回。保留其余待办和已完成结论。
3. 将完整交接传给 `spawn_agent`。核对交接明细、PLAN 的业务范围和数量相符。目标有变时，重新核对并更新 PLAN 后再启动。各条需求的结论收到后分别勾选。

主 Agent 使用宿主的原生子 Agent 调用，启动一位新上下文的 Reviewer，等待实际返回。Codex 的 `spawn_agent` 必须明确传 `fork_turns: "none"`。交接只提供任务、文件路径和需要核对的事实，不提供主 Agent 的判断或完整会话。Reviewer 对项目资料和证据只读，只能写自己本轮的意见文件。交接真实验收 ID、冻结需求和场景、项目测试约定、实际用例 ID、源码及候选版本、对应执行或调试结果和证据。需要图像判断时让 Reviewer 实际查看相关图片。以原始材料判断，独立确认每个 TC 的覆盖边界和原始证据。

交接用例或观察收据时，为每个验证项列明它负责的检查项 ID、原文、预期和证据路径。已有用例参考匹配收据中该项的 `cases[ID].covers`，执行复审同时核对已审设计分配给该项的检查。自动项按实际断言及运行证据判断，人工项按本次操作的预期和原始观察判断。场景总义务用于核对整体覆盖。若文案内容由人工项验证，自动项按它负责的退出状态和输出契约判断。把跨验证项或整场景的缺口单独交给需求级复审。若分工与冻结要求冲突，指出具体出处。

执行复审和需求级复审对诊断文案采用同一判据。引用原始输出并核对其含义，含义等价即满足文案检查。若冻结要求明确指定语言或原文，额外核对该语言或原文。

先为这一轮选定 `review-v1` 等唯一请求 ID。先创建意见目录，向 Reviewer 提供仓库根路径和完整目标文件路径。请 Reviewer 自己写入 `.acceptance-local/native-v1/<AT-ID>/review-inbox/<请求ID>.json`，并报告写入结果。批量交接时每份目标收据预先分配独立请求 ID 和意见文件，文件名与请求 ID 一致。每份 JSON 只包含对应目标收据里的实际 TC。

```json
{"cases":{"<TC-ID>":{"status":"needs_revision","reason":"实际观察到的具体问题与来源","covers":["实际支持的检查项 ID"]}}}
```

可用结论为 `passed`、`needs_revision`、`product_defect`、`definition_conflict`、`insufficient_evidence`。优先从目标收据的 `case_obligations[TC-ID]` 读取分配项。`covers` 只列这些分配项中有证据支持的部分。其他检查的原始事实可以写在 `reason`，覆盖归属仍以分配项为准。新增用例在独立设计复审时根据实际断言确定检查项，正式执行沿用该设计结论。工具拒绝越界认领时，请原 Reviewer 用新请求 ID 出具修正意见，保留原文件。未看到独立意见、缺少实际图片或没有对应版本时，将待复审或阻塞如实记录。

判断创建与持久化场景时，核对候选用例是否用场景里的独立预期断言了首次写入值。典型的房间、时间、标题需要逐字段核对。若代码仅比较创建返回和随后查询返回的两个产品观察，即使两者都一致，也应把缺少独立预期判为 `needs_revision`，明确指出实际漏掉的字段和位置。

由主 Agent 从工具返回取得真实 Reviewer 会话 ID。以意见文件的文件名（去掉 `.json`）作为 `request_id`。用程序直接读取目标 `result.json` 字节计算 SHA-256 并填入请求，用 [操作工具](../../scripts/operations.py) 的 `review <AT-ID>` 记录。工具直接读取 Reviewer 自己写出的文件并保留原始字节，主 Agent 只传路径。

```json
{
  "request_id":"review-v1","scene":"SC1","definitions_sha256":"<当前摘要>",
  "target_operation":"debug","target":"<实际调试 ID>","target_sha256":"<实际调试结果摘要>",
  "reviewer_session":"<真实子 Agent ID>","review_file":".acceptance-local/native-v1/<AT-ID>/review-inbox/review-v1.json"
}
```

在 `debug` 中，若用例和证据有效、失败已确认来自产品，同时写 `"status":"product_defect","design_status":"valid"`。这允许同版测试进入正式执行。正式执行复审按实际产品结论使用 `product_defect`，省略 `design_status`。

目标操作可为 `debug`、`execute`、`history`、`manual`、`tdd-red` 或 `tdd-green`。`tdd-red` 只在实际日志证明测试因冻结需求对应的行为缺失而失败时使用 `expected_failure`。环境错误、测试错误和错误预期要分别说明，均不得授予 `expected_failure`。记录工具核对证据、实际用例集、版本和返回格式。主 Agent 依据宿主真实父子会话轨迹确认独立身份，按返回原样保存。

## 需求级交接

单独用 `spawn_agent` 加 `fork_turns: "none"` 启动需求级 Reviewer。一次可交接多条需求，用各自的请求 ID 和文件写意见。设计或执行 Reviewer 的上下文属于前一阶段。

按需求交接冻结原文、选定范围、相关场景、实际用例与人工记录、执行复审收据、同版证据入口及已知缺口。有源码且可能用于说明覆盖范围时，附实际候选文件路径和版本。Reviewer 核对每条需求的完整范围，说明实测覆盖到哪里。其余范围若由同版实现或其他证据支持，引用实际材料并说明为何能推出结论。证据不足时判 `blocked` 并指出缺口。确认行为与要求相违时判 `failed` 并指出证据。符合范围且证据充分时判 `passed`。
