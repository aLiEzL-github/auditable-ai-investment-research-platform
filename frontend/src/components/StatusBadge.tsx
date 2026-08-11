// 状态徽标 —— 文本先行，颜色只是附加（E-4：不得仅以颜色区分）

import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "ok" | "warn" | "fail";

export function StatusBadge({
  tone,
  label,
  detail,
}: {
  tone: BadgeTone;
  label: string;
  detail?: ReactNode;
}) {
  return (
    <span className={`status-badge status-badge--${tone}`} data-testid="status-badge">
      <span className="status-badge__label">{label}</span>
      {detail != null && <span className="status-badge__detail">{detail}</span>}
    </span>
  );
}
