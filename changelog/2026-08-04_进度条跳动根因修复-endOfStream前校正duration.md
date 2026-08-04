# 2026-08-04 — 进度条跳动根因修复：endOfStream 前用实际缓冲终点校正 duration

## 问题

上一版（2026-08-03）在 sourceopen 预设 `mediaSource.duration = (endTs-startTs)/1e9`，
用户反馈进度条仍跳动。

## E2E 实测定位（DSW，bag=11OTFWosqNfw9sTQ5DYbtD202606）

用 8s ts 窗口（start_ts=1773371160s, end_ts=1773371168s）调
`/api/video/stream-h264`，落盘后 ffprobe：

| 指标 | 值 |
|------|-----|
| ts 窗口 | 8.000 s |
| 实际 MP4 duration | **7.846 s** |

根因：首帧 ts 不精确对齐 start_ts，实际流比窗口短 ~0.15s。
MSE 规范下 `endOfStream()` 取 `max(预设duration, 缓冲终点)`，
**预设值只会被抬高、不会被缩短** → duration 停留 8.0，
currentTime 到 7.85 即结束，进度条与播放进度错位、结尾跳动。

## 修复

`AgentPanel.jsx` MSE 效果：新增 `finishStream()`，在两处收尾路径
（reader done、updateend 排空）调用。endOfStream 之前若
`mediaSource.duration > 实际 buffered.end + 0.05`，先下调到实际终点。

效果：打开即显示 ts 窗口总长（进度条立即完整），流结束后一次性
微调到真实时长（<0.2s，不可感知），此后完全稳定。

## 涉及文件

- `visualizer/frontend/src/components/AgentPanel.jsx`（finishStream + 2 处调用点）

## 验证

- 本地 `react-scripts build` 通过
- DSW 部署后由用户在浏览器复验
