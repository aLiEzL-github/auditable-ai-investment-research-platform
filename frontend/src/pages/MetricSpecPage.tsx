// MetricSpec 页（G5-03，基线 B §8）
// 交付件：20 指标 origin（expected_origin + caliber）。
//   · 行数须可机检（⑨）：显示 20/N
//   · 冻结哈希恒定可见

import { useEffect, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, EmptyState, ErrorState } from "../components/Basic";
import type { WorkbenchApi } from "../api/client";

export function MetricSpecPage() {
  const { api } = useWorkbench();
  const [view, setView] = useState<Awaited<ReturnType<WorkbenchApi["getMetricSpecView"]>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getMetricSpecView()
      .then((v) => {
        if (!cancelled) setView(v);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (loading) return <EmptyState label="MetricSpec 加载中…" />;
  if (error != null) return <ErrorState message={error} />;
  if (view == null) return <EmptyState label="无 MetricSpec 数据" />;

  return (
    <div className="page">
      <h1>MetricSpec（20 指标 origin）</h1>
      <Card title={`指标清单 —— ${view.rows.length}/20 项`}>
        <p className="source-sha">冻结哈希: {view.frozen_sha256}</p>
        <table className="rules-table" data-testid="metrics-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>expected_origin</th>
              <th>caliber</th>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((m) => (
              <tr key={m.metric_id} data-testid="metric-row">
                <td>{m.metric_id}</td>
                <td>{m.expected_origin}</td>
                <td>{m.caliber}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
