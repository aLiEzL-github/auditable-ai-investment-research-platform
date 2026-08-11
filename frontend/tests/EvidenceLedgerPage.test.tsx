// G5-03 证据台账页验收
//  · 冲突与权利阻断不可隐藏（E-4 延伸）：恒定渲染、不得折叠
//  · 冲突显示详情（E-5）
//  · 来源树：kind（主/副）+ 权利状态 + 法律依据 + 原件入口 + 哈希

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { EvidenceLedgerPage } from "../src/pages/EvidenceLedgerPage";
import { MockApi } from "../src/api/mock";

function renderPage() {
  return render(
    <WorkbenchProvider api={new MockApi()}>
      <EvidenceLedgerPage />
    </WorkbenchProvider>,
  );
}

describe("EvidenceLedgerPage（G5-03）", () => {
  it("冲突与权利阻断恒定可见，带详情", async () => {
    renderPage();
    await waitFor(() => {
      const items = screen.getAllByTestId("conflict-item");
      expect(items.length).toBe(2);
      const conflicts = items.map((i) => i.getAttribute("data-conflict"));
      expect(conflicts).toContain("VALUE_CONFLICT");
      expect(conflicts).toContain("RIGHTS_BLOCKED");
    });
    expect(screen.getByText(/主源 52.3 亿元 vs 副源 52.0 亿元/)).toBeInTheDocument();
    expect(screen.getByText(/automated_bulk_acquisition 未获授权/)).toBeInTheDocument();
  });

  it("来源树：kind（主/副）+ 权利状态 + 依据 + 原件入口 + sha256", async () => {
    renderPage();
    await waitFor(() => {
      const nodes = screen.getAllByTestId("source-node");
      expect(nodes.length).toBe(2);
      const kinds = nodes.map((n) => n.getAttribute("data-kind"));
      expect(kinds).toContain("PRIMARY");
      expect(kinds).toContain("SECONDARY");
      const statuses = nodes.map((n) => n.getAttribute("data-status"));
      expect(statuses).toContain("ALLOWED");
      expect(statuses).toContain("UNKNOWN");
      const text = screen.getAllByTestId("source-node")[0].textContent!;
      expect(text).toContain("ALLOWED");
      expect(screen.getAllByText(/sha256:/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/原件入口/).length).toBeGreaterThan(0);
    });
  });

  it("权利阻断（UNKNOWN）以醒目文本区分，不只靠颜色", async () => {
    renderPage();
    await waitFor(() => {
      const nodes = screen.getAllByTestId("source-node");
      const unknown = nodes.find((n) => n.getAttribute("data-status") === "UNKNOWN");
      expect(unknown).toBeDefined();
      expect(unknown!.textContent).toContain("UNKNOWN");
      expect(unknown!.textContent).toContain("automated_bulk_acquisition");
    });
  });
});
