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
import { useDocumentTheme } from "../ui/useDocumentTheme";
import { useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";
import { fetchCatalog, type CatalogNode, type TriggerKind } from "../api/flowClient";
import { Loader } from "../components/Loader";
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

  const onConnect: OnConnect = useCallback(
    (connection) => setEdges((eds) => addEdge(connection, eds)),
    [setEdges],
  );

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
      setNodes((nds) => [...nds, node]);
      setSelectedId(id);
    },
    [nodes.length, setNodes],
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
      setNodes((nds) => [...nds, node]);
      setSelectedId(id);
    },
    [nodes.length, setNodes],
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
    setNodes((nds) => nds.filter((n) => n.id !== selectedId));
    setEdges((eds) => eds.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }, [selectedId, setNodes, setEdges]);

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
    setNodes((nds) => [...nds, clone]);
    setSelectedId(id);
  }, [selectedNode, setNodes]);

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

  const renderNodes = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        className: highlightedNodeIds.has(n.id) ? "flow-node-highlight" : hoveredId ? "flow-node-dim" : undefined,
      })),
    [nodes, highlightedNodeIds, hoveredId],
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
        <ReactFlow
          nodes={renderNodes}
          edges={renderEdges}
          onNodesChange={readOnly ? undefined : onNodesChange}
          onEdgesChange={readOnly ? undefined : onEdgesChange}
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
        onChangeTrigger={onChangeTrigger}
        onRenameLabel={onRenameLabel}
        onDeleteNode={onDeleteSelected}
        onDuplicateNode={onDuplicateSelected}
      />
    </div>
  );
}
