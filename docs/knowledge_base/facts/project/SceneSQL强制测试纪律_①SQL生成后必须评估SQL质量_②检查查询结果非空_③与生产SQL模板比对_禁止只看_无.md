---
category: project
tags: testing,discipline
---

> [交接注] 本条为前任原环境(2026-08-31)快照：服务地址/凭证/绝对路径均为历史值，操作时以你自己的 DSW 部署和 .env 为准（映射见交接手册附录A）。

SceneSQL强制测试纪律：①SQL生成后必须评估SQL质量 ②检查查询结果非空 ③与生产SQL模板比对。禁止只看"无报错"就当通过。测试报告路径: <SceneSQL仓库>/test_reports/，每次发版必写测试报告，E2E测试必须验证start_ts+end_ts列存在。
