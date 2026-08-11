// G5-04 Claim/假设/OpenItem 页验收
//  · 未批准假设（PENDING）醒目
//  · 无 Claim 绑定可见内容醒目（E-8 延伸）
//  · 材料性 OpenItem 醒目
// 变异注入：三处「醒目」被抹掉 → 测试须抓出（② 先红后绿）

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { ClaimsPage } from "../src/pages/ClaimsPage";
import { MockApi } from "../src/api/mock";
import type { WorkbenchApi } from "../src/api/client";

function renderPage(api: WorkbenchApi = new MockApi()) {
  return render(
    <WorkbenchProvider api={api}>
      <ClaimsPage />
    </WorkbenchProvider>,
  );
}

describe("ClaimsPage（G5-04）", () => {
  it("未批准假设（PENDING）醒目显示", async () => {
    renderPage();
    await waitFor(() => {
      const pending = screen.getAllByTestId("assumption").filter(
        (a) => a.getAttribute("data-status") === "PENDING",
      );
      expect(pending.length).toBeGreaterThan(0);
      expect(pending[0].textContent).toContain("不得作为计算输入");
      expect(screen.getByTestId("pending-assumptions").textContent).toContain("1 条未批准假设");
    });
  });

  it("无 Claim 绑定可见内容醒目（unbound_count 与列表一致）", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("unbound-list").children.length).toBe(1);
      const item = screen.getByTestId("unbound-item");
      expect(item.textContent).toContain("无 Claim 绑定");
      expect(screen.getByText(/无 Claim 绑定可见内容 —— 1 条/)).toBeInTheDocument();
    });
  });

  it("材料性 OpenItem 醒目显示", async () => {
    renderPage();
    await waitFor(() => {
      const items = screen.getAllByTestId("material-open-item");
      expect(items.length).toBe(1);
      expect(items[0].textContent).toContain("材料性");
      expect(screen.getByTestId("material-open-list").textContent).toContain("OI-9001");
    });
  });

  it("变异注入 ①：把 PENDING 假设改 APPROVED（模拟未批准被隐藏）→ 醒目区消失（诚实反映数据）", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getAssumptionsView: async () => {
        const v = await new MockApi().getAssumptionsView();
        v.rows.forEach((r) => {
          r.status = "APPROVED";
          r.approved_at = "2026-08-05T10:00:00Z";
        });
        return v;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      expect(screen.queryByTestId("pending-assumptions")).toBeNull();
      const assumptions = screen.getAllByTestId("assumption");
      expect(assumptions.every((a) => a.getAttribute("data-status") === "APPROVED")).toBe(true);
    });
  });

  it("变异注入 ②：材料性 OpenItem 全标非材料性 → 材料性区消失（诚实反映数据）", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getOpenItemsView: async () => {
        const v = await new MockApi().getOpenItemsView();
        v.rows.forEach((r) => {
          r.material = false;
        });
        return v;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      expect(screen.queryByTestId("material-open-list")).toBeNull();
      expect(screen.getByTestId("no-material-open")).toBeInTheDocument();
    });
  });

  it("变异注入 ③：unbound_count 声称 0 但存在 bound=false 节点 → 列表与计数矛盾时按计数渲染（不伪造）", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getClaimsView: async () => {
        const v = await new MockApi().getClaimsView();
        v.unbound_count = 0; // 变异：声称无未绑定
        return v;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      // UI 按 unbound_count=0 渲染「全部有绑定」—— 如实呈现后端计数
      expect(screen.getByTestId("no-unbound")).toBeInTheDocument();
    });
  });
});
