import { Button, Empty } from "@open-lzt/ui";
import { useNodesState, useEdgesState, type Edge, type Node } from "@xyflow/react";
import { useCallback, useEffect, useState } from "react";
import { exportFlow, fetchCatalog, type CatalogNode, type FlowSpec } from "./api/flowClient";
import { AuthoringMode } from "./canvas/AuthoringMode";
import { FlowCanvas } from "./canvas/FlowCanvas";
import type { CanvasNodeData } from "./canvas/canvasTypes";
import { displayLabel } from "./canvas/labels";
import { AuthGate } from "./components/AuthGate";
import { CompositeList } from "./components/CompositeList";
import { DeployButton } from "./components/DeployButton";
import { FlowList } from "./components/FlowList";
import { LiveBadge } from "./components/LiveBadge";
import { BUILDER_ENABLED } from "./config";
import { HistoryPanel } from "./history/HistoryPanel";
import { PanelShell } from "./panel/PanelShell";
import { TaskMonitor } from "./panel/TaskMonitor";
import { AccountsView } from "./panel/features/accounts/AccountsView";
import { AutomationView } from "./panel/features/preset/AutomationView";
import { RegistryView } from "./panel/features/registry/RegistryView";
import "./app.css";

const DEFAULT_FLOW_NAME = "Мой флоу";

// The backend advertises every tab the installation has; this is the subset THIS build can render.
// Authoring is filtered out rather than hidden behind a disabled tab: a tab that exists and does
// nothing is worse than one that was never offered.
const supportedTabs: ReadonlySet<string> = new Set(
  BUILDER_ENABLED
    ? ["tasks", "automation", "accounts", "flows", "registry", "history", "composites"]
    : ["tasks", "automation", "accounts", "flows", "registry", "history"],
);

/** Rebuilds canvas nodes/edges from a saved FlowSpec (GET .../export). The spec is the domain
 * graph only — it carries neither canvas layout (positions) nor the trigger's kind, since
 * triggers are attached out-of-band via POST .../triggers and NodeSpec has no x/y field. Loaded
 * flows therefore get a synthetic "manual" trigger and an auto-stacked vertical layout; the
 * operator can drag nodes and re-attach a real trigger after loading. */
function flowSpecToCanvas(spec: FlowSpec, catalog: CatalogNode[]): { nodes: Node<CanvasNodeData>[]; edges: Edge[] } {
  const categoryByKey = new Map(catalog.map((entry) => [entry.key, entry.category]));
  const triggerId = "trigger-loaded";

  const nodes: Node<CanvasNodeData>[] = [
    {
      id: triggerId,
      type: "trigger",
      position: { x: 40, y: 40 },
      data: {
        catalogKey: "manual",
        category: "trigger",
        label: displayLabel("manual"),
        values: {},
        triggerConfig: { kind: "manual", schedule_cron: "", event_type: "" },
      },
    },
  ];
  const edges: Edge[] = [{ id: `${triggerId}->${spec.entry_node_id}`, source: triggerId, target: spec.entry_node_id }];

  spec.nodes.forEach((nodeSpec, index) => {
    const category = categoryByKey.get(nodeSpec.type) ?? "action";
    const values: Record<string, string | number | boolean> = {};
    for (const [key, input] of Object.entries(nodeSpec.inputs)) {
      if (typeof input.literal === "string" || typeof input.literal === "number" || typeof input.literal === "boolean") {
        values[key] = input.literal;
      }
    }
    nodes.push({
      id: nodeSpec.id,
      type: category,
      position: { x: 340, y: 40 + index * 120 },
      data: { catalogKey: nodeSpec.type, category, label: displayLabel(nodeSpec.type), values },
    });
    for (const [port, targetId] of Object.entries(nodeSpec.edges)) {
      edges.push({
        id: `${nodeSpec.id}-${port}->${targetId}`,
        source: nodeSpec.id,
        target: targetId,
        sourceHandle: port === "next" ? undefined : port,
      });
    }
  });

  return { nodes, edges };
}

