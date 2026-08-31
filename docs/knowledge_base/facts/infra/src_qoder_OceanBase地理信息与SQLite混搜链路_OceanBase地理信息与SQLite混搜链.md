---
category: infra
tags: qoder
---

[src=qoder:OceanBase地理信息与SQLite混搜链路] OceanBase地理信息与SQLite混搜链路
OceanBase 地理信息 × SQLite DB 混搜链路（2026-08-20 实测跑通）

OB 实例与库（密码修正：`YhdK6-5.`，fact_store 旧记录漏了 "5"）

host=`t7txo1a472krk.aliyun-cn-wulanchabu.oceanbase.cloud:3306`, user=`gac_access`，仅 DSW 可直连（VPC）。

| 库 | 内容 |
|---|---|
| `infra_map_data` | 高精地图：road_info(534万,含 from/toRoadIds 拓扑,几何 SRID4326 LINESTRING(lat lon))、lane_info、boundary_info、lane_group_info、obj_info。**无路口表**。多值分隔符是 `;` 不是逗号 |
| `infra_geom_data` | **轨迹/匹配表**：bag_location_track_v2(6684万点,77.8万bag,GACRT测试车队18台)、bag_location_junction_v1(456万,bag↔junction_id预匹配)、**ubm_bag_location_track_v1**(334万点,2.8万bag,UBM挖掘车队)、ubm_bag_location_junction_v1(1854行)、merge_area_v1(仅merge/diverge匝道) |
| `default.ubm_bag_id_map` | c-UUID→图片OSS路径，不是ID映射表 |

bag_id 体系对齐（关键结论）

- **OB 所有轨迹表的 bag_id = 原始 bag_id**（c-UUID 格式，来自 `collection_t68_thor_bag_metadata` 原始表）
- **sqlite db 文件名 = 回灌 bag_id**（随机串如 `1000Toa0QZCWncus1SA8NT202606`）
- 桥接：dm_sdk `ProdDataClient(table='ubm_vehicle_module_bin').get_bag_metadata(data_id=回灌ID).resp_data()['origins'][0]['bag_id']` → 原始 c-UUID（单条 ~15ms，可多线程）；vin 在 `metadata.vin` 子字段
- **反向（c-UUID→回灌ID）dm_sdk 不支持**（search_bags 按 origin 过滤报 500），只能正向扫批次建映射表
- DSW 上运行：`cd /root/data/text2sql && PYTHONPATH=/root/.local/lib/python3.12/site-packages .venv/bin/python`（venv 有 dm_sdk 无 pymysql，借系统的）
- 0702 批次全量映射已缓存在 DSW `/tmp/batch0702_origin_map.json`（15460 条，c-UUID→db名）

覆盖率实测（2026-08-20）

- `bag_location_track_v2`（GACRT 车队）与 0702 批次（T68 thor 量产）**零交集**
- `ubm_bag_location_track_v1` 与 0702 批次交集仅 **5 个 bag**
- 其他批次：0616/0522/0603 的 db 在产线表查不到（code 9014，合成/测试 bag）

Y 型路口定位（无路口表，用拓扑+几何自己算）

1. `road_info WHERE toRoadIds LIKE '%;%'`（64.6万条分叉路，4城：广州/北京/上海/重庆）；严格 Y 型 = 恰好 1 个分号（一变二）
2. 分叉点 = 该 road 几何 LINESTRING 终点（OB 无 ST_EndPoint，取 WKT 解析）
3. 轨迹匹配：`ubm_bag_location_track_v1 WHERE ST_Distance_Sphere(location, POINT(fork,4326)) <= 30`
4. OB 空间函数可用：ST_Intersects / ST_Contains / ST_Buffer / ST_Distance_Sphere / ST_X/Y/ST_AsText；不可用：ST_EndPoint/ST_PointN/MBR*；**ST_GeomFromText 必须带 SRID 4326** 否则报 3643
5. 轨迹 location 与地图几何坐标序一致：**X=纬度, Y=经度**（POINT(lat lon)）

E2E 验证案例

Y 路口 = road_info 55660922（广州，分叉点 23.0951447, 113.3333014）→ 24 个 bag 经过 → 其中 `c-0005f852` 映射到 db `16UO68Wf4ocTqwVpoOhwZT202606`（0702 批次）→ sqlite ego 表：经过窗口 ts(秒)=1773700811~1773700824，车速 47.6~105.1 km/h（OB ts 纳秒 ÷1e9 对齐 sqlite ts 秒）。

SQLite db 侧要点

- ego 表有 speed(m/s)、ts(秒级)、utm_x/y/yaw；还有 **intersection_info 表**（intersection_id, lane_count, lane_info JSON 含 turn_type）可用于路口场景交叉验证
- 批次路径：`/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/20260702_T68_2471_c5afa57_100w/`（15462 db）

已知限制

混搜链路逻辑已通，但**当前数据覆盖是瓶颈**：ubm 轨迹表只有 2.8 万 bag，与 0702 批次交集极小。规模化需要：(a) 轨迹入库覆盖更多 UBM 挖掘 bag，或 (b) 用 bag_location_junction_v1 的 junction 预匹配体系（但 junction_id 与 infra_map_data 的 roadId 不互通，来源待确认）。
