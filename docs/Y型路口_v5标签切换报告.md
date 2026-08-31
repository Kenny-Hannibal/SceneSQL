# Y型路口 v5 标签切换报告（Round 3）

日期：2026-08-18 ｜ 分支：feature/gen-sql-two-round ｜ tag_name：`topology_intersection_y_junction_v4` → `topology_intersection_y_junction_v5`

## 1. 背景

用户基于 v4 标签重标评测集：55 → 67 条（新增 12 条：6 pass + 6 fail）。v4 在新评测集上 **52/67**（旧 46/55，新 6/12）。新增 6 条 fail 均为误报，分三类：

| 类别 | 代表 bag | fork | 结论 |
|---|---|---|---|
| ① 右转专用道 | 10CxRtaiktAub1MVXPm80W202606 | 162093285 | **v5 已修复** |
| ② 路边私域入口（闸门） | 10CLiIkgWPWBVR3fVOLnZs202606 | 146558621 / 88577059 | 能力边界，无解 |
| ③ 隔离带外同向 link | 10CYuh7…/10CzoR…/10EhxX…/10FAHV… | 163345464 / 156955499 / 163938328 | 能力边界，无解 |

## 2. v5 新增规则：右转专用道剔除

- 规则：分叉的任一分支，其**全部行车道**（laneTypes 首段 ≠ 65536）的 turnType 均含 bit 32（右转），且该分支相对 fork 行进方向偏航角 dev < 20° → 剔除该 fork。
- 验证：杀 83/1822 个 fork；抽样 20/20 被杀分支几何确为右转弯道；eval 全部 23 个 pass fork 与 4 个受保护 fork 零命中（dev<20 阈值保护了 pass fork 91065715，其右转分支 dev=22.6°）。

## 3. ②③ 类为何无解（排查过程）

- 分支 link 几何仅 ~5m 短桩（length 字段单位为厘米），v4_parallel 的 50/100/150m 采样从未触发；改用「串联后继 link」测分支间距剖面后，pass fork 同样大量存在平行不分离剖面（如 pass 157156039 与 fail 146558621 几乎同构）。
- 分叉点 degree、拓扑回环、分支等长、formWay、obj_info 对象签名（无闸门/barrier 对象）均无法分离 fail 与 pass。
- 候选规则 fork_fw==2 可修复 2 条旧 fail，但会打坏 1 条 pass（101XYk），净收益为负，放弃。
- 标签矛盾：fork 132684963 两次 pass、一次 fail；fork 20468686 一次 pass 一次 fail——同一地图对象标注不一致，fork 级规则原理上无解。

## 4. 部署动作（已全部完成）

1. `gen_forks_v5.py`：forks_v4（1822）→ forks_v5（1739），剔除 83。
2. `pipeline.py --forks-csv forks_v5.csv.gz` 全量重标 15,460 bag：正样本 1588 → **1552**（-36，v5⊆v4，零新增，零错误）。
3. writeback：清理 ego_track_fork_match 旧行 1992 条，写入 v5 行 1954 条（1552 bag 全部 ok）。
4. YAML 切 v5：`user_strategies/Y型路口.yaml`、`recipes/intersection_y_junction.yaml`（commit 624d51c），服务热加载生效。
5. E2E（线上 API execute-sql）：**ALL PASS** —— 10/10 正例命中（含 6 条新 pass）；右转 bad case 10CxRt 已无输出；36 个反例窗口仅 13 个 KNOWN-RESIDUAL（8 旧 + 5 新能力边界）重叠。
6. 翻转审计：36 个降级 bag 仅由 7 个被剔除 fork 贡献（162093285 占 22 bag），无 pass 误伤。

## 5. 指标

- 评测集预期准确率：52/67 → **53/67（79.1%）**。
- 全量正样本：1588 → 1552（-2.3%）。
- 剩余 14 条 fail：13 条能力边界（闸门/平行同向 link/标签矛盾）+ 1 条修复未覆盖窗口差异。

## 6. DSW 产物索引（/root/yj_r2/）

`forks_v5.csv.gz`、`labels_v5.csv`、`gen_forks_v5.py`、`e2e_check_v5.py`、`writeback_v5.log`、`relabel_v5.out`、`fork_degree.csv.gz`、`r3_chain2.py/out`、`r3_topo.py`
