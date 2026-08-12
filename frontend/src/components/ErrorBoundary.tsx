// G5-06 错误边界（ErrorBoundary）
// 验收：错误不显示为成功 —— 渲染期异常必须进入失败态，不得静默或冒充成功。
// 中断后可恢复 —— 提供重试按钮（reset 后重渲染子树）。

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  onRetry?: () => void;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 上抛给全局（console 可审计），但 UI 已进入失败态
    console.error("ErrorBoundary caught:", error, info);
  }

  private handleRetry = (): void => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  render(): ReactNode {
    if (this.state.error != null) {
      return (
        <div role="alert" data-testid="error-boundary" className="error-state">
          <strong>页面渲染失败（错误不显示为成功）</strong>
          <p>{this.state.error.message}</p>
          <button
            type="button"
            data-testid="error-boundary-retry"
            className="retry-button"
            onClick={this.handleRetry}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
