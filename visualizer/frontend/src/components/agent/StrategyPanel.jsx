import React from 'react';
import { colors, radius, btn, badge } from '../../theme';

// ── 我的策略 · 页内平面面板 ──
// 平面化设计（仿 data-platform-fe）：策略列表与验证集不再是 modal，
// 而是 SQL 编辑器下方的页内折叠面板；验证集在策略行下方内联展开。
// 这样可视化链路（TopicModal → PlayerModal）永远不会被策略弹窗盖住。
export default function StrategyPanel({
  strategyList,
  syncBusy,
  onLoadStrategy, onDeleteStrategy,
  validationSet,            // {name, cases, loading} | null
  onToggleValidationSet,    // (strategy) => void —— 展开/收起该策略的验证集
  onOpenEvalSync, onSyncStrategyDm,
  onRelabelCase, onVisualizeCase,
}) {
  if (strategyList.length === 0) {
    return (
      <div style={{ padding: 16, textAlign: 'center', color: colors.textTertiary, fontSize: 13 }}>
        暂无自定义策略（在 SQL 编辑器中写好 SQL 后点「保存为策略」）
      </div>
    );
  }

  return (
    <div style={{ border: `1px solid ${colors.border}`, borderRadius: radius.md, overflow: 'hidden' }}>
      {strategyList.map((s) => {
        const expanded = validationSet?.name === s.name;
        return (
          <div key={s.name} style={{ borderBottom: `1px solid ${colors.borderLight}` }}>
            {/* ── 策略行 ── */}
            <div style={{ padding: '10px 14px', background: '#fff' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <strong style={{ fontSize: 14 }}>{s.name}</strong>
                  <span style={{ marginLeft: 8, fontSize: 11, color: colors.textTertiary }}>
                    关键词: {s.keywords.join(', ')}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button onClick={() => onLoadStrategy(s)} style={btn.outline(colors.primary, false)}>加载</button>
                  <button onClick={() => onToggleValidationSet(s)} style={btn.outline(colors.cyan, false, expanded)}>
                    验证集 {expanded ? '▲' : '▼'}
                  </button>
                  <button onClick={() => onOpenEvalSync(s)} disabled={syncBusy} style={btn.outline(colors.purple, syncBusy)}>同步评测集</button>
                  <button onClick={() => onSyncStrategyDm(s.name)} disabled={syncBusy} style={btn.outline(colors.orange, syncBusy)}>同步策略</button>
                  <button onClick={() => onDeleteStrategy(s.name)} style={btn.outline(colors.error, false)}>删除</button>
                </div>
              </div>
              <div style={{ fontSize: 11, color: colors.textSecondary, marginTop: 4 }}>{s.description || '无备注'}</div>
              <pre style={{ fontSize: 10, color: colors.textTertiary, marginTop: 4, maxHeight: 40, overflow: 'hidden', background: colors.bgStripe, padding: 6, borderRadius: radius.sm, margin: '4px 0 0' }}>{s.sql.substring(0, 200)}{s.sql.length > 200 ? '...' : ''}</pre>
            </div>

            {/* ── 验证集内联展开 ── */}
            {expanded && (
              <div style={{ padding: '10px 14px', background: colors.bgStripe, borderTop: `1px dashed ${colors.border}` }}>
                {validationSet.loading ? (
                  <div>
                    {[...Array(2)].map((_, i) => (
                      <div key={i} className="skeleton" style={{ height: 28, marginBottom: 6 }} />
                    ))}
                  </div>
                ) : validationSet.cases.length === 0 ? (
                  <div style={{ color: colors.textTertiary, fontSize: 13, padding: '8px 0' }}>
                    该策略暂无验证集标注（播放视频时用 ✅/❌ 按钮标注）
                  </div>
                ) : (
                  <>
                    <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 8 }}>
                      共 {validationSet.cases.length} 条标注
                      （✅ 通过 {validationSet.cases.filter((c) => c.verdict === 'pass').length}，
                      ❌ 不通过 {validationSet.cases.filter((c) => c.verdict === 'fail').length}）
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, background: '#fff', borderRadius: radius.sm }}>
                        <thead>
                          <tr style={{ borderBottom: `2px solid ${colors.borderLight}`, textAlign: 'left' }}>
                            <th style={{ padding: '6px 8px' }}>Bag ID</th>
                            <th style={{ padding: '6px 8px' }}>时间范围</th>
                            <th style={{ padding: '6px 8px' }}>标注</th>
                            <th style={{ padding: '6px 8px' }}>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {validationSet.cases.map((c, idx) => (
                            <tr key={idx} style={{ borderBottom: `1px solid ${colors.borderLight}` }}>
                              <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 12 }}>{c.bag_id}</td>
                              <td style={{ padding: '6px 8px', fontSize: 12, whiteSpace: 'nowrap' }}>
                                {c.start_ts != null ? `${c.start_ts}s` : '?'} ~ {c.end_ts != null ? `${c.end_ts}s` : '?'}
                              </td>
                              <td style={{ padding: '6px 8px' }}>
                                <span style={badge(c.verdict === 'pass' ? colors.success : colors.error)}>
                                  {c.verdict === 'pass' ? '✅ 通过' : '❌ 不通过'}
                                </span>
                              </td>
                              <td style={{ padding: '6px 8px' }}>
                                <div style={{ display: 'flex', gap: 4 }}>
                                  <button onClick={() => onVisualizeCase(c)} style={btn.outline(colors.primary, false)}>📹 可视化</button>
                                  <button onClick={() => onRelabelCase(c, 'pass')} style={btn.outline(colors.success, false, c.verdict === 'pass')}>✅</button>
                                  <button onClick={() => onRelabelCase(c, 'fail')} style={btn.outline(colors.error, false, c.verdict === 'fail')}>❌</button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
