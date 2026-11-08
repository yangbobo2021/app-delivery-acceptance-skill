# 依据与定义

使用本包的 [记录工具](../../scripts/records.py)。命令示例中的 `<脚本>` 为该文件的绝对路径，`<项目>` 为Git 仓库根目录。在应用位于子目录时，使用从 Git 根起算的路径。脚本使用 Python 3.10+，只处理本地记录。

## 选择操作

1. `python3 <脚本> --root <项目> current` 查看新版与旧版当前指针。新版写入 `.acceptance/native-v1/` 与 `.acceptance-local/native-v1/`。查询旧验收用 `show <AT-ID> --legacy`，只读展示已有定义与记录路径。新版查询用 `show <AT-ID>`。以实际返回为准。
2. 新建时用 `create <AT-ID> --request <用户实际请求> --reference <来源>`。用户给出 ID 时，按原样使用。例如「新建 AT-file」必须传入 `AT-file`。只有未指定 ID 时才分配新 ID。保留用户原话。消息 ID 不可取得时，使用「当前用户消息」这样的真实来源描述。仅创建时在取得身份后停止。已有新版验收可以用 `start <AT-ID>` 选中，当前已有另一活动验收时报告冲突。
3. Git 项目需要实际忽略 `.acceptance-local/native-v1/`。工具会核对。用户授权准备仓库忽略规则时可先补充该规则，其他情况提示具体阻塞。

## 冻结需求与场景

- 核对用户指定的文件、范围和实际文字。范围明确后，对每个选段调用 `source <文件> <起始行> <结束行>`，行号含两端。用命令返回的完整对象作为该项的 `source`。它包含选段原文和来源文件摘要。口头需求使用 `{"kind":"user","reference":"当前用户消息","text":"用户实际提出的需求原话"}`。
- 需求和场景应一致，场景明确关联已冻结或本次同时冻结的需求。遇到矛盾或范围歧义，给出具体出处并停止冻结依赖项。已有 ID 的定义需要改动时，取得明确授权后在该项记录实际授权原话。工具校验版本和引用，Agent 负责业务含义与授权判断。
- 用 `show` 核对当前版本，再通过下例的 heredoc 将完整请求传给工具。首次执行 `freeze` 就传入完整 JSON。新请求使用新 `request_id`，重试同一请求使用同一份输入。首次冻结的 `base_sha256` 为 `null`，后续用当前 `definitions.sha256`。

```sh
python3 "<脚本>" --root "<项目>" freeze "<AT-ID>" <<'JSON'
{
  "request_id": "freeze-r1",
  "base_sha256": null,
  "requirements": [
    {"id":"R1","source":{"kind":"user","reference":"当前用户消息","text":"用户实际提出的需求原话"}}
  ],
  "scenarios": []
}
JSON
```

场景项形如 `{"id":"SC1","requirements":["R1"],"source":{...}}`，其中 `source` 必须是实际取得的完整来源对象。指定修订时加 `revision_authorization`，保留具体授权。

收到 `frozen` 或 `already_frozen` 后，交付实际版本、位置、选定需求及场景，并更新计划。历史请求返回旧版本时如实说明适用性。若返回 `scenes_needing_revalidation`，指出受影响场景。冻结操作按用户停止点结束。
