# 独立 Reviewer

主 Agent 使用宿主的原生子 Agent 调用，启动一位新上下文的 Reviewer，等待实际返回。Codex 的 `spawn_agent` 必须明确传 `fork_turns: "none"`。交接只提供任务、文件路径和需要核对的事实，不提供主 Agent 的判断或完整会话。Reviewer 对项目资料和证据只读，只能写自己本轮的意见文件。交接真实验收 ID、冻结需求和场景、项目测试约定、实际用例 ID、源码及候选版本、对应执行或调试结果和证据。需要图像判断时让 Reviewer 实际查看相关图片。以原始材料判断，独立确认每个 TC 的覆盖边界和原始证据。

先为这一轮选定 `review-v1` 等唯一请求 ID。请 Reviewer 自己写入 `.acceptance-local/native-v1/<AT-ID>/review-inbox/<请求ID>.json`，并报告写入结果。使用真实 JSON，每个实际 TC 有一项：

```json
{"cases":{"<TC-ID>":{"status":"needs_revision","reason":"实际观察到的具体问题与来源","covers":["实际支持的检查项 ID"]}}}
```

可用结论为 `passed`、`needs_revision`、`product_defect`、`definition_conflict`、`insufficient_evidence`。`covers` 只列本尝试已声明且由证据支持的检查项。调试和正式执行分别判断。未看到独立意见、缺少实际图片或没有对应版本时，将待复审或阻塞如实记录。

判断创建与持久化场景时，核对候选用例是否用场景里的独立预期断言了首次写入值。典型的房间、时间、标题需要逐字段核对。若代码仅比较创建返回和随后查询返回的两个产品观察，即使两者都一致，也应把缺少独立预期判为 `needs_revision`，明确指出实际漏掉的字段和位置。

由主 Agent 从工具返回取得真实 Reviewer 会话 ID。计算目标 `result.json` 的实际 SHA-256，用 [操作工具](../../scripts/operations.py) 的 `review <AT-ID>` 记录。工具直接读取 Reviewer 自己写出的文件并保留原始字节，主 Agent 只传路径。

```json
{
  "request_id":"review-v1","scene":"SC1","definitions_sha256":"<当前摘要>",
  "target_operation":"debug","target":"<实际调试 ID>","target_sha256":"<实际调试结果摘要>",
  "reviewer_session":"<真实子 Agent ID>","review_file":".acceptance-local/native-v1/<AT-ID>/review-inbox/review-v1.json"
}
```

在 `debug` 中，若用例和证据有效、失败已确认来自产品，同时写 `"status":"product_defect","design_status":"valid"`。这允许同版测试进入正式执行。正式执行复审按实际产品结论使用 `product_defect`，省略 `design_status`。

目标操作可为 `debug`、`execute`、`history`、`manual`、`tdd-red` 或 `tdd-green`。`tdd-red` 只在实际日志证明测试因冻结需求对应的行为缺失而失败时使用 `expected_failure`。环境错误、测试错误和错误预期要分别说明，均不得授予 `expected_failure`。记录工具核对证据、实际用例集、版本和返回格式。主 Agent 依据宿主真实父子会话轨迹确认独立身份，按返回原样保存。
