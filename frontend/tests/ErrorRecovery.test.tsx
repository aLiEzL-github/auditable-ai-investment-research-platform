// G5-06 错误、取消、恢复和无障碍验收（基线 B §8）
// 验收：错误不显示为成功；中断后可恢复。
// 交付件：空态、失败态、重试、键盘支持。
// 变异注入：把失败态渲染成成功态 → 测试须抓出。

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { AsyncStateRenderer } from "../src/components/AsyncStateRenderer";
import { ErrorBoundary } from "../src/components/ErrorBoundary";
import { RulesPage } from "../src/pages/RulesPage";
import { MockApi } from "../src/api/mock";
import type { WorkbenchApi } from "../src/api/client";

function ThrowingChild(): never {
  throw new Error("boom-render");
}

describe("G5-06 错误不显示为成功", () => {
  it("渲染期异常 → ErrorBoundary 失败态（role=alert + 错误文本），不显示成功内容", () => {
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    const el = screen.getByTestId("error-boundary");
    expect(el.getAttribute("role")).toBe("alert");
    expect(el.textContent).toContain("页面渲染失败");
    expect(el.textContent).toContain("boom-render");
    // 失败态不得渲染正常内容（ThrowingChild 的唯一输出是抛错，无内容可泄漏）
    expect(el.textContent).not.toContain("FABRICATED");
    expect(screen.getByTestId("error-boundary-retry")).toBeInTheDocument();
  });

  it("中断后可恢复：ErrorBoundary 重试 → 子树重新渲染", () => {
    let boom = true;
    function Flaky(): JSX.Element {
      if (boom) throw new Error("boom");
      return <div data-testid="recovered">recovered</div>;
    }
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary")).toBeInTheDocument();
    boom = false;
    fireEvent.click(screen.getByTestId("error-boundary-retry"));
    expect(screen.getByTestId("recovered")).toBeInTheDocument();
  });
});

describe("G5-06 AsyncStateRenderer", () => {
  it("ERROR 态：role=alert + 重试按钮，且不渲染数据内容（错误不显示为成功）", () => {
    render(
      <AsyncStateRenderer
        state={{ phase: "ERROR", message: "net fail" }}
        onRetry={() => {}}
        renderValue={() => <div>FABRICATED-SUCCESS</div>}
      />,
    );
    const el = screen.getByTestId("async-error");
    expect(el.getAttribute("role")).toBe("alert");
    expect(el.textContent).toContain("net fail");
    expect(el.textContent).toContain("加载失败");
    expect(el.textContent).not.toContain("FABRICATED-SUCCESS");
    expect(screen.getByTestId("async-retry")).toBeInTheDocument();
  });

  it("EMPTY 与 READY 可分辨（⑨）：文本互异、testid 不同", () => {
    const { unmount } = render(
      <AsyncStateRenderer
        state={{ phase: "EMPTY", reason: "无数据" }}
        onRetry={() => {}}
        renderValue={() => null}
      />,
    );
    expect(screen.getByTestId("async-empty")).toHaveTextContent("无数据");
    unmount();
    render(
      <AsyncStateRenderer
        state={{ phase: "READY", value: 42 }}
        onRetry={() => {}}
        renderValue={(v) => <span data-testid="ready-val">{v}</span>}
      />,
    );
    expect(screen.getByTestId("ready-val")).toHaveTextContent("42");
    expect(screen.queryByTestId("async-empty")).toBeNull();
  });

  it("变异注入 ①: ERROR 载荷被错误渲染为成功内容（renderValue 泄漏）→ 断言必须抓住", () => {
    // 模拟一个错误的实现：把 ERROR 直接渲染 renderValue —— 测试端验证守卫逻辑存在
    render(
      <AsyncStateRenderer
        state={{ phase: "ERROR", message: "x" }}
        onRetry={() => {}}
        renderValue={() => <div>FABRICATED-SUCCESS</div>}
      />,
    );
    expect(screen.queryByText("FABRICATED-SUCCESS")).toBeNull();
  });
});

describe("G5-06 中断后可恢复（页面级）", () => {
  it("RulesPage 后端失败 → 失败态 + 重试；修复后重试 → 数据恢复", async () => {
    let fail = true;
    const flaky: WorkbenchApi = Object.assign(new MockApi(), {
      getRulesView: async () => {
        if (fail) throw new Error("interrupted");
        return new MockApi().getRulesView();
      },
    });
    render(
      <WorkbenchProvider api={flaky}>
        <RulesPage />
      </WorkbenchProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("async-error")).toHaveTextContent("interrupted");
      expect(screen.queryByTestId("rules-table")).toBeNull();
    });
    fail = false;
    fireEvent.click(screen.getByTestId("async-retry"));
    await waitFor(() => {
      expect(screen.getByTestId("rules-table")).toBeInTheDocument();
    });
  });
});
