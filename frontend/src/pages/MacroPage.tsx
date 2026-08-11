// 宏观与计算页（G5-04，基线 B §8）
// 交付件：传导链、CalcLedger。
// 验收：派生判断和外部事实明确区分（[D:] vs [F:]）。
//   · MacroGate 状态恒定可见：MACRO_GATE_FAIL 时明示「不得输出当前估值」（C-2 UI 形态）
//   · 宏观四时刻（published/effective/retrieved/cutoff）分离显示（G3-03）

import { useEffect, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, EmptyState, ErrorState } from "../components/Basic";
import { StatusBadge } from "../components/StatusBadge";
import type { CalcView, MacroView } from "../types";

export function MacroPage() {
  const { api } = useWorkbench();
  const [macro, setMacro] = useState<MacroView | null>(null);
  const [calc, setCalc] = useState<CalcView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getMacroView(), api.getCalcView()])
      .then(([m, c]) => {
        if (!cancelled) {
          setMacro(m);
          setCalc(c);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (error != null) return <ErrorState message={error} />;
  if (macro == null || calc == null) return <EmptyState label="宏观与计算加载中…" />;

  const gateFail = macro.snapshot.gate.verdict === "MACRO_GATE_FAIL";

  return (
    <div className="page">
      <h1>宏观与计算</h1>

      {/* MacroGate 状态：失败 → 不得输出当前估值（C-2） */}
      <Card title={`MacroGate —— ${macro.snapshot.gate.verdict}`}>
        {gateFail ? (
          <div data-testid="macro-gate-fail" className="macro-gate-fail">
            <strong>宏观聚合门失败 —— 不得输出当前估值（C-2）</strong>
            <ul>
              {macro.snapshot.gate.failures.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p data-testid="macro-gate-pass">宏观聚合门通过；快照已冻结（FROZEN）</p>
        )}
        <p className="source-sha">spec_sha256: {macro.snapshot.spec_sha256}</p>
      </Card>

      {/* 宏观快照：四时刻分离 */}
      <Card title={`宏观快照 —— ${macro.snapshot.state}`}>
        <ul data-testid="macro-times">
          <li>published: {macro.snapshot.published_at}</li>
          <li>effective: {macro.snapshot.effective_date}</li>
          <li>retrieved: {macro.snapshot.retrieved_at}</li>
          <li>cutoff: {macro.snapshot.cutoff_at}</li>
        </ul>
        <ul data-testid="macro-series">
          {macro.snapshot.series.map((s) => (
            <li key={s.series_id} data-testid="macro-series-item" data-status={s.status}>
              <StatusBadge
                tone={s.status === "OK" ? "ok" : "warn"}
                label={s.material ? "材料性" : "非材料性"}
              />
              <strong>{s.name}</strong> vintage={s.vintage} rows={s.rows} · {s.status}
            </li>
          ))}
        </ul>
      </Card>

      {/* 传导链 */}
      <Card title={`传导链（${macro.transmission.length} 条）`}>
        <ul data-testid="transmission">
          {macro.transmission.map((t) => (
            <li key={t.macro_series_id}>
              <strong>{t.macro_series_id}</strong> → {t.transmission} → 目标: {t.target_metric}
            </li>
          ))}
        </ul>
      </Card>

      {/* CalcLedger：派生 vs 外部事实 */}
      <Card title={`CalcLedger（${calc.entries.length} 笔）`}>
        <table className="rules-table" data-testid="calc-table">
          <thead>
            <tr>
              <th>entry</th>
              <th>公式</th>
              <th>输入（EXTERNAL_FACT 外部事实 / DERIVED 派生）</th>
              <th>结果</th>
              <th>unit</th>
            </tr>
          </thead>
          <tbody>
            {calc.entries.map((e) => (
              <tr key={e.entry_id} data-testid="calc-entry">
                <td>{e.entry_id}</td>
                <td>{e.formula_id}@{e.formula_version}</td>
                <td>
                  <ul>
                    {e.inputs.map((i) => (
                      <li key={i.input_key} data-testid={`calc-input-${i.kind}`}>
                        {i.kind === "EXTERNAL_FACT" ? "[F:]" : "[D:]"} {i.input_key} =
                        {i.value}
                        <span className="source-sha"> hash={i.input_sha256}</span>
                      </li>
                    ))}
                  </ul>
                </td>
                <td>{e.result}</td>
                <td>{e.unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="source-sha">结果哈希逐笔可回溯（输入哈希 + 公式版本 + 结果哈希）</p>
      </Card>
    </div>
  );
}
