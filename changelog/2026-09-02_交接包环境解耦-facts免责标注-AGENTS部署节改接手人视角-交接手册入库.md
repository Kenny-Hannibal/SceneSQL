# 2026-09-02 交接包环境解耦：facts 免责标注 + AGENTS.md 部署节改接手人视角 + 交接手册入库

## 背景

原作者（黄梓建）离职交接。原作者链路 = 本机 xct 用 Qoder + SSH 远程操作大写 DSW（8.130.209.216:1025）的 SceneSQL；
接手人链路 = 自有 DSW 上本地全链路（Qoder / SceneSQL / 数据同机）。

`docs/knowledge_base/` 的 64 条记忆从原作者活记忆导出，导出时未做环境解耦：
活记忆中"本机"指 xct、"服务"指 8.130.209.216，对无这两台机器访问权限的接手人是污染——
其 Qoder 查库命中后可能去连接无权访问的机器，或误以为服务已存在而跳过自己的部署。

## 变更内容

1. **facts 免责标注（17 条）**：`docs/knowledge_base/facts/` 下所有含环境地址的记忆，
   头部加 `[交接注]`（声明为 2026-08-31 原环境快照，操作以接手人自己的 DSW 部署和 .env 为准）；
   - 服务端点加映射注："你的服务地址 = 你自己 DSW 的 30001"
   - 裸凭证标注"原环境值，你的见 .env"
   - xct 绝对路径 `/data/var/workspace/projects/projects/...` 改 `<SceneSQL仓库>/` 相对表述
2. **AGENTS.md 部署节重写**：改为接手人视角（本地部署、无需 ssh）；前任跳板机
   （ssh 别名 `DSW`，8.130.209.216:1025）降级为历史注——旧文档/脚本中的 `ssh DSW` 勿模仿
3. **交接手册入库**：`docs/gac/LLM标签开发交接手册.md` 随 clone 分发，仓库内副本为权威版本
   （与原作者单独发送的副本内容一致）
4. **手册新增 §0.5「导入事实的时效声明」**：接手人语义分流规则——
   环境类陈述（地址/别名/路径/"本机"）一律视为前任环境仅作对照；
   方法论类陈述（18 条 schema 实证坑、误报分类学、Loop 规程、交付纪律）与机器无关，直接照用
5. **setup.sh**：注入全局 AGENTS.md 的引用文本改为仓库内相对路径

## 涉及文件

- `AGENTS.md`（部署节 + 末节引用路径）
- `CHANGELOG.md`（本条目索引）
- `docs/knowledge_base/setup.sh`
- `docs/gac/LLM标签开发交接手册.md`（新增）
- `docs/knowledge_base/facts/project/`×12、`facts/infra/`×4、`facts/general/`×1、`facts/tool/`×1（共 18 条记忆修订，其中 17 条为环境地址免责）

## 测试验证

- `bash -n docs/knowledge_base/setup.sh` 通过
- 复扫 64 条 facts：无"含环境地址且未免责"残留
- 纯文档变更，不影响运行服务，不触发 DSW deploy

## 方法论零改动说明

18 条 schema 实证坑、误报分类学、Loop 12 步、对照组机制、交付纪律为跨机器成立的知识，
是交接包主体价值，本次未做任何改动。
