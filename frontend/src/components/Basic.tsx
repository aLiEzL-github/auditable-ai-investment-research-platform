// 基础布局组件（G5-01 交付件「基础组件」）

import type { ReactNode } from "react";

export function Card({ title, children, footer }: { title?: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <section className="card" data-testid="card">
      {title != null && <h3 className="card__title">{title}</h3>}
      <div className="card__body">{children}</div>
      {footer != null && <div className="card__footer">{footer}</div>}
    </section>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="empty-state" data-testid="empty-state">
      {label}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="error-state" data-testid="error-state">
      <strong>加载失败</strong>
      <p>{message}</p>
    </div>
  );
}
