# 结果展示

用 [结果工具](../../scripts/results.py) 读取已有证据。查看是只读操作；报告将实际快照及所引用的证据字节放到同一导出目录。

```sh
python3 "<结果工具>" --root "<项目>" view "<AT-ID>"
python3 "<结果工具>" --root "<项目>" report "<AT-ID>" <<'JSON'
{"request_id":"report-sc1","scene_ids":["SC1"]}
JSON
```

查看旧项目的原始定义可用 [依据与定义](../definitions/entry.md) 的只读入口。报告链接指向实际生成的 `report.html` 与 `snapshot.json`。区分未执行、待独立复审、原证据缺失、旧版、产品缺陷、需修订和通过。只跑部分用例时交付该范围；场景通过需要全部必要义务、当前版本和对应独立证据。只查看或报告任务结束于交付链接。

用户要在浏览器持续查看时，可运行 `results.py --root <项目> serve <AT-ID>`，给出该进程实际返回的本机 URL。服务逐次读取当前资料，并核验附件。需要可分享的离线副本时使用报告导出。
