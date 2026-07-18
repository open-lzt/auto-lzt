import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// Fonts + React Flow base styles are imported here (not chained via CSS @import) so Vite bundles
// the self-hosted Inter woff2 (cyrillic subset included, font-display: swap) without a
// render-blocking @import cascade.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@xyflow/react/dist/style.css";
import "./index.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("root element not found");
}

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
