// G5-04 三情景页验收：PESSIMISTIC/BASE/OPTIMISTIC + 触发器

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { ScenariosPage } from "../src/pages/ScenariosPage";
import { MockApi } from "../src/api/mock";

function renderPage() {
  return render(
    <WorkbenchProvider api={new MockApi()}>
      <ScenariosPage />
    </WorkbenchProvider>,
  );
}

describe("ScenariosPage（G5-04）", () => {
  it("三情景齐备且各带触发器", async () => {
    renderPage();
    await waitFor(() => {
      const rows = screen.getAllByTestId("scenario-row");
      const names = rows.map((r) => r.getAttribute("data-scenario"));
      expect(names).toContain("PESSIMISTIC");
      expect(names).toContain("BASE");
      expect(names).toContain("OPTIMISTIC");
      for (const r of rows) {
        expect(r.textContent).toContain("触发器".length ? "变动" : "");
      }
    });
  });

  it("情景区间的低-高与每股价格可见", async () => {
    renderPage();
    await waitFor(() => {
      const base = screen.getAllByTestId("scenario-row").find(
        (r) => r.getAttribute("data-scenario") === "BASE",
      )!;
      expect(base.textContent).toContain("22.0");
      expect(base.textContent).toContain("25.5");
      expect(base.textContent).toContain("23.8");
    });
  });
});
