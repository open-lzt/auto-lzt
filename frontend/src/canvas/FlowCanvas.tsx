import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type OnConnect,
} from "@xyflow/react";
import { Button } from "@open-lzt/ui";
import { useDocumentTheme } from "../ui/useDocumentTheme";
import { useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";
import { fetchCatalog, type CatalogNode } from "../api/catalogClient";
import type { TriggerKind } from "../api/flowClient";
import { Loader } from "../components/Loader";
import { isInsideAccountLoop, needsAccount } from "./accountScope";
import {
  isStructuralEdgeChange,
  isStructuralNodeChange,
  useGraphHistory,
} from "./history/useGraphHistory";
import { useConnectedChain } from "./hooks/useConnectedChain";
import { displayLabel } from "./labels";
import { Inspector } from "./Inspector";
import { ActionNode } from "./nodes/ActionNode";
import { LogicNode } from "./nodes/LogicNode";
import { TemplateBoundaryNode } from "./nodes/TemplateBoundaryNode";
import { TriggerNode } from "./nodes/TriggerNode";
import { Sidebar } from "./Sidebar";
import type { CanvasNodeData, TriggerConfig } from "./canvasTypes";
import "./flow-canvas.css";

const nodeTypes = { trigger: TriggerNode, action: ActionNode, logic: LogicNode, templateBoundary: TemplateBoundaryNode };

let nextNodeSeq = 1;
// The backend validates a node id against ^\w+$ (app/domain/flow_engine/spec.py) — a dash makes
// the whole save 422, so the separator is an underscore.
function nextNodeId(prefix: string): string {
  return `${prefix}_${nextNodeSeq++}`;
}

function defaultValuesFor(entry: CatalogNode): Record<string, string | number | boolean> {
  const props = entry.input_schema.properties ?? {};
  const values: Record<string, string | number | boolean> = {};
  for (const [key, schema] of Object.entries(props)) {
    if (typeof schema.default === "string" || typeof schema.default === "number" || typeof schema.default === "boolean") {
      values[key] = schema.default;
    }
  }
  return values;
}

// nodes/edges are lifted to App.tsx (useNodesState/useEdgesState) and passed down as controlled
// props — DeployButton needs the same graph to build the Flow-JSON, and prop-passing beats adding
// a store just to share two arrays between three components.
export interface FlowCanvasProps {
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
  onNodesChange: ReturnType<typeof useNodesState<Node<CanvasNodeData>>>[2];
  onEdgesChange: ReturnType<typeof useEdgesState>[2];
  setNodes: ReturnType<typeof useNodesState<Node<CanvasNodeData>>>[1];
  setEdges: ReturnType<typeof useEdgesState>[1];
  /** "template" = the internal graph of a composite: no trigger, no nested composite (see Sidebar). */
  variant?: "flow" | "template";
  /** Shown over an empty canvas — the surface is otherwise a black void with no next step. */
  emptyHint?: string;
  /** Preview: the graph can be read and panned but not edited. Drives the BUILDER_ENABLED flag
   * (see config.ts) — a product decision, not a security boundary; the API key is what actually
   * gates mutations. */
  readOnly?: boolean;
  /** Changing this value clears the undo stack. Loading another flow is not a step back — without
   * it, Ctrl+Z after switching flows would paste the previous flow's graph over this one. */
  historyResetKey?: string | number;
  /** Keys of the flow's declared params — the inspector shows them as available `{{vars.X}}` refs. */
  varKeys?: readonly string[];
}

export function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  setNodes,
  setEdges,
  variant = "flow",
  // No «слева»: the palette is to the left only on a wide screen — below 900px it stacks ABOVE the
  // canvas, and the hint then pointed at nothing. Naming the list is true in both layouts.
  emptyHint = "Соберите флоу: начните с триггера в списке блоков, затем добавьте действия.",
  readOnly = false,
  historyResetKey,
  varKeys,
}: FlowCanvasProps) {
  const theme = useDocumentTheme();
  const [catalog, setCatalog] = useState<CatalogNode[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  useEffect(() => {
    fetchCatalog()
      .then(setCatalog)
      .catch((err: unknown) => setCatalogError(err instanceof Error ? err.message : String(err)));
  }, []);

  const history = useGraphHistory({ nodes, edges, setNodes, setEdges });
  const { capture, undo, redo, reset: resetHistory } = history;

  useEffect(() => {
    resetHistory();
  }, [historyResetKey, resetHistory]);

  // The snapshot is taken here rather than at each call site because React Flow's own edits —
  // Backspace on a selected node, a drag, a reconnect — never pass through a handler of ours.
  const handleNodesChange = useCallback<typeof onNodesChange>(
    (changes) => {
      if (isStructuralNodeChange(changes)) capture();
      onNodesChange(changes);
    },
    [capture, onNodesChange],
  );

  const handleEdgesChange = useCallback<typeof onEdgesChange>(
    (changes) => {
      if (isStructuralEdgeChange(changes)) capture();
      onEdgesChange(changes);
    },
    [capture, onEdgesChange],
  );

  const onConnect: OnConnect = useCallback(
    (connection) => {
      capture();
      setEdges((eds) => addEdge(connection, eds));
    },
    [capture, setEdges],
  );

  // Ctrl/Cmd+Z undoes, Ctrl+Shift+Z (and Ctrl+Y, the Windows habit) redoes. Bound to the window
  // rather than the canvas element: the operator's focus is usually in the inspector, and a
  // shortcut that only fires when the graph itself is focused is a shortcut nobody finds.
  useEffect(() => {
    if (readOnly) return;
    function onKeyDown(e: KeyboardEvent) {
      if (!(e.ctrlKey || e.metaKey)) return;
      // Undoing the graph while the operator is mid-word in a text field would look like the field
      // ate the text — inside an input, Ctrl+Z belongs to the browser.
      const target = e.target as HTMLElement | null;
      if (target?.isContentEditable) return;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault();
        redo();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [readOnly, undo, redo]);

  // Guards buildFlowSpec (flowClient.ts: outEdges[port] = edge.target) from silently overwriting
  // an existing wire: that map is keyed by port name, so a second edge from the same single-output
  // port (next/true/false) would replace the first with no warning. fork/join ports are per-branch
  // handle ids, so they never collide here and stay unrestricted.
  const isValidConnection = useCallback(
    (conn: Connection | Edge) => {
      if (conn.source === conn.target) return false;
      const port = conn.sourceHandle ?? "next";
      return !edges.some((e) => e.source === conn.source && (e.sourceHandle ?? "next") === port);
    },
    [edges],
  );

  const addTriggerNode = useCallback(
    (kind: TriggerKind) => {
      const id = nextNodeId("trigger");
      const config: TriggerConfig = { kind, schedule_cron: "", event_type: "" };
      const node: Node<CanvasNodeData> = {
        id,
        type: "trigger",
        position: { x: 40, y: 40 + nodes.length * 40 },
        data: { catalogKey: kind, category: "trigger", label: displayLabel(kind), values: {}, triggerConfig: config },
      };
      capture();
      setNodes((nds) => [...nds, node]);
      setSelectedId(id);
    },
    [capture, nodes.length, setNodes],
  );

  const addCatalogNode = useCallback(
    (entry: CatalogNode) => {
      const id = nextNodeId(entry.key.replace(/\W+/g, "_"));
      const node: Node<CanvasNodeData> = {
        id,
        type: entry.category,
        position: { x: 340, y: 40 + nodes.length * 40 },
        data: {
          catalogKey: entry.key,
          category: entry.category,
          label: displayLabel(entry.key),
          values: defaultValuesFor(entry),
        },
      };
      capture();
      setNodes((nds) => [...nds, node]);
      setSelectedId(id);
    },
    [capture, nodes.length, setNodes],
  );

  const selectedNode = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId]);
  const selectedCatalogEntry = useMemo(
    () => catalog?.find((c) => c.key === selectedNode?.data.catalogKey),
    [catalog, selectedNode],
  );

  const onChangeValue = useCallback(
    (key: string, value: string | number | boolean) => {
      if (!selectedId) return;
      setNodes((nds) =>
        nds.map((n) =>
          n.id === selectedId ? { ...n, data: { ...n.data, values: { ...n.data.values, [key]: value } } } : n,
        ),
      );
    },
    [selectedId, setNodes],
  );

  const onChangeAccountRef = useCallback(
    (accountId: string | null) => {
      if (!selectedId) return;
      setNodes((nds) =>
        nds.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, accountRef: accountId } } : n)),
      );
    },
    [selectedId, setNodes],
  );

  const insideAccountLoop = useMemo(
    () => (selectedNode ? isInsideAccountLoop(selectedNode.id, nodes, edges) : false),
    [selectedNode, nodes, edges],
  );

  const onChangeTrigger = useCallback(
    (patch: Partial<TriggerConfig>) => {
      if (!selectedId) return;
      setNodes((nds) =>
        nds.map((n) =>
          n.id === selectedId && n.data.triggerConfig
            ? { ...n, data: { ...n.data, triggerConfig: { ...n.data.triggerConfig, ...patch } } }
            : n,
        ),
      );
    },
    [selectedId, setNodes],
  );

  const onRenameLabel = useCallback(
    (label: string) => {
      if (!selectedId) return;
      setNodes((nds) => nds.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, label } } : n)));
    },
    [selectedId, setNodes],
  );

  const onDeleteSelected = useCallback(() => {
    if (!selectedId) return;
    capture();
    setNodes((nds) => nds.filter((n) => n.id !== selectedId));
    setEdges((eds) => eds.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }, [capture, selectedId, setNodes, setEdges]);

  const onDuplicateSelected = useCallback(() => {
    if (!selectedNode) return;
    const id = nextNodeId(selectedNode.data.catalogKey.replace(/\W+/g, "_"));
    const clone: Node<CanvasNodeData> = {
      ...selectedNode,
      id,
      selected: false,
      position: { x: selectedNode.position.x + 40, y: selectedNode.position.y + 40 },
      data: {
        ...selectedNode.data,
        values: { ...selectedNode.data.values },
        triggerConfig: selectedNode.data.triggerConfig ? { ...selectedNode.data.triggerConfig } : undefined,
      },
    };
    capture();
    setNodes((nds) => [...nds, clone]);
    setSelectedId(id);
  }, [capture, selectedNode, setNodes]);

  // @xyflow/react's built-in delete (deleteKeyCode) already cascades: GraphView resolves
  // connected edges before calling onEdgesChange, and onEdgesChange here is the controlled
  // setEdges from useEdgesState in App.tsx — so edges are cleaned up without extra code.
  // This handler only clears a stale Inspector selection.
  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      if (selectedId && deleted.some((n) => n.id === selectedId)) setSelectedId(null);
    },
    [selectedId],
  );

  const onNodeMouseEnter = useCallback((_event: MouseEvent, node: Node) => setHoveredId(node.id), []);
  const onNodeMouseLeave = useCallback(() => setHoveredId(null), []);

  const { highlightedNodeIds, highlightedEdgeIds } = useConnectedChain(nodes, edges, hoveredId);

  // The missing-account warning is derived here and never stored: it is a function of the whole
  // graph (a loop upstream removes it), so persisting it in node.data would be a second copy of a
  // fact the graph already carries, going stale the moment an edge moves.
  const renderNodes = useMemo(
    () =>
      nodes.map((n) => {
        const accountMissing =
          needsAccount(n.data.catalogKey) &&
          !n.data.accountRef &&
          !isInsideAccountLoop(n.id, nodes, edges);
        return {
          ...n,
          data: accountMissing
            ? { ...n.data, warning: "нужен аккаунт — выберите в инспекторе или оберните в цикл по аккаунтам" }
            : n.data,
          className: highlightedNodeIds.has(n.id) ? "flow-node-highlight" : hoveredId ? "flow-node-dim" : undefined,
        };
      }),
    [nodes, edges, highlightedNodeIds, hoveredId],
  );

  const renderEdges = useMemo(
    () =>
      edges.map((e) => ({
        ...e,
        className: highlightedEdgeIds.has(e.id) ? "flow-edge-highlight" : hoveredId ? "flow-edge-dim" : undefined,
      })),
    [edges, highlightedEdgeIds, hoveredId],
  );

  return (
    <div className="flow-canvas">
      {/* The palette exists to add nodes; in preview there is nothing to add it to. */}
      {readOnly ? null : (
        <Sidebar
          catalog={catalog}
          catalogError={catalogError}
          onAddTrigger={addTriggerNode}
          onAddNode={addCatalogNode}
          variant={variant}
        />
      )}
      <div className="flow-canvas__stage">
        {!catalog && !catalogError ? (
          <div className="flow-canvas__loading">
            <Loader label="Загрузка холста…" />
          </div>
        ) : null}
        {catalog && nodes.length === 0 ? <p className="flow-canvas__empty">{emptyHint}</p> : null}
        {readOnly ? null : (
          <div className="flow-canvas__history">
            <Button
              variant="ghost"
              size="sm"
              onClick={undo}
              disabled={!history.canUndo}
              // Says what does NOT come back, where the operator will look for it. Field values are
              // out of the stack on purpose (see useGraphHistory) and their absence needs naming.
              title="Отменить последнее изменение графа (Ctrl+Z). Значения полей не откатываются."
            >
              Отменить
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={redo}
              disabled={!history.canRedo}
              title="Вернуть отменённое (Ctrl+Shift+Z)"
            >
              Вернуть
            </Button>
          </div>
        )}
        <ReactFlow
          nodes={renderNodes}
          edges={renderEdges}
          onNodesChange={readOnly ? undefined : handleNodesChange}
          onEdgesChange={readOnly ? undefined : handleEdgesChange}
          onNodesDelete={readOnly ? undefined : onNodesDelete}
          onConnect={readOnly ? undefined : onConnect}
          isValidConnection={isValidConnection}
          onNodeClick={(_, node) => setSelectedId(node.id)}
          onNodeMouseEnter={onNodeMouseEnter}
          onNodeMouseLeave={onNodeMouseLeave}
          onPaneClick={() => setSelectedId(null)}
          nodeTypes={nodeTypes}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          edgesReconnectable={!readOnly}
          // Selection stays on in preview: clicking a node opens the inspector, which is how the
          // graph is read. Only the edits are withheld.
          deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
          // React Flow paints its own chrome (background dots, controls, minimap, edge defaults)
          // from an internal palette this app's tokens cannot reach, so it needs telling the theme
          // separately. Left hardcoded to "dark" it stayed dark inside a light page — the one place
          // the token migration could not fix by aliasing.
          colorMode={theme}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(255,255,255,0.08)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <Inspector
        node={selectedNode}
        catalogEntry={selectedCatalogEntry}
        onChangeValue={onChangeValue}
        onChangeAccountRef={onChangeAccountRef}
        insideAccountLoop={insideAccountLoop}
        onChangeTrigger={onChangeTrigger}
        onRenameLabel={onRenameLabel}
        onDeleteNode={onDeleteSelected}
        onDuplicateNode={onDuplicateSelected}
        varKeys={varKeys}
      />
    </div>
  );
}
