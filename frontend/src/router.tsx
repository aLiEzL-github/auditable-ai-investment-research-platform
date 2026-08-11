// 路由（G5-01 交付件「路由」）—— 路由骨架按 Gate 布局，G5-02~05 逐 Gate 填充

import { createBrowserRouter, Navigate } from "react-router-dom";
import { Shell } from "./components/Shell";
import {
  AuditPage,
  ClaimsPage,
  EvidencePage,
  MacroPage,
  MetricSpecPage,
  ResearchNewPage,
  RulesPage,
} from "./pages/Pages";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/research/new" replace /> },
      { path: "research/new", element: <ResearchNewPage /> },
      { path: "evidence", element: <EvidencePage /> },
      { path: "rules", element: <RulesPage /> },
      { path: "metrics", element: <MetricSpecPage /> },
      { path: "macro", element: <MacroPage /> },
      { path: "claims", element: <ClaimsPage /> },
      { path: "audit", element: <AuditPage /> },
      { path: "audit/eligibility", element: <AuditPage /> },
    ],
  },
]);
