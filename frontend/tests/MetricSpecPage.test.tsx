// G5-03 MetricSpec 页验收：20 指标 origin + 冻结哈希

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { MetricSpecPage } from "../src/pages/MetricSpecPage";
import { MockApi } from "../src/api/mock";

function renderPage() {
  return render(
    <WorkbenchProvider api={new MockApi()}>
      <MetricSpecPage />
    </WorkbenchProvider>,
  );
}

describe("MetricSpecPage（G5-03）", () => {
  it("指标行数可机检（⑨）：表头显示 N/20", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/指标清单 —— \d+\/20 项/)).toBeInTheDocument();
      const rows = screen.getAllByTestId("metric-row");
      expect(rows.length).toBeGreaterThan(0);
    });
  });

  it("每行显示 metric_id + expected_origin + caliber", async () => {
    renderPage();
    await waitFor(() => {
      const rows = screen.getAllByTestId("metric-row");
      const first = rows[0].textContent!;
      expect(first).toContain("expected_origin".length ? "营业收入" : "");
      expect(rows[0].children[1].textContent).toMatch(/REPORTED|DERIVED/);
      expect(rows[0].children[2].textContent!.length).toBeGreaterThan(0);
    });
  });

  it("冻结哈希恒定可见", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/冻结哈希: [0-9a-f]{64}/)).toBeInTheDocument();
    });
  });
});
