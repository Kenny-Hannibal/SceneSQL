import React from 'react';
import { colors, radius, modal, btn, input, badge } from '../../theme';

// ── 策略相关的四个弹窗 ──
// 保存策略 / 策略列表 / 评测集同步 / 验证集列表。全部受控，状态来自 useStrategies。
export default function StrategyModals({
  saveStrategyModalOpen, setSaveStrategyModalOpen,
  strategyForm, setStrategyForm,
  pendingLabel, setPendingLabel,
  onSaveStrategy,
  strategyListOpen, setStrategyListOpen,
  strategyList, onLoadStrategy, onDeleteStrategy,
  onOpenValidationSet, onOpenEvalSync, onSyncStrategyDm,
  evalSyncModal, setEvalSyncModal, onSyncEvalset,
  validationSetModal, setValidationSetModal, onRelabelCase, onVisualizeCase,
  syncBusy,
}) {
  return (
    <>
      {/* ── 保存策略弹窗 ── */}
      {saveStrategyModalOpen && (
        <div style={modal.overlay(1000)}>
          <div style={modal.dialog(400, 480)}>
            <h3 style={modal.title}>保存为策略</h3>
            {pendingLabel && (
              <div style={{ marginBottom: 12, padding: '8px 12px', background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: radius.md, fontSize: 12, color: '#874d00' }}>
                当前 SQL 尚未保存为策略。请先保存，标注（{pendingLabel.verdict === 'pass' ? '✅ 通过' : '❌ 不通过'}）将自动绑定到新策略。
              </div>
            )}
            {[
              { key: 'name', label: '策略名', placeholder: '如: high_speed_cutin' },
              { key: 'keywords', label: '触发关键词（逗号分隔，用户输入含关键词时自动匹配此策略）', placeholder: '如: 高速切入,高速变道' },
              { key: 'tag_name', label: 'tag_name（可选，留空自动从SQL提取）', placeholder: '如: high_speed_cutin' },
              { key: 'description', label: '备注', placeholder: '策略说明' },
            ].map((f) => (
              <div key={f.key} style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: colors.textSecondary }}>{f.label}</label>
                <input
                  value={strategyForm[f.key]}
                  onChange={(e) => setStrategyForm((p) => ({ ...p, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  style={{ ...input, marginTop: 4, fontSize: 13, padding: '7px 10px' }}
                />
              </div>
            ))}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button onClick={() => { setSaveStrategyModalOpen(false); setPendingLabel(null); }} style={btn.ghost(false)}>取消</button>
              <button onClick={onSaveStrategy} style={btn.success(false)}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* ── 策略列表面板 ── */}
      {strategyListOpen && (
        <div style={modal.overlay(1000)}>
          <div style={modal.dialog(500, 640)}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ ...modal.title, margin: 0 }}>我的策略</h3>
              <button onClick={() => setStrategyListOpen(false)} style={modal.closeBtn}>✕</button>
            </div>
            {strategyList.length === 0 ? (
              <div style={{ color: colors.textTertiary, textAlign: 'center', padding: 20 }}>暂无自定义策略</div>
            ) : (
              <div>
                {strategyList.map((s) => (
                  <div key={s.name} style={{ padding: 12, borderBottom: `1px solid ${colors.borderLight}` }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                      <div>
                        <strong style={{ fontSize: 14 }}>{s.name}</strong>
                        <span style={{ marginLeft: 8, fontSize: 11, color: colors.textTertiary }}>
                          关键词: {s.keywords.join(', ')}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <button onClick={() => onLoadStrategy(s)} style={btn.outline(colors.primary, syncBusy)}>加载</button>
                        <button onClick={() => onOpenValidationSet(s)} style={btn.outline(colors.cyan, syncBusy)}>验证集</button>
                        <button onClick={() => onOpenEvalSync(s)} disabled={syncBusy} style={btn.outline(colors.purple, syncBusy)}>同步评测集</button>
                        <button onClick={() => onSyncStrategyDm(s.name)} disabled={syncBusy} style={btn.outline(colors.orange, syncBusy)}>同步策略</button>
                        <button onClick={() => onDeleteStrategy(s.name)} style={btn.outline(colors.error, syncBusy)}>删除</button>
                      </div>
                    </div>
                    <div style={{ fontSize: 11, color: colors.textSecondary, marginTop: 4 }}>{s.description || '无备注'}</div>
                    <pre style={{ fontSize: 10, color: colors.textTertiary, marginTop: 4, maxHeight: 60, overflow: 'auto', background: colors.bgStripe, padding: 6, borderRadius: radius.sm }}>{s.sql.substring(0, 200)}{s.sql.length > 200 ? '...' : ''}</pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 评测集同步弹窗 ── */}
      {evalSyncModal && (
        <div style={modal.overlay(1002)}>
          <div style={modal.dialog(440, 520)}>
            <h3 style={modal.title}>同步评测集 — {evalSyncModal.strategy.name}</h3>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: colors.textSecondary }}>产线 benchmark 名称</label>
              <input
                value={evalSyncModal.benchmarkName}
                onChange={(e) => setEvalSyncModal((p) => p && ({ ...p, benchmarkName: e.target.value }))}
                style={{ ...input, marginTop: 4, fontSize: 13, padding: '7px 10px' }}
              />
            </div>
            <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 12, lineHeight: 1.8 }}>
              <div>标签映射：✅ 通过 → <code style={{ background: colors.bgStripe, padding: '1px 4px' }}>{evalSyncModal.strategy.name}_positive</code>，❌ 不通过 → <code style={{ background: colors.bgStripe, padding: '1px 4px' }}>{evalSyncModal.strategy.name}_negative</code></div>
              <div>待同步 case：<strong>{evalSyncModal.cases.length}</strong> 条
                （通过 {evalSyncModal.cases.filter((c) => c.verdict === 'pass').length}，不通过 {evalSyncModal.cases.filter((c) => c.verdict === 'fail').length}）</div>
              <div style={{ color: colors.textTertiary, fontSize: 11 }}>产线按 (benchmark, bin_id, tag, 时间窗) 去重，可重复同步</div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button onClick={() => setEvalSyncModal(null)} style={btn.ghost(false)}>取消</button>
              <button
                onClick={onSyncEvalset}
                disabled={syncBusy || evalSyncModal.cases.length === 0}
                style={btn.purple(syncBusy || evalSyncModal.cases.length === 0)}
              >
                {syncBusy ? '同步中...' : `同步 ${evalSyncModal.cases.length} 条到产线`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── 验证集列表弹窗 ── */}
      {validationSetModal && (
        <div style={modal.overlay(1003)}>
          <div style={modal.dialog(600, '90vw')}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ ...modal.title, margin: 0 }}>验证集 — {validationSetModal.strategy.name}</h3>
              <button onClick={() => setValidationSetModal(null)} style={modal.closeBtn}>✕</button>
            </div>
            {validationSetModal.loading ? (
              <div style={{ padding: '20px 0' }}>
                {[...Array(3)].map((_, i) => (
                  <div key={i} className="skeleton" style={{ height: 32, marginBottom: 8 }} />
                ))}
              </div>
            ) : validationSetModal.cases.length === 0 ? (
              <div style={{ color: colors.textTertiary, textAlign: 'center', padding: 20 }}>该策略暂无验证集标注</div>
            ) : (
              <div>
                <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 12 }}>
                  共 {validationSetModal.cases.length} 条标注
                  （✅ 通过 {validationSetModal.cases.filter((c) => c.verdict === 'pass').length}，
                  ❌ 不通过 {validationSetModal.cases.filter((c) => c.verdict === 'fail').length}）
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: `2px solid ${colors.borderLight}`, textAlign: 'left' }}>
                      <th style={{ padding: '8px 4px' }}>Bag ID</th>
                      <th style={{ padding: '8px 4px' }}>时间范围</th>
                      <th style={{ padding: '8px 4px' }}>标注</th>
                      <th style={{ padding: '8px 4px' }}>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {validationSetModal.cases.map((c, idx) => (
                      <tr key={idx} style={{ borderBottom: `1px solid ${colors.borderLight}` }}>
                        <td style={{ padding: '8px 4px', fontFamily: 'monospace', fontSize: 12 }}>{c.bag_id}</td>
                        <td style={{ padding: '8px 4px', fontSize: 12 }}>
                          {c.start_ts != null ? `${c.start_ts}s` : '?'} ~ {c.end_ts != null ? `${c.end_ts}s` : '?'}
                        </td>
                        <td style={{ padding: '8px 4px' }}>
                          <span style={badge(c.verdict === 'pass' ? colors.success : colors.error)}>
                            {c.verdict === 'pass' ? '✅ 通过' : '❌ 不通过'}
                          </span>
                        </td>
                        <td style={{ padding: '8px 4px' }}>
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
            )}
          </div>
        </div>
      )}
    </>
  );
}
