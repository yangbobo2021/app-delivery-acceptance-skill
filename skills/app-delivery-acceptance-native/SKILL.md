---
name: app-delivery-acceptance-native
description: 在 Codex 中创建交付验收、冻结需求与场景、匹配已有用例、验证测试运行和覆盖率准备。
---

# 交付验收

根据用户本次目标、范围、修改权限和停止点执行。先在 Codex 原生 `update_plan` 中列出当前请求所需的步骤。操作成功并取得真实结果后才勾选。遇到阻塞时记录原因，依赖该结果的事项保持待办。若宿主没有计划工具，说明当前会话的进度展示限制。

- [依据与定义](references/definitions/entry.md)负责创建、冻结和查询。仅创建、只冻结或仅查看时在该能力返回后停止。
- [用例准备](references/case-preparation/entry.md)负责匹配已有用例。在仅匹配任务中，交付实际匹配和缺口后停止。
- [测试与覆盖环境](references/case-preparation/environment.md)负责运行探测和采集探测。只准备环境时以实际就绪或阻塞收据为终点。

输出实际验收 ID、已完成工作、产物位置与版本，或具体阻塞原因。用户项目的定义来源决定冻结内容。
