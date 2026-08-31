---
category: project
tags: SceneSQL,UBM,agent,autonomous-loop,architecture
---

SceneSQL/UBM Agent Autonomous Loop: 4-phase闭环 (自设计→自研发→自验证→自反馈)。MCP工具: batch_search(993个db批量SQL), cloud_capture_vis_frames(WebSocket抽帧), update_audit_conclusion(审计归档), save_mining_audit(挖掘报告)。约束层: MCP工具描述→Skill引导→Rule强制。关键Rule: 多图时必须写frame_analyses，仅抽帧不记录=任务未完成。
