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
import "./app.css";

const DEFAULT_FLOW_NAME = "Мой флоу";

type ViewMode = "canvas" | "history" | "composites";

// With the builder off there is no composites view to reach: the tab is not rendered, and this
// keeps a stale `view` value (or a future deep link) from landing on one anyway.
function isReachable(view: ViewMode): boolean {
  return BUILDER_ENABLED || view !== "composites";
}

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
  const [view, setView] = useState<ViewMode>("canvas");
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

  return (
    <AuthGate>
      <div className="app">
        <header className="app__header">
          {/* Name + publish belong to a flow. In the composites view they act on whichever flow
              happens to be open behind it — a foot-gun, so they are not shown there. */}
          {view === "composites" ? (
            <span className="app__section-title">Составные блоки</span>
          ) : BUILDER_ENABLED ? (
            <input
              className="app__flow-name"
              value={flowName}
              onChange={(e) => setFlowName(e.target.value)}
              aria-label="Название флоу"
            />
          ) : (
            <span className="app__section-title">{flowName}</span>
          )}
          <nav className="app__view-tabs" aria-label="Режим просмотра">
            <button
              type="button"
              className={view === "canvas" ? "app__view-tab app__view-tab--active" : "app__view-tab"}
              onClick={() => setView("canvas")}
            >
              Флоу
            </button>
            <button
              type="button"
              className={view === "history" ? "app__view-tab app__view-tab--active" : "app__view-tab"}
              onClick={() => setView("history")}
            >
              История
            </button>
            {BUILDER_ENABLED ? (
              <button
                type="button"
                className={view === "composites" ? "app__view-tab app__view-tab--active" : "app__view-tab"}
                onClick={() => setView("composites")}
              >
                Составные блоки
              </button>
            ) : null}
          </nav>
          <div className="app__header-right">
            {switchError ? <span className="app__switch-error">{switchError}</span> : null}
            {view === "composites" ? null : (
              <>
                {triggerUnknown ? (
                  <span className="app__trigger-warning" role="alert">
                    Триггер этого флоу неизвестен клиенту и будет пересоздан как «вручную» при
                    публикации — задайте расписание/событие заново.
                  </span>
                ) : null}
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
              </>
            )}
          </div>
        </header>
        <main className="app__body">
          {/* The flow list is the spine of both working views — switching to История used to drop
              it and leave the screen chrome-less, with no way to pick another flow. */}
          {view === "canvas" || view === "history" ? (
            <FlowList
              activeFlowId={flowId}
              onSelect={(id) => void handleSelectFlow(id)}
              onCreateNew={handleCreateNew}
              reloadToken={deploys}
              canAuthor={BUILDER_ENABLED}
            />
          ) : null}
          {view === "canvas" ? (
            <>
              <FlowCanvas
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                setNodes={setNodes}
                setEdges={setEdges}
                readOnly={!BUILDER_ENABLED}
              />
            </>
          ) : null}
          {view === "history" ? (
            flowId ? (
              <HistoryPanel flowId={flowId} />
            ) : (
              <div className="app__view-empty">
                <div className="empty-prompt">
                  <p className="empty-prompt__title">История пуста</p>
                  <p className="empty-prompt__hint">
                    Выберите или сохраните флоу — здесь появятся его запуски.
                  </p>
                  <button type="button" className="empty-prompt__action" onClick={() => setView("canvas")}>
                    К флоу
                  </button>
                </div>
              </div>
            )
          ) : null}
          {view === "composites" && isReachable(view) ? (
            <>
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
                <div className="app__view-empty">
                  <div className="empty-prompt">
                    <p className="empty-prompt__title">Составной блок не выбран</p>
                    <p className="empty-prompt__hint">
                      Соберите свой блок из нескольких шагов и переиспользуйте его в любом флоу.
                    </p>
                    <button
                      type="button"
                      className="empty-prompt__action"
                      onClick={handleCreateNewComposite}
                    >
                      Создать блок
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : null}
        </main>
      </div>
    </AuthGate>
  );
}
