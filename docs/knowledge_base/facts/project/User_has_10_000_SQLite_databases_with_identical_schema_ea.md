---
category: project
tags: rosbag-nl2sql,scenario-database,architecture
---

User has ~10,000+ SQLite databases with identical schema, each from autonomous driving ROS bag tagging. Industrial research (AWS, Microsoft AVOps, BMW) confirms: merge into Parquet + build metadata search index (Elasticsearch/FTS) + use DuckDB for queries. The "metadata index layer" for coarse search is the critical differentiator vs naive approaches.
