import { ThemeProvider, ToastProvider } from "@open-lzt/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// The @fontsource imports STAY. @open-lzt/ui asks for Inter in --lzt-font but ships no @font-face
// of its own (it used to ship four pointing at files that were never added to that repo, so they
// 404'd); supplying the face is the consumer's job. Dropping these would silently fall the whole
// app back to Segoe UI. Self-hosted woff2, cyrillic subset, never a render-blocking CDN @import.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@xyflow/react/dist/style.css";
// Order is load-bearing: the design system's tokens must land BEFORE index.css, which aliases this
// app's own token names onto them. Reversed, the aliases resolve against nothing.
import "@open-lzt/ui/lzt-ui.css";
// The icon sprite injects the <symbol id="i-*"> set into the document on import. Without it
// every <Icon name="…"> renders an empty <svg><use href="#i-…"> — no error, no glyph, just a
// gap. A side-effect import: it exports nothing, so it must never be tree-shaken away.
import "@open-lzt/ui/lzt-icons.js";
import "./index.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("root element not found");
}

createRoot(rootEl).render(
  <StrictMode>
    {/* ThemeProvider stamps data-theme on <html>, which every --lzt-* token keys off, so it wraps
        everything that renders. The light theme arrives at zero cost here: the kit already defines
        the full light token set and persists the choice to localStorage. */}
    <ThemeProvider defaultTheme="dark">
      <ToastProvider>
        <App />
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
);
