import { ThemeProvider, ToastProvider } from "@open-lzt/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// @open-lzt/ui asks for Inter in --lzt-font but ships no @font-face — dropping these silently
// falls the app back to Segoe UI.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@xyflow/react/dist/style.css";
// Order load-bearing: must land before index.css, which aliases tokens onto it.
import "@open-lzt/ui/lzt-ui.css";
// Side-effect import (injects <symbol id="i-*"> icon sprite) — exports nothing, must not be tree-shaken.
import "@open-lzt/ui/lzt-icons.js";
import "./index.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("root element not found");
}

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider defaultTheme="dark">
      <ToastProvider>
        <App />
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
);
