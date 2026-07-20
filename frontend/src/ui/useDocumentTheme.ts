import { useEffect, useState } from "react";

export type DocumentTheme = "light" | "dark";

function read(): DocumentTheme {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/**
 * The current theme, read from the `data-theme` attribute on `<html>`.
 *
 * The kit exports `useTheme`, and this is NOT a reimplementation of it — it answers a different
 * question. `useTheme` reads React context and THROWS outside a `ThemeProvider`, which makes every
 * component that calls it unrenderable on its own; `FlowCanvas` is mounted bare in several tests and
 * would take them all down. More importantly `data-theme` is already the real source of truth: every
 * `--lzt-*` token resolves against it, so a component that needs to hand the theme to a
 * non-CSS consumer (React Flow paints its chrome from JS) should read the same thing the stylesheet
 * does rather than a parallel copy that can disagree with it.
 *
 * The observer is not decoration: without it a component using this hook keeps its old value after a
 * toggle, because it subscribes to no context and therefore never re-renders.
 */
export function useDocumentTheme(): DocumentTheme {
  const [theme, setTheme] = useState<DocumentTheme>(read);

  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    // The attribute is stamped in ThemeProvider's own effect, which may land after this one.
    setTheme(read());
    return () => observer.disconnect();
  }, []);

  return theme;
}
