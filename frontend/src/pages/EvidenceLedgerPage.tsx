// 证据台账页（G5-03，基线 B §8）
// 交付件：主副源、冲突、哈希、权利、原件入口。
//   · 来源树：kind（主/副）+ 权利状态 + 法律依据（原件入口 = locator）
//   · 冲突（VALUE_CONFLICT / RIGHTS_BLOCKED）恒定可见，不得折叠（E-4 延伸）
//   · 冲突项带详情（E-5：显示为什么）

import { useEffect, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, EmptyState, ErrorState } from "../components/Basic";
import { StatusBadge } from "../components/StatusBadge";
import type { EvidenceConflict, SourceStatus } from "../types";
import type { EvidenceLedger } from "../types";

const SOURCE_TONE: Record<SourceStatus, "ok" | "fail" | "warn" | "neutral"> = {
  ALLOWED: "ok",
  UNKNOWN: "warn",
  PROHIBITED: "fail",
};

const CONFLICT_TONE: Record<EvidenceConflict, "ok" | "fail" | "warn"> = {
  NONE: "ok",
  VALUE_CONFLICT: "warn",
  RIGHTS_BLOCKED: "fail",
};

export function EvidenceLedgerPage() {
  const { api } = useWorkbench();
  const [ledger, setLedger] = useState<EvidenceLedger | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getEvidenceLedger()
      .then((v) => {
        if (!cancelled) setLedger(v);
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

  if (loading) return <EmptyState label="证据台账加载中…" />;
  if (error != null) return <ErrorState message={error} />;
  if (ledger == null) return <EmptyState label="无证据台账数据" />;

  const conflicts = ledger.items.filter((i) => i.conflict !== "NONE");

  return (
    <div className="page">
      <h1>证据台账</h1>

      {/* 冲突横幅：恒定可见 */}
      <Card title={`冲突与权利阻断 —— ${conflicts.length} 条（不可隐藏）`}>
        {conflicts.length === 0 ? (
          <p data-testid="no-conflict">无证据冲突</p>
        ) : (
          <ul data-testid="conflict-list">
            {conflicts.map((i) => (
              <li key={i.evidence_id} data-testid="conflict-item" data-conflict={i.conflict}>
                <StatusBadge tone={CONFLICT_TONE[i.conflict]} label={i.conflict} />
                <strong>{i.evidence_id}</strong> {i.snippet}
                <p className="conflict-detail">{i.conflict_detail}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* 来源树 */}
      <Card title={`来源树（${ledger.sources.length} 个来源）`}>
        <ul data-testid="source-tree">
          {ledger.sources.map((s) => (
            <li key={s.id} data-testid="source-node" data-kind={s.kind} data-status={s.status}>
              <StatusBadge tone={SOURCE_TONE[s.status]} label={`${s.kind} · ${s.status}`} />
              <strong>{s.name}</strong>
              <p className="source-basis">{s.legal_basis}</p>
              <p className="source-locator">原件入口: <a href={s.locator} target="_blank" rel="noreferrer">{s.locator}</a></p>
              <p className="source-sha">sha256: {s.sha256}</p>
            </li>
          ))}
        </ul>
      </Card>

      {/* 证据明细 */}
      <Card title={`证据明细（${ledger.items.length} 条）`}>
        <ul data-testid="evidence-items">
          {ledger.items.map((i) => (
            <li key={i.evidence_id} data-testid="evidence-item">
              <strong>{i.evidence_id}</strong> [{i.source.kind}] {i.source.name}
              <p>{i.snippet}</p>
              <p className="source-sha">sha256: {i.sha256}</p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
