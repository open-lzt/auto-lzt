import { Icon, Shell, Sidenav, SidenavItem, Topbar } from "@open-lzt/ui";
import { useEffect, useState, type ReactNode } from "react";
import { ThemeToggle } from "../components/ThemeToggle";
import { fetchPanelTabs, type PanelTab } from "./tabs";
import "./panel-shell.css";

export interface PanelShellProps {
  renderTab: (key: string, goTo: (key: string) => void) => ReactNode;
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
    // supported is a module-level constant at every call site — safe to omit from deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Shell className="panel-shell">
      <Topbar className="panel-shell__topbar">
        <div className="panel-shell__topbar-inner">
          <span className="panel-shell__brand">
            auto<span className="panel-shell__brand-accent">-lzt</span>
          </span>
          <div className="panel-shell__topbar-right">
            {headerRight}
            <ThemeToggle />
          </div>
        </div>
      </Topbar>

      <div className="panel-shell__body">
        <Sidenav className="panel-shell__nav" label="Разделы">
          {(tabs ?? []).map((tab) => (
            <SidenavItem
              key={tab.key}
              href="#"
              active={tab.key === active}
              className="panel-shell__nav-item"
              // Accessible name: the label is hidden on phones (icon-only strip).
              title={tab.title}
              aria-label={tab.title}
              // Stable hook for the responsive audit — label text is hidden on phones.
              data-key={tab.key}
              onClick={(e) => {
                e.preventDefault();
                setActive(tab.key);
              }}
            >
              {tab.icon ? <Icon name={tab.icon} size={16} /> : null}
              <span className="panel-shell__nav-label">{tab.title}</span>
            </SidenavItem>
          ))}
        </Sidenav>

        <main className="panel-shell__main">
          {active ? (
            <div key={active} className="panel-shell__tab-content">
              {renderTab(active, setActive)}
            </div>
          ) : null}
        </main>
      </div>
    </Shell>
  );
}
