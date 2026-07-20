import { Container, Main, Shell, Tabs, ThemeToggle, Topbar } from "@open-lzt/ui";
import { useEffect, useState, type ReactNode } from "react";
import { fetchPanelTabs, type PanelTab } from "./tabs";
import "./panel-shell.css";

export interface PanelShellProps {
  /** Rendered for the active tab key. `goTo` lets a tab's own content send the operator to another
   * tab — an empty state that names the action which fills it has to be able to offer it. */
  renderTab: (key: string, goTo: (key: string) => void) => ReactNode;
  /** Tab keys this build can actually render — a tab the frontend has no content for is dropped
   * rather than shown broken. */
  supported: ReadonlySet<string>;
  headerRight?: ReactNode;
}

export function PanelShell({ renderTab, supported, headerRight }: PanelShellProps) {
  const [tabs, setTabs] = useState<PanelTab[] | null>(null);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPanelTabs()
      .then((all) => {
        if (cancelled) return;
        const usable = all.filter((tab) => supported.has(tab.key));
        setTabs(usable);
        setActive((current) => current ?? usable[0]?.key ?? null);
      })
      .catch(() => {
        if (!cancelled) setTabs([]);
      });
    return () => {
      cancelled = true;
    };
    // `supported` is a module-level constant at every call site; listing it would re-fetch the tab
    // strip on each render if a caller ever passed an inline Set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Shell className="panel-shell">
      <Topbar className="panel-shell__topbar">
        <Container className="panel-shell__topbar-inner">
          <span className="panel-shell__brand">
            auto<span className="panel-shell__brand-accent">-lzt</span>
          </span>
          {tabs && tabs.length > 0 && active ? (
            <Tabs
              items={tabs.map((tab) => ({ value: tab.key, label: tab.title }))}
              value={active}
              onChange={setActive}
              className="panel-shell__tabs"
            />
          ) : null}
          <div className="panel-shell__topbar-right">
            {headerRight}
            <ThemeToggle />
          </div>
        </Container>
      </Topbar>
      <Main className="panel-shell__main">
        {/* Keyed by tab so switching remounts and replays the entrance animation instead of
            swapping content in place, which reads as a jump cut. */}
        {active ? (
          <div key={active} className="panel-shell__tab-content">
            {renderTab(active, setActive)}
          </div>
        ) : null}
      </Main>
    </Shell>
  );
}
