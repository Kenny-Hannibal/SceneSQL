---
category: project
tags: project,scenesql,nl2sql
---

SceneSQL 项目：NL2SQL Agent for ROS bag tag DB，项目路径 /data/var/workspace/projects/projects/SceneSQL。架构：FastAPI 后端 + React 前端 + gsbag SDK + Parquet/SQLite 双查询引擎。本机端口 30002，DSW 端口 30001。.venv 在项目根目录 (Python 3.10)。前端 CRA (React 19)，构建命令 npm run build。后端启动: source .env && .venv/bin/python -m uvicorn visualizer.backend.app.main:app --host 0.0.0.0 --port 30002。
