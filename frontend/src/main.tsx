import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { WorkbenchProvider } from "./state/WorkbenchContext";
import "./styles/base.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WorkbenchProvider>
      <RouterProvider router={router} />
    </WorkbenchProvider>
  </StrictMode>,
);
