// 新建研究页（G5-02，基线 B §8）
// 交付件：市场、证券、as-of、期限、模型、预算。
// 验收：缺 ResearchContract 不能启动 ——
//   ① 表单六字段齐备才能构造 contract（缺任一 → 启动按钮禁用并显示缺项）
//   ② 「启动」动作一律走后端 launchResearch，前端不自行判定可启动（E-1/E-3）
//   ③ 后端拒绝 → 显式失败态（E-8），不显示成功

import { useCallback, useMemo, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, ErrorState } from "../components/Basic";
import type {
  ResearchContract,
  ResearchForm,
  ResearchLaunchResult,
} from "../types";

const FORM_FIELDS: { key: keyof ResearchForm; label: string; placeholder: string }[] = [
  { key: "market", label: "市场", placeholder: "A-share" },
  { key: "security", label: "证券", placeholder: "600089.SH" },
  { key: "as_of", label: "as-of", placeholder: "2026-08-11" },
  { key: "horizon", label: "期限", placeholder: "2026" },
  { key: "model", label: "模型", placeholder: "v0.1.0" },
  { key: "budget", label: "预算", placeholder: "budget-2026" },
];

// 表单 → ResearchContract 的映射（仅组装，不含任何「可否启动」判定）
function toContract(f: ResearchForm): ResearchContract {
  return {
    scope: f.security.replace(/\.SH$|\.SZ$/, ""),
    period: f.horizon,
    unit: "CNY_million",
    vintage: f.as_of.slice(0, 7),
    snapshot: "SNAP-001",
    security_code: f.security,
    company_id: f.security.split(".")[0] ?? f.security,
    as_of: f.as_of,
    version: f.model,
    workflow: "a-share-single-company-research",
  };
}

export function ResearchNewPage() {
  const { api } = useWorkbench();
  const [form, setForm] = useState<ResearchForm>({
    market: "",
    security: "",
    as_of: "",
    horizon: "",
    model: "",
    budget: "",
  });
  const [launching, setLaunching] = useState(false);
  const [result, setResult] = useState<ResearchLaunchResult | null>(null);

  const missing = useMemo(
    () => FORM_FIELDS.filter((f) => !form[f.key].trim()).map((f) => f.label),
    [form],
  );

  const onFieldChange = useCallback((key: keyof ResearchForm, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setResult(null);
  }, []);

  const onLaunch = useCallback(async () => {
    if (missing.length > 0) return; // 缺字段不触发启动（①）
    setLaunching(true);
    setResult(null);
    try {
      // ② 判定权在后端：mock 校验字段完整性，http 由后端拒绝
      setResult(await api.launchResearch(toContract(form)));
    } catch (err) {
      setResult({
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setLaunching(false);
    }
  }, [api, form, missing.length]);

  return (
    <div className="page">
      <h1>新建研究</h1>
      <Card title="研究参数">
        {FORM_FIELDS.map((f) => (
          <label key={f.key} className="form-field">
            <span className="form-field__label">{f.label}</span>
            <input
              data-testid={`form-${f.key}`}
              className="form-field__input"
              type={f.key === "as_of" ? "date" : "text"}
              placeholder={f.placeholder}
              value={form[f.key]}
              onChange={(e) => onFieldChange(f.key, e.target.value)}
            />
          </label>
        ))}
      </Card>

      {missing.length > 0 && (
        <div className="launch-gate" data-testid="launch-gate" data-state="INCOMPLETE">
          <strong>ResearchContract 不完整，不能启动：</strong>
          <span>缺字段 —— {missing.join("、")}</span>
        </div>
      )}

      {missing.length === 0 && result === null && (
        <div className="launch-gate" data-testid="launch-gate" data-state="COMPLETE">
          <span>ResearchContract 字段齐备（校验以启动结果为准）</span>
        </div>
      )}

      {result !== null && (
        <div
          className="launch-gate"
          data-testid="launch-gate"
          data-state={result.ok ? "LAUNCHED" : "FAILED"}
        >
          {result.ok ? (
            <span>
              研究已启动 —— run_id: {result.run_id} · state: {result.state}
            </span>
          ) : (
            <ErrorState message={result.error} />
          )}
        </div>
      )}

      <button
        type="button"
        data-testid="launch-button"
        className="launch-button"
        disabled={missing.length > 0 || launching}
        onClick={onLaunch}
      >
        {launching ? "启动中…" : "启动研究"}
      </button>
    </div>
  );
}
