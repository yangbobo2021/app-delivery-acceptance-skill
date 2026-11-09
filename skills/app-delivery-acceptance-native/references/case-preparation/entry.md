# 已有用例匹配

核对用户指定的当前验收、冻结场景与实际测试范围。读实际测试声明、输入、断言、辅助方法和跳过条件。按场景原文列出必要检查，把每个用例的实际断言与它支持的检查关联。说明检索了哪些入口，以及未取得哪些材料。标题相似、作者自述和已有绿灯不足以证明覆盖；跳过的用例单列。

以 [定义记录工具](../../scripts/records.py) 的 `show` 获取当前 `definitions.sha256`，再用 `source <文件> <首行> <末行>` 取得用例对应完整声明的实际来源对象。检查过但未采用的文件也留实际摘要。用 [操作工具](../../scripts/operations.py) 保存关联：

```sh
python3 "<操作工具绝对路径>" --root "<项目根>" match "<AT-ID>" <<'JSON'
{
  "request_id": "match-sc1",
  "scene": "SC1",
  "definitions_sha256": "<实际摘要>",
  "search_scope": "<实际检查过的范围及限制>",
  "inspected": {"tests/test_product.py":"<实际文件摘要>"},
  "obligations": [{"id":"state","quote":"<场景原文中的必要检查>"}],
  "cases": [{
    "selector":"<项目的实际声明定位>",
    "status":"active",
    "source": {"kind":"file","path":"tests/test_product.py","first":5,"last":16,"sha256":"<实际文件摘要>","text":"<source 返回的精确原文>"},
    "covers":{"state":"<选段内实际断言原文>"}
  }]
}
JSON
```

无匹配时使用空 `cases`，保留已检查的文件和缺口。工具只核验来源、版本及引用；断言支持哪些义务由当前 Agent 根据实际代码判断。已经有永久用例 ID 时沿用，其余使用声明定位得到的登记 ID，保留原测试文件。仅匹配任务以 `execution_status: not_executed` 结束，并明确「全覆盖／部分覆盖／无匹配」是对本次已列出的场景检查而言。

正式验收沿用 TDD 场景时，先查同场景 `tdd-green` 收据的 `cases` 和 `test_bindings`。确认实际声明与该用例一致后，匹配条目加 `"tdd_case":{"green":"<实际绿灯请求 ID>","case_id":"<原 TC ID>"}`。跨验收时同时填原 `acceptance_id`。有原始声明选段时复用同一选段。旧收据无选段时结合测试声明、命令和独立复审核对。工具验证冻结场景和测试文件字节，匹配结果保留原 TC ID 及收据引用。正式执行和复审使用匹配返回的 ID。

人工验证项也用 `match` 登记。条目加 `"kind":"manual"`，用实际检查步骤及预期所在的文件选段作为来源和覆盖依据。人工项与自动用例均保留原 ID，调试和正式自动执行只提交自动用例。
