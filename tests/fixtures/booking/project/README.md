# 会议室预约 CLI 测试项目

这是可独立运行的会议室预约测试项目，Python 3.10+，仅用标准库和本地 SQLite，不需网络或服务器。产品入口为 app/booking.py。

读取 requirements.md、scenarios.md 和 test-design.md 确认依据。测试目录为 tests/；SC1 的现有入口是 `python3 tests/check_s1.py`，SC2 的既有入口是 `python3 tests/check_s2.py`。命令、辅助代码及实际断言共同决定覆盖，文件名不代表已完整覆盖。

创建：
```
python3 app/booking.py --db /tmp/own-new-db.sqlite create --room A --start 2026-11-10T10:00:00 --end 2026-11-10T11:00:00 --title 原始周会
```
查询：
```
python3 app/booking.py --db /tmp/own-new-db.sqlite list
```

所有业务响应为一行 JSON，业务状态在 status。创建返回 booking，查询返回 bookings。预约字段为 id、room、start、end、title、created_at。命令正常结束只表示请求已处理；业务是否符合预期需要检查 JSON 和实际状态。

history/ 如存在，保存某次真实执行的输入快照、收据和原始输出，应保持原样；它不是当前测试版本的自动通过证明。准备调试与正式执行须区分。正式验收或复核结论应有独立 Reviewer 核对真实用例、基线与同次原始证据；不能把作者自检称为独立 Review。

requirements.md、app/、test-design.md、tests/check_s2.py 以及 SC2 均不在本轮修改范围。其他权限和停止点见本次用户请求。
