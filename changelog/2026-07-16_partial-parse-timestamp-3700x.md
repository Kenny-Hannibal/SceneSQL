# perf: partial protobuf parse提取timestamp — 263s→0.07s

## 变更类型
性能优化

## 影响模块
- `visualizer/backend/app/services/fusion_map_parser.py`

## 问题
BEV视图首次打开需要构建ts索引，完整protobuf解码1185帧需263秒（逐帧`msg.ParseFromString`构建完整EFusionMap对象仅提取timestamp），用户体验极差。

## 方案
**Partial protobuf parse**：EFusionMap.timestamp是field 1 (tag 0x0a)，只解析payload前几个字节提取timestamp，不构建完整protobuf对象。

- EFusionMap: field 1 = timestamp (length-delimited, tag=0x0a)
- Comm.TimeStamp: field 1 = sec (varint, tag=0x08), field 2 = nsec (varint, tag=0x10)

## 性能对比
| 方法 | 1185帧耗时 | 加速比 |
|------|-----------|--------|
| 完整protobuf解码 | 263s | 1x |
| partial parse | 0.07s | **3700x** |

## 验证
DSW curl测试 `fusion-map-frames-by-ts-range` API：0.1s返回，结果正确。
