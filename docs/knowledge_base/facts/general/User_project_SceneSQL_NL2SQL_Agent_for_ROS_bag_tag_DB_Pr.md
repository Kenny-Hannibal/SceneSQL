---
category: general
tags: handover
---

User project: SceneSQL (NL2SQL Agent for ROS bag tag DB). Project path: /data/var/workspace/projects/projects/SceneSQL/. NL query -> SQL with visualization. Architecture: (1) Schema split by domain (perception/localization/planning/scenario); (2) 3-layer injection: L1 domain directory, L2 sub-domain details (dynamic loader), L3 few-shot templates (RAG); (3) 3 MCP servers: Schema Introspection, SQL Validator+Executor, Query Refiner; (4) Weak LLM main, strong LLM fallback; (5) Phased build: P0 ✓ (2026-05-20), P1 Few-shot扩充, P2 MCP reinforcement, P3 Skill system, P4 ROS viz.
