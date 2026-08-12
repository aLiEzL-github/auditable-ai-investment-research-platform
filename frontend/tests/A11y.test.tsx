// G5-06 无障碍/键盘支持验收

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { Shell } from "../src/components/Shell";

describe("G5-06 键盘支持与无障碍", () => {
  it("skip link 存在，聚焦时可见（键盘用户可达主内容）", () => {
    render(
      <WorkbenchProvider>
        <MemoryRouter>
          <Shell />
        </MemoryRouter>
      </WorkbenchProvider>,
    );
    const link = screen.getByTestId("skip-link");
    expect(link.getAttribute("href")).toBe("#main-content");
    expect(link.className).toContain("skip-link");
  });

  it("主内容区有 id=main-content（skip link 目标）", () => {
    render(
      <WorkbenchProvider>
        <MemoryRouter>
          <Shell />
        </MemoryRouter>
      </WorkbenchProvider>,
    );
    const main = document.getElementById("main-content");
    expect(main).not.toBeNull();
  });

  it("阻断横幅 aria-live（状态变化对读屏播报）", () => {
    render(
      <WorkbenchProvider>
        <MemoryRouter>
          <Shell />
        </MemoryRouter>
      </WorkbenchProvider>,
    );
    const banner = screen.getByTestId("release-status");
    expect(banner.getAttribute("aria-live")).toBe("polite");
  });
});
