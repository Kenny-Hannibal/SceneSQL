---
category: project
tags: testing,discipline
---

SceneSQL强制测试纪律：①SQL生成后必须评估SQL质量 ②检查查询结果非空 ③与生产SQL模板比对。禁止只看"无报错"就当通过。测试报告路径: /data/var/workspace/projects/projects/SceneSQL/test_reports/，每次发版必写测试报告，E2E测试必须验证start_ts+end_ts列存在。
