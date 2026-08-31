---
category: infra
tags: qoder
---

[src=qoder:SceneSQL部署目标-大写DSW] SceneSQL部署目标-大写DSW
SceneSQL 部署目标 — 统一用大写 DSW（小写 dsw 已废弃）

**结论（重要）：SceneSQL 及本项目所有部署/E2E 验证，一律用大写 `DSW`；小写 `dsw` 已废弃不用。**

| ssh 别名 | HostName | Port | 状态 |
|---------|----------|------|------|
| `DSW`（大写） | `8.130.209.216` | 1025 | ✅ 唯一部署/验证目标 |
| `dsw`（小写） | `8.130.175.37` | 1021 | ❌ 已废弃，不再部署、不再验证 |

- 两台是**不同机器**，ssh 别名大小写敏感，千万别混。
- 大写 DSW 仓库路径：`/root/data/text2sql`。
- 部署命令：`ssh DSW "cd /root/data/text2sql && git pull --ff-only && bash visualizer/deploy.sh -f"`（deploy.sh 会在 DSW 上 npm build 前端并重启 backend，端口 30001）。
- DataMining 同步所需 `DM_ACCESS_TOKEN` 在 DSW 的 `/root/data/text2sql/.env`（值以 `=` 结尾，取值用 `cut -d= -f2-`）。
- 用户验证入口在大写 DSW；改了前端后提醒用户强刷（Ctrl+Shift+R）绕过浏览器缓存。

背景：2026-08-04 曾误把改动只部署到小写 dsw，导致用户在大写 DSW 上看不到「通过/不通过」按钮；确认废弃小写 dsw 后已切换并全链路复验通过。
