import React from 'react';
import { colors, radius, modal, btn, input, zIndex } from '../../theme';

// ── 策略轻量表单弹窗 ──
// 平面化设计约定（仿 data-platform-fe）：modal 只承载轻量交互——
// 保存策略、评测集同步、策略加载。列表/详情/可视化一律页内平面展示（见 StrategyPanel.jsx）。

// 策略加载弹窗：唯一职责 = 把选中策略的 SQL 灌入 SQL 编辑器。
// 评测集管理不在这里，走顶部 Tab「策略列表」页。
export function StrategyLoaderModal({ strategyList, onLoad, onClose }) {
  return (
    <div style={modal.overlay(zIndex.modal)} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modal.dialog(440, 560)}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h3 style={{ ...modal.title, margin: 0 }}>策略加载</h3>
          <button onClick={onClose} style={modal.closeBtn}>✕</button>
        </div>
        <div style={{ fontSize: 12, color: colors.textTertiary, marginBottom: 12 }}>
          选择策略，将其 SQL 加载到 SQL 编辑器（管理评测集请用顶部「策略列表」页）
        </div>
        {strategyList.length === 0 ? (
          <div style={{ color: colors.textTertiary, textAlign: 'center', padding: 20 }}>暂无自定义策略</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {strategyList.map((s) => (
              <div
                key={s.name}
                onClick={() => onLoad(s)}
                style={{
                  padding: '10px 12px', border: `1px solid ${colors.border}`, borderRadius: radius.md,
                  cursor: 'pointer', transition: 'all 0.1s',
                }}
                onMouseEnter={(ev) => { ev.currentTarget.style.borderColor = colors.primary; ev.currentTarget.style.background = colors.bgHover; }}
                onMouseLeave={(ev) => { ev.currentTarget.style.borderColor = colors.border; ev.currentTarget.style.background = '#fff'; }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontSize: 13 }}>{s.name}</strong>
                  <span style={{ fontSize: 11, color: colors.textTertiary }}>{s.keywords.join(', ')}</span>
                </div>
                <div style={{ fontSize: 11, color: colors.textTertiary, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.description || '无备注'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function StrategyModals({
  saveStrategyModalOpen, setSaveStrategyModalOpen,
  strategyForm, setStrategyForm,
  pendingLabel, setPendingLabel,
  onSaveStrategy,
  evalSyncModal, setEvalSyncModal, onSyncEvalset,
  syncBusy,
}) {
  return (
    <>
      {/* ── 保存策略弹窗 ── */}
      {saveStrategyModalOpen && (
        <div style={modal.overlay(zIndex.modal)}>
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

      {/* ── 评测集同步弹窗 ── */}
      {evalSyncModal && (
        <div style={modal.overlay(zIndex.modal)}>
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
    </>
  );
}
