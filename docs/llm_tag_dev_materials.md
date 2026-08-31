# LLM 标签开发交接手册（已迁移）

本手册已整合迁移至（唯一总入口）：

```
/data/var/workspace/projects/projects/docs/gac/LLM标签开发交接手册.md
```

内容：Schema 查看与更新、链路 A（SQL→策略→评测集）、链路 B（Spark 批量打标→转数据集）、
交付纪律与 18 条实证坑、fact_store 机器级嵌入说明、原环境参考值附录。

仓库内配套：
- `docs/scene_tag_sql_dev_guide.md` — 标签 SQL 开发深度知识库
- `docs/knowledge_base/` — fact_store 交接包（`bash setup.sh` 一键安装）
- `.agents/skills/` — llm-sql-writing / ubm-schema-sync / development-workflow
