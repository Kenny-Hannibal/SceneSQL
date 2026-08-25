import React, { useEffect } from 'react';
import { colors, radius, btn } from '../../theme';
import PaginationControls from './PaginationControls';

// 结果表格：顶部/底部双滚动条同步、骨架屏 loading、空态、标注小圆点、行可视化按钮。
export default function ResultTable({
  result,
  loading,
  allRows,
  displayRows,
  totalRows,
  page,
  pageSize,
  onPageChange,
  visualizedRows,
  onVisualize,
  matchedLabels,
  labelKey,
  actionDisabled,
  onArrowDownload,
  mayBeTruncated,
}) {
  const columns = result?.columns || [];
  const hasResults = allRows.length > 0;
  // 聚合/统计类结果（GROUP BY/COUNT 等）无行级时间语义，不展示可视化按钮（rg-17）
  const visualizable = result?.visualizable !== false;

  // 双向滚动条同步：顶部 + 底部
  useEffect(() => {
    const topBar = document.getElementById('top-scrollbar');
    const topContent = document.getElementById('top-scrollbar-content');
    const bottomBar = document.getElementById('tbl-scroll-container');
    if (!topBar || !topContent || !bottomBar) return;

    const syncWidth = () => {
      topContent.style.width = bottomBar.scrollWidth + 'px';
    };
    syncWidth();

    let isSyncing = false;
    const onTopScroll = () => {
      if (isSyncing) return;
      isSyncing = true;
      bottomBar.scrollLeft = topBar.scrollLeft;
      isSyncing = false;
    };
    const onBottomScroll = () => {
      if (isSyncing) return;
      isSyncing = true;
      topBar.scrollLeft = bottomBar.scrollLeft;
      isSyncing = false;
    };

    topBar.addEventListener('scroll', onTopScroll);
    bottomBar.addEventListener('scroll', onBottomScroll);
    window.addEventListener('resize', syncWidth);
    return () => {
      topBar.removeEventListener('scroll', onTopScroll);
      bottomBar.removeEventListener('scroll', onBottomScroll);
      window.removeEventListener('resize', syncWidth);
    };
  }, [allRows, page]);

  // ── 查询中：骨架屏 ──
  if (loading && !result) {
    return (
      <div style={{ marginTop: 16 }}>
        <div className="skeleton" style={{ height: 16, width: '30%', marginBottom: 12 }} />
        {[...Array(5)].map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 36, marginBottom: 6, opacity: 1 - i * 0.15 }} />
        ))}
      </div>
    );
  }

  if (!result) return null;

  return (
    <div style={{ marginTop: 16 }}>
      {result.explanation && (
        <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 12, lineHeight: 1.6 }}>
          {result.explanation}
        </div>
      )}

      {hasResults && (
        <div>
          <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 6, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <span>
              共 <b>{totalRows}</b> 行{mayBeTruncated ? '（已截断：可调大结果行数限制，或用「导出 CSV」获取全量）' : ''} · 第 {page} 页
            </span>
            {result?.scanned_dbs > 0 && (
              <span style={{ color: colors.textTertiary }}>
                扫描 {result.scanned_dbs} 个 DB，命中 {result.matched_dbs} 个
              </span>
            )}
            <button
              onClick={onArrowDownload}
              style={btn.outline(colors.success, false)}
              title="下载 Arrow IPC 二进制文件（高效传输，pyarrow 不可用时自动降级为 JSON）"
            >
              ⬇ Arrow 下载
            </button>
          </div>

          {/* 顶部滚动条：与底部同步 */}
          <div id="top-scrollbar" style={{ overflowX: 'auto', height: 14, borderBottom: `1px solid ${colors.borderLight}` }}>
            <div id="top-scrollbar-content" style={{ height: 1 }}></div>
          </div>

          <div style={{ overflowX: 'auto', border: `1px solid ${colors.border}`, borderRadius: radius.md }} id="tbl-scroll-container">
            <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontSize: 13 }}>
              <thead>
                <tr style={{ background: colors.bgStripe }}>
                  {columns.map((col) => (
                    <th key={col} style={{
                      borderBottom: `1px solid ${colors.border}`, borderRight: `1px solid ${colors.borderLight}`,
                      padding: '10px 12px', textAlign: 'left', fontWeight: 600, color: colors.text,
                      position: 'sticky', top: 0, background: colors.bgStripe, whiteSpace: 'nowrap',
                    }}>
                      {col}
                    </th>
                  ))}
                  <th style={{
                    borderBottom: `1px solid ${colors.border}`, padding: '10px 12px', textAlign: 'left', fontWeight: 600,
                    position: 'sticky', right: 0, background: colors.bgStripe, zIndex: 2,
                    boxShadow: '-2px 0 4px rgba(0,0,0,0.05)', whiteSpace: 'nowrap',
                  }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {displayRows.map((row, idx) => {
                  // 用行内容的唯一标识做去重 key，保证翻页后也能识别
                  const rowKey = `${row.bag_path || ''}|${row.topic || ''}|${row.start_ts || ''}|${row.tag_name || ''}`;
                  const isVisualized = visualizedRows.has(rowKey);
                  const zebra = idx % 2 === 0 ? '#fff' : colors.bgStripe;
                  return (
                    <tr key={idx} className="tbl-row" style={{
                      background: isVisualized ? '#e6f4ff' : zebra,
                      borderLeft: isVisualized ? `3px solid ${colors.primary}` : 'none',
                    }}>
                      {columns.map((col) => (
                        <td key={col} style={{
                          borderBottom: `1px solid ${colors.borderLight}`, borderRight: `1px solid ${colors.borderLight}`,
                          padding: '8px 12px', color: colors.text, maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis',
                        }}
                          title={typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? '')}
                        >
                          {typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? '')}
                        </td>
                      ))}
                      <td style={{
                        borderBottom: `1px solid ${colors.borderLight}`, padding: '8px 12px',
                        position: 'sticky', right: 0, background: isVisualized ? '#e6f4ff' : zebra, zIndex: 1,
                        boxShadow: '-2px 0 4px rgba(0,0,0,0.05)', whiteSpace: 'nowrap',
                      }}>
                        {visualizable ? (
                          <button
                            onClick={() => onVisualize(row, rowKey)}
                            disabled={actionDisabled}
                            title={actionDisabled ? '请先关闭当前弹窗' : '播包可视化'}
                            style={btn.outline(colors.primary, actionDisabled)}
                          >
                            📹 播包可视化
                          </button>
                        ) : (
                          <span style={{ fontSize: 11, color: colors.textTertiary }} title="聚合/统计结果无行级时间语义，不可锚定视频片段">
                            统计结果
                          </span>
                        )}
                        {matchedLabels[labelKey(row)] && (
                          <span
                            title={matchedLabels[labelKey(row)] === 'pass' ? '已标注：通过' : '已标注：不通过'}
                            style={{
                              display: 'inline-block', width: 8, height: 8, borderRadius: '50%', marginLeft: 6,
                              background: matchedLabels[labelKey(row)] === 'pass' ? colors.success : colors.error,
                            }}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {totalRows > 0 && (
            <PaginationControls
              page={page}
              pageSize={pageSize}
              totalRows={totalRows}
              onPageChange={onPageChange}
            />
          )}
        </div>
      )}

      {allRows.length === 0 && (
        <div style={{
          color: colors.textTertiary, fontSize: 14, textAlign: 'center',
          padding: '32px 0', border: `1px dashed ${colors.border}`, borderRadius: radius.md,
        }}>
          🔍 查询成功，但没有符合条件的行
        </div>
      )}
    </div>
  );
}
