// G5-01 外壳验收：E-9 首屏声明、路由骨架、mock schema 独立开发

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, RouterProvider, createMemoryRouter } from "react-router-dom";
import { Shell } from "../src/components/Shell";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { router } from "../src/router";

function renderShell() {
  return render(
    <WorkbenchProvider>
      <MemoryRouter>
        <Shell />
      </MemoryRouter>
    </WorkbenchProvider>,
  );
}

describe("Shell (G5-01)", () => {
  it("E-9: SINGLE_REVIEWER_ATTESTED 首屏可见（header 之下主区域之前，不在页脚/折叠区）", () => {
    renderShell();
    const el = screen.getByTestId("attestation");
    expect(el.textContent).toContain("SINGLE_REVIEWER_ATTESTED");
    expect(getComputedStyle(el).display).not.toBe("none");
    const header = document.querySelector(".shell__header");
    const main = document.querySelector(".shell__main");
    const footer = document.querySelector("footer");
    expect(footer).toBeNull();
    if (header && main) {
      expect(header.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(el.compareDocumentPosition(main!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("E-6: 首屏阻断态横幅以「尚未检查」为初始态（未检查 ≠ 已检查无阻断）", () => {
    renderShell();
    expect(screen.getByTestId("release-status").getAttribute("data-status")).toBe("NOT_CHECKED");
  });

  it("路由骨架：/research/new 有导航入口", () => {
    renderShell();
    expect(screen.getByRole("link", { name: "新建研究" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "证据台账" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "审计与发布" })).toBeInTheDocument();
  });
});

describe("router (mock schema 独立开发)", () => {
  it("index 重定向到 /research/new", () => {
    const r = createMemoryRouter(router.routes, { initialEntries: ["/"] });
    render(
      <WorkbenchProvider>
        <RouterProvider router={r} />
      </WorkbenchProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "新建研究" })).toBeInTheDocument();
  });
});
