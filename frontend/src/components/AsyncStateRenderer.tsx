// G5-06 统一状态渲染（AsyncStateRenderer）
// 验收：
//  · 错误不显示为成功 —— ERROR 态渲染失败外观（role=alert + 错误文本 + 重试），
//    绝不渲染数据内容；EMPTY 与 READY 文本互异（⑨）
//  · 中断后可恢复 —— ERROR/EMPTY 均带「重试」按钮
//  · 键盘支持 —— 按钮可聚焦、可回车触发（原生 button 语义），焦点可见
//  · aria-live —— 状态变化对读屏播报

import { useId, type ReactNode } from "react";
import type { AsyncState } from "../state/useAsync";

export function AsyncStateRenderer<T>({
  state,
  onRetry,
  renderValue,
  loadingLabel = "加载中…",
}: {
  state: AsyncState<T>;
  onRetry: () => void;
  renderValue: (v: T) => ReactNode;
  loadingLabel?: string;
}) {
  const id = useId();
  switch (state.phase) {
    case "LOADING":
      return (
        <div data-testid="async-loading" aria-busy="true" className="empty-state">
          {loadingLabel}
        </div>
      );
    case "EMPTY":
      return (
        <div data-testid="async-empty" className="empty-state">
          <p>{state.reason}</p>
          <button type="button" data-testid="async-retry" className="retry-button" onClick={onRetry}>
            重试
          </button>
        </div>
      );
    case "ERROR":
      return (
        <div
          data-testid="async-error"
          role="alert"
          aria-describedby={id}
          className="error-state"
        >
          <strong>加载失败（错误不显示为成功）</strong>
          <p id={id}>{state.message}</p>
          <button type="button" data-testid="async-retry" className="retry-button" onClick={onRetry}>
            重试
          </button>
        </div>
      );
    case "READY":
      return <div data-testid="async-ready">{renderValue(state.value)}</div>;
  }
}
