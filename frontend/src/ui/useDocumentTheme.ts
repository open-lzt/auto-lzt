import { useEffect, useState } from "react";

export type DocumentTheme = "light" | "dark";

function read(): DocumentTheme {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** NOT a reimplementation of the kit's `useTheme` — that throws outside a `ThemeProvider`, which
 * would break `FlowCanvas` (mounted bare in several tests). Reads `data-theme` directly instead. */
export function useDocumentTheme(): DocumentTheme {
  const [theme, setTheme] = useState<DocumentTheme>(read);

  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    // ThemeProvider's own effect may stamp the attribute after this one runs.
    setTheme(read());
    return () => observer.disconnect();
  }, []);

  return theme;
}
