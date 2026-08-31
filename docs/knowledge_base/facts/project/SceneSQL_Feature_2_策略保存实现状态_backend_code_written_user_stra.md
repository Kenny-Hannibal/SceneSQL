---
category: project
tags: SceneSQL,feature2,strategy,blocked
---

SceneSQL Feature 2 策略保存实现状态: backend code written (user_strategy.py, strategies.py, concept_router.py patch, block_assembler.py patch, main.py registration), frontend UI written (AgentPanel.jsx save modal + strategy list). BLOCKED: import path issue — strategies.py in visualizer can't import user_strategy from agent core. Fix: copy user_strategy.py to visualizer/backend/app/core/.
