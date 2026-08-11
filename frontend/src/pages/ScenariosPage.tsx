// 三情景页（G5-04，基线 B §8）
// 交付件：情景和触发器。
//   · PESSIMISTIC/BASE/OPTIMISTIC 三情景恒定显示 + 触发器（G3-06）

import { useEffect, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, EmptyState, ErrorState } from "../components/Basic";
import type { ScenariosView } from "../types";

export function ScenariosPage() {
  const { api } = useWorkbench();
  const [view, setView] = useState<ScenariosView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getScenariosView()
      .then((v) => {
        if (!cancelled) setView(v);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (error != null) return <ErrorState message={error} />;
  if (view == null) return <EmptyState label="三情景加载中…" />;

  return (
    <div className="page">
      <h1>三情景估值</h1>
      <Card title={`情景（${view.rows.length} 个，PESSIMISTIC/BASE/OPTIMISTIC）`}>
        <table className="rules-table" data-testid="scenarios-table">
          <thead>
            <tr>
              <th>情景</th>
              <th>方法</th>
              <th>区间（低-高）</th>
              <th>每股</th>
              <th>触发器</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((r) => (
              <tr key={r.scenario} data-testid="scenario-row" data-scenario={r.scenario}>
                <td><strong>{r.scenario}</strong></td>
                <td>{r.method}</td>
                <td>{r.low} – {r.high}</td>
                <td>{r.per_share}</td>
                <td>{r.triggers}</td>
                <td>{r.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
