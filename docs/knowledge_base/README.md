# knowledge_base — 标签开发 fact_store 交接包

前任（黄梓建）积累的 **64 条标签开发记忆**导出包。完整背景、原理、手动步骤、
验证方法、维护方式见**总交接手册 §0.4**：

```
/data/var/workspace/projects/projects/docs/gac/LLM标签开发交接手册.md
```

## 快速使用

```bash
cd <SceneSQL仓库>/docs/knowledge_base
bash setup.sh        # 一键：导入记忆 + 注入全局指令 + 安装全局 skill（幂等）
```

装完后你机器上的 Qoder 在**任何项目里**都知道这个记忆库（机器级生效）。

只想导入记忆、不动全局配置：

```bash
python3 import_fact_store.py            # 默认写入 /root/.hermes/memory_store.db
python3 import_fact_store.py --dry-run  # 先预览
```

验证：

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
print(list(c.execute('SELECT COUNT(*) FROM facts'))[0][0], '条 facts')
"
```

## 目录结构

```
knowledge_base/
├── README.md                # 本文件（薄入口，细节以总交接手册为准）
├── setup.sh                 # 一键安装
├── import_fact_store.py     # 导入脚本（自包含，可单独拷走）
└── facts/                   # 64 条记忆，按 category 分目录的 markdown
    ├── project/  infra/  general/  tool/  user_pref/
```
