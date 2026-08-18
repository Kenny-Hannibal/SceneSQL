import { useState, useEffect } from 'react';
import { API_BASE, authFetch } from '../../api';
import { useToast } from '../../toast';

// ── 策略管理 + 评测标注 hook ──
// 从 AgentPanel 抽出的策略保存/加载/删除、通过/不通过标注、产线同步、验证集逻辑。
// 依赖注入：getSqlEditor() 取当前 SQL（避免 hook 与编辑器 state 耦合）。

export function useStrategies({ getSqlEditor, setSqlEditor, playerData }) {
  const toast = useToast();

  const [saveStrategyModalOpen, setSaveStrategyModalOpen] = useState(false);
  const [strategyListOpen, setStrategyListOpen] = useState(false);
  const [strategyList, setStrategyList] = useState([]);
  const [strategyForm, setStrategyForm] = useState({ name: '', keywords: '', tag_name: '', description: '' });

  // 评测标注（通过/不通过）与产线同步
  const [pendingLabel, setPendingLabel] = useState(null);   // {verdict, bag_id, start_ts, end_ts} 保存策略后补提交
  const [rowLabel, setRowLabel] = useState(null);           // 当前播放行的标注 'pass'|'fail'|null
  const [matchedLabels, setMatchedLabels] = useState({});   // key: bag_id|start_ts|end_ts → verdict
  const [evalSyncModal, setEvalSyncModal] = useState(null); // {strategy, benchmarkName, cases}
  const [syncBusy, setSyncBusy] = useState(false);
  const [validationSetModal, setValidationSetModal] = useState(null); // {strategy, cases, loading}

  const labelKey = (row) => `${row?.bag_id}|${row?.start_ts ?? ''}|${row?.end_ts ?? ''}`;

  // 当前 SQL 是否已保存为策略（按 SQL 文本精确匹配）
  const sqlEditor = getSqlEditor();
  const matchedStrategy = strategyList.find((s) => (s.sql || '').trim() === sqlEditor.trim()) || null;

  const loadStrategyList = async () => {
    try {
      const res = await authFetch(`${API_BASE}/api/strategies`);
      if (res.ok) setStrategyList(await res.json());
    } catch (e) { console.error('Failed to load strategies', e); }
  };

  useEffect(() => { loadStrategyList(); }, []);

  const loadLabelsForStrategy = async (name) => {
    try {
      const res = await authFetch(`${API_BASE}/api/eval-labels/${encodeURIComponent(name)}`);
      if (res.ok) {
        const data = await res.json();
        const map = {};
        (data.cases || []).forEach((c) => { map[`${c.bag_id}|${c.start_ts ?? ''}|${c.end_ts ?? ''}`] = c.verdict; });
        setMatchedLabels(map);
        return data.cases || [];
      }
    } catch (e) { console.error('Failed to load labels', e); }
    return [];
  };

  const postLabel = async (strategyName, row, verdict) => {
    const res = await authFetch(`${API_BASE}/api/eval-labels`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_name: strategyName,
        bag_id: row.bag_id,
        start_ts: row.start_ts != null ? Number(row.start_ts) : null,
        end_ts: row.end_ts != null ? Number(row.end_ts) : null,
        verdict,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'HTTP ' + res.status);
    }
  };

  const handleSaveStrategy = async () => {
    const sql = getSqlEditor();
    try {
      const keywords = strategyForm.keywords.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
      if (!strategyForm.name || !keywords.length || !sql.trim()) {
        toast.warning('请填写策略名、关键词，并确保 SQL 不为空');
        return;
      }
      // 自动推断 tag_name：从 SQL 中提取第一个字符串字面量
      let tag_name = strategyForm.tag_name;
      if (!tag_name) {
        const m = sql.match(/(?:AS\s+tag_name|tag_name\s*=\s*)['"]([^'"]+)['"]/i)
          || sql.match(/['"]([A-Z][A-Za-z_]+)['"]\s+AS\s+tag_name/i);
        tag_name = m ? m[1] : strategyForm.name;
      }
      const res = await authFetch(`${API_BASE}/api/strategies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: strategyForm.name,
          keywords,
          tag_name,
          sql: sql.trim(),
          description: strategyForm.description,
        }),
      });
      if (res.ok) {
        setSaveStrategyModalOpen(false);
        setStrategyForm({ name: '', keywords: '', tag_name: '', description: '' });
        loadStrategyList();
        // 通过/不通过触发的保存：保存成功后立即补提交标注
        if (pendingLabel) {
          try {
            await postLabel(strategyForm.name, pendingLabel, pendingLabel.verdict);
            setRowLabel(pendingLabel.verdict);
            setMatchedLabels((prev) => ({
              ...prev,
              [`${pendingLabel.bag_id}|${pendingLabel.start_ts ?? ''}|${pendingLabel.end_ts ?? ''}`]: pendingLabel.verdict,
            }));
            toast.success('策略已保存，标注已绑定到该策略');
          } catch (e) {
            toast.error('策略已保存，但标注失败: ' + e.message);
          }
          setPendingLabel(null);
        } else {
          toast.success('策略已保存');
        }
      } else {
        const err = await res.json();
        toast.error('保存失败: ' + (err.detail || JSON.stringify(err)));
      }
    } catch (e) {
      toast.error('保存失败: ' + e.message);
    }
  };

  const handleDeleteStrategy = async (name) => {
    if (!window.confirm(`确定删除策略 "${name}"？`)) return;
    try {
      const res = await authFetch(`${API_BASE}/api/strategies/${name}`, { method: 'DELETE' });
      if (res.ok) {
        loadStrategyList();
        toast.success(`策略 "${name}" 已删除`);
      }
    } catch (e) { toast.error('删除失败: ' + e.message); }
  };

  const handleLoadStrategy = (s) => {
    setSqlEditor(s.sql);
    setStrategyListOpen(false);
  };

  const handleLabel = async (verdict) => {
    const row = playerData?.row;
    if (!row || !row.bag_id) { toast.warning('当前行缺少 bag_id，无法标注'); return; }
    if (row.start_ts == null || row.end_ts == null) { toast.warning('当前行缺少 start_ts/end_ts，无法标注'); return; }
    try {
      if (matchedStrategy) {
        await postLabel(matchedStrategy.name, row, verdict);
        setRowLabel(verdict);
        setMatchedLabels((prev) => ({ ...prev, [labelKey(row)]: verdict }));
        toast.success(verdict === 'pass' ? '已标注：通过' : '已标注：不通过');
      } else {
        // SQL 未保存为策略 → 先弹保存策略窗口，保存成功后补提交标注
        setPendingLabel({ verdict, bag_id: row.bag_id, start_ts: row.start_ts, end_ts: row.end_ts });
        setSaveStrategyModalOpen(true);
      }
    } catch (e) {
      toast.error('标注失败: ' + e.message);
    }
  };

  const handleSyncStrategyDm = async (name) => {
    if (!window.confirm(`将策略 "${name}" 同步到 DataMining 平台（重名则更新），并推送标注 case 为评测详情（通过/不通过）？`)) return;
    setSyncBusy(true);
    try {
      const res = await authFetch(`${API_BASE}/api/strategies/${encodeURIComponent(name)}/sync-dm`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const rv = data.reviews || {};
        const modeText = data.mode === 'created' ? '新建' : '更新';
        toast.success(`已同步到产线（${modeText}）\n评测详情：成功 ${rv.pushed ?? 0}，跳过 ${rv.skipped ?? 0}，失败 ${rv.failed ?? 0}`, 6000);
      } else {
        toast.error('同步失败: ' + (data.detail || JSON.stringify(data)));
      }
    } catch (e) {
      toast.error('同步失败: ' + e.message);
    } finally {
      setSyncBusy(false);
    }
  };

  const openEvalSyncModal = async (s) => {
    const cases = await loadLabelsForStrategy(s.name);
    setEvalSyncModal({ strategy: s, benchmarkName: `scenesql_${s.name}`, cases });
  };

  const handleSyncEvalset = async () => {
    if (!evalSyncModal) return;
    const { strategy, benchmarkName, cases } = evalSyncModal;
    if (!cases.length) { toast.warning('该策略暂无标注 case'); return; }
    if (!benchmarkName.trim()) { toast.warning('请填写 benchmark 名称'); return; }
    setSyncBusy(true);
    try {
      const res = await authFetch(`${API_BASE}/api/eval-labels/${encodeURIComponent(strategy.name)}/sync-evalset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ benchmark_name: benchmarkName.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const r = data.result || {};
        let msg = `同步完成：提交 ${data.submitted} 条，产线成功 ${r.successCount ?? '?'}，失败 ${r.failCount ?? '?'}`;
        if ((data.skipped || []).length) msg += `，跳过 ${data.skipped.length} 条（缺时间戳）`;
        toast.success(msg, 6000);
        setEvalSyncModal(null);
      } else {
        toast.error('同步失败: ' + (data.detail || JSON.stringify(data)));
      }
    } catch (e) {
      toast.error('同步失败: ' + e.message);
    } finally {
      setSyncBusy(false);
    }
  };

  // 验证集可视化：打开验证集列表弹窗
  const openValidationSet = async (s) => {
    setValidationSetModal({ strategy: s, cases: [], loading: true });
    try {
      const res = await authFetch(`${API_BASE}/api/eval-labels/${encodeURIComponent(s.name)}`);
      if (res.ok) {
        const data = await res.json();
        setValidationSetModal({ strategy: s, cases: data.cases || [], loading: false });
      } else {
        setValidationSetModal({ strategy: s, cases: [], loading: false });
      }
    } catch (e) {
      setValidationSetModal({ strategy: s, cases: [], loading: false });
    }
  };

  // 验证集可视化：覆盖标注
  const handleRelabelValidationCase = async (c, verdict) => {
    try {
      const res = await authFetch(`${API_BASE}/api/eval-labels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_name: validationSetModal.strategy.name,
          bag_id: c.bag_id,
          start_ts: c.start_ts,
          end_ts: c.end_ts,
          verdict,
        }),
      });
      if (res.ok) {
        setValidationSetModal((prev) => prev ? {
          ...prev,
          cases: prev.cases.map((x) =>
            x.bag_id === c.bag_id && x.start_ts === c.start_ts && x.end_ts === c.end_ts
              ? { ...x, verdict }
              : x
          ),
        } : prev);
        toast.success('标注已更新');
      }
    } catch (e) {
      toast.error('标注失败: ' + e.message);
    }
  };

  // 当前 SQL 匹配到策略时，载入其标注（结果表小圆点回显）
  useEffect(() => {
    if (matchedStrategy) loadLabelsForStrategy(matchedStrategy.name);
    else setMatchedLabels({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchedStrategy?.name, strategyList.length]);

  // 播放器打开时：载入匹配策略的既有标注，回显当前行标注状态
  useEffect(() => {
    if (!playerData?.row) {
      setRowLabel(null);
      return;
    }
    setRowLabel(matchedLabels[labelKey(playerData.row)] || null);
    if (matchedStrategy) {
      loadLabelsForStrategy(matchedStrategy.name).then((cases) => {
        const c = cases.find((x) => `${x.bag_id}|${x.start_ts ?? ''}|${x.end_ts ?? ''}` === labelKey(playerData.row));
        setRowLabel(c ? c.verdict : null);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerData, matchedStrategy?.name]);

  return {
    // 状态
    saveStrategyModalOpen, setSaveStrategyModalOpen,
    strategyListOpen, setStrategyListOpen,
    strategyList, strategyForm, setStrategyForm,
    pendingLabel, setPendingLabel,
    rowLabel, matchedLabels,
    evalSyncModal, setEvalSyncModal,
    syncBusy,
    validationSetModal, setValidationSetModal,
    matchedStrategy, labelKey,
    // 处理器
    loadStrategyList,
    handleSaveStrategy,
    handleDeleteStrategy,
    handleLoadStrategy,
    handleLabel,
    handleSyncStrategyDm,
    openEvalSyncModal,
    handleSyncEvalset,
    openValidationSet,
    handleRelabelValidationCase,
  };
}