export default function App() {
  const [nodes, setNodes, rawOnNodesChange] = useNodesState<Node<CanvasNodeData>>([]);
  const [edges, setEdges, rawOnEdgesChange] = useEdgesState<Edge>([]);
  const [flowName, setFlowName] = useState(DEFAULT_FLOW_NAME);
  const [flowId, setFlowId] = useState<string | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [activeCompositeId, setActiveCompositeId] = useState<string | null>(null);
  const [authoringCompositeId, setAuthoringCompositeId] = useState<string | null>(null);
  const [isAuthoringNew, setIsAuthoringNew] = useState(false);
  const [compositeSaves, setCompositeSaves] = useState(0);
  const [deploys, setDeploys] = useState(0);
  // Tracks unsaved canvas edits so navigation away from the current graph (new flow, switch flow,
  // tab close) can warn instead of silently discarding work.
  const [isDirty, setIsDirty] = useState(false);
  // A loaded flow's trigger is always synthesized as "manual" (see flowSpecToCanvas) because the
  // export endpoint carries no trigger kind — publishing without re-picking one silently
  // downgrades a cron/event trigger to manual. This flag drives a visible warning until the
  // operator explicitly re-assigns the trigger.
  const [triggerUnknown, setTriggerUnknown] = useState(false);

  const onNodesChange: typeof rawOnNodesChange = useCallback(
    (changes) => {
      rawOnNodesChange(changes);
      setIsDirty(true);
    },
    [rawOnNodesChange],
  );

  const onEdgesChange: typeof rawOnEdgesChange = useCallback(
    (changes) => {
      rawOnEdgesChange(changes);
      setIsDirty(true);
    },
    [rawOnEdgesChange],
  );

  useEffect(() => {
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      if (!isDirty) return;
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  function confirmDiscardIfDirty(): boolean {
    if (!isDirty) return true;
    return window.confirm("Несохранённые изменения будут потеряны. Продолжить?");
  }

  function handleCreateNew() {
    if (!confirmDiscardIfDirty()) return;
    setFlowId(null);
    setFlowName(DEFAULT_FLOW_NAME);
    setNodes([]);
    setEdges([]);
    setSwitchError(null);
    setTriggerUnknown(false);
    setIsDirty(false);
  }

  function handleSelectComposite(id: string) {
    setActiveCompositeId(id);
    setAuthoringCompositeId(id);
    setIsAuthoringNew(false);
  }

  function handleCreateNewComposite() {
    setActiveCompositeId(null);
    setAuthoringCompositeId(null);
    setIsAuthoringNew(true);
  }

  function handleAuthoringDone() {
    setAuthoringCompositeId(null);
    setIsAuthoringNew(false);
  }

  function handleCompositeSaved() {
    setCompositeSaves((n) => n + 1);
    handleAuthoringDone();
  }

  function handleDeployed(id: string) {
    setFlowId(id);
    setDeploys((n) => n + 1);
    setIsDirty(false);
    setTriggerUnknown(false);
  }

  async function handleSelectFlow(id: string) {
    if (!confirmDiscardIfDirty()) return;
    setSwitchError(null);
    try {
      const [spec, catalog] = await Promise.all([exportFlow(id), fetchCatalog()]);
      const loaded = flowSpecToCanvas(spec, catalog);
      setFlowId(id);
      setFlowName(spec.name);
      setNodes(loaded.nodes);
      setEdges(loaded.edges);
      setTriggerUnknown(true);
      setIsDirty(false);
    } catch (err) {
      setSwitchError(err instanceof Error ? err.message : "не удалось загрузить флоу");
    }
  }

  function renderFlowsTab() {
    return (
      <div className="app__workspace">
        <FlowList
          activeFlowId={flowId}
          onSelect={(id) => void handleSelectFlow(id)}
          onCreateNew={handleCreateNew}
          reloadToken={deploys}
          canAuthor={BUILDER_ENABLED}
        />
        <div className="app__workspace-main">
          <div className="app__flow-bar">
            {BUILDER_ENABLED ? (
              <input
                className="app__flow-name"
                value={flowName}
                onChange={(e) => setFlowName(e.target.value)}
                aria-label="Название флоу"
              />
            ) : (
              <span className="app__section-title">{flowName}</span>
            )}
            {switchError ? <span className="app__switch-error">{switchError}</span> : null}
            {triggerUnknown ? (
              <span className="app__trigger-warning" role="alert">
                Триггер этого флоу неизвестен клиенту и будет пересоздан как «вручную» при
                публикации — задайте расписание/событие заново.
              </span>
            ) : null}
            <div className="app__flow-bar-right">
              <LiveBadge flowId={flowId} />
              {BUILDER_ENABLED ? (
                <DeployButton
                  flowId={flowId}
                  flowName={flowName}
                  nodes={nodes}
                  edges={edges}
                  setNodes={setNodes}
                  onDeployed={handleDeployed}
                />
              ) : null}
            </div>
          </div>
          <FlowCanvas
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            setNodes={setNodes}
            setEdges={setEdges}
            readOnly={!BUILDER_ENABLED}
          />
        </div>
      </div>
    );
  }

  function renderHistoryTab(goTo: (key: string) => void) {
    if (flowId) return <HistoryPanel flowId={flowId} />;
    return (
      <div className="panel-view">
        <Empty title="История пуста">
          <p className="panel-empty__hint">
            Выберите или сохраните флоу — здесь появятся его запуски.
          </p>
          <Button variant="primary" onClick={() => goTo("flows")}>
            К флоу
          </Button>
        </Empty>
      </div>
    );
  }

  function renderCompositesTab() {
    return (
      <div className="app__workspace">
        <CompositeList
          activeCompositeId={activeCompositeId}
          onSelect={handleSelectComposite}
          onCreateNew={handleCreateNewComposite}
          reloadToken={compositeSaves}
        />
        {authoringCompositeId || isAuthoringNew ? (
          <AuthoringMode
            compositeId={authoringCompositeId}
            onSaved={handleCompositeSaved}
            onCancel={handleAuthoringDone}
          />
        ) : (
          <div className="panel-view">
            <Empty title="Составной блок не выбран">
              <p className="panel-empty__hint">
                Соберите свой блок из нескольких шагов и переиспользуйте его в любом флоу.
              </p>
              <Button variant="primary" onClick={handleCreateNewComposite}>
                Создать блок
              </Button>
            </Empty>
          </div>
        )}
      </div>
    );
  }

  return (
    <AuthGate>
      <PanelShell
        supported={supportedTabs}
        renderTab={(key, goTo) => {
          if (key === "tasks") return <TaskMonitor onGoToBuilder={() => goTo("flows")} />;
          if (key === "automation") return <AutomationView onDeployed={() => goTo("tasks")} />;
          if (key === "accounts") return <AccountsView />;
          if (key === "registry") return <RegistryView />;
          if (key === "flows") return renderFlowsTab();
          if (key === "history") return renderHistoryTab(goTo);
          if (key === "composites") return renderCompositesTab();
          return null;
        }}
      />
    </AuthGate>
  );
}
