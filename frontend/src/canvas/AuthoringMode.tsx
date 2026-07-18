import { useEdgesState, useNodesState, type Edge, type Node } from "@xyflow/react";
import { useCallback, useEffect, useState } from "react";
import {
  createComposite,
  fetchCatalog,
  getComposite,
  type CatalogNode,
  type CompositeDetail,
  type InputSpec,
  type NodeSpec,
  type TemplateParam,
} from "../api/flowClient";
import { FlowCanvas } from "./FlowCanvas";
import { displayLabel } from "./labels";
import type { CanvasNodeData, TemplateBoundaryKind } from "./canvasTypes";
import "./authoring-mode.css";

let nextBoundarySeq = 1;
function nextBoundaryId(kind: TemplateBoundaryKind): string {
  return `boundary_${kind}_${nextBoundarySeq++}`;
}

function paramPlaceholder(name: string): string {
  return `{{param.${name}}}`;
}

function isBoundaryNode(node: Node<CanvasNodeData>): boolean {
  return node.type === "templateBoundary";
}

/** Reconstructs canvas nodes/edges from a saved composite (GET .../:id) — inverse of
 * buildTemplateSpec below. Internal step nodes render with their real catalog category;
 * boundary markers are re-synthesized from `inputs`/`outputs` and laid out in flanking columns
 * so the declared parameter surface stays visually distinct from the internal graph. */
function compositeToCanvas(
  composite: CompositeDetail,
  catalog: CatalogNode[],
): { nodes: Node<CanvasNodeData>[]; edges: Edge[] } {
  const categoryByKey = new Map(catalog.map((entry) => [entry.key, entry.category]));
  const nodes: Node<CanvasNodeData>[] = [];
  const edges: Edge[] = [];

  composite.nodes.forEach((nodeSpec: NodeSpec, index) => {
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

  composite.inputs.forEach((param, index) => {
    const id = nextBoundaryId("input");
    nodes.push({
      id,
      type: "templateBoundary",
      position: { x: -60, y: 40 + index * 80 },
      data: { catalogKey: id, category: "action", label: param.name, values: {}, boundaryKind: "input", paramName: param.name },
    });
  });

  composite.outputs.forEach((param, index) => {
    const id = nextBoundaryId("output");
    nodes.push({
      id,
      type: "templateBoundary",
      position: { x: 900, y: 40 + index * 80 },
      data: { catalogKey: id, category: "action", label: param.name, values: {}, boundaryKind: "output", paramName: param.name },
    });
    if (param.output_port) {
      const [sourceId, port] = param.output_port.split(".");
      edges.push({
        id: `${sourceId}-${port}->${id}`,
        source: sourceId,
        target: id,
        sourceHandle: port === "next" ? undefined : port,
      });
    }
  });

  return { nodes, edges };
}

/** The internal template graph has no trigger and no "control-flow only" edge into the entry
 * step — the entry is simply the one domain node nothing else points at. Cycles among domain
 * nodes with no free node surface as a null return (caller turns that into a save error). */
function findEntryNodeId(domain: Node<CanvasNodeData>[], edges: Edge[], domainIds: Set<string>): string | null {
  const hasDomainIncoming = new Set(
    edges.filter((e) => domainIds.has(e.source) && domainIds.has(e.target)).map((e) => e.target),
  );
  return domain.find((n) => !hasDomainIncoming.has(n.id))?.id ?? null;
}

function buildNodeSpecs(domain: Node<CanvasNodeData>[], edges: Edge[], domainIds: Set<string>): NodeSpec[] {
  return domain.map((node) => {
    const outEdges: Record<string, string> = {};
    for (const edge of edges) {
      if (edge.source !== node.id || !domainIds.has(edge.target)) continue;
      outEdges[edge.sourceHandle ?? "next"] = edge.target;
    }
    const inputs: Record<string, InputSpec> = {};
    for (const [key, value] of Object.entries(node.data.values)) {
      if (value === "" || value === undefined) continue;
      inputs[key] = { literal: value };
    }
    return { id: node.id, type: node.data.catalogKey, inputs, account_ref: null, edges: outEdges, on_error: null };
  });
}

/** An output param's `output_port` names the internal node+port that produced it
 * ("<node_id>.<port>") — resolved from the one edge pointing at that output's boundary marker.
 * An unconnected output marker (no such edge) is surfaced to the caller, never silently
 * defaulted. */
function buildOutputParams(boundaryNodes: Node<CanvasNodeData>[], edges: Edge[]): { params: TemplateParam[]; unconnected: string[] } {
  const params: TemplateParam[] = [];
  const unconnected: string[] = [];
  for (const marker of boundaryNodes.filter((n) => n.data.boundaryKind === "output")) {
    const name = marker.data.paramName?.trim();
    const edge = edges.find((e) => e.target === marker.id);
    if (!name || !edge) {
      unconnected.push(name || marker.id);
      continue;
    }
    params.push({ name, output_port: `${edge.source}.${edge.sourceHandle ?? "next"}` });
  }
  return { params, unconnected };
}

/** An input param carries no `output_port` (backend: wiring lives in the consuming node's own
 * `{{param.NAME}}`-templated literal, not in an edge) — "connected" for an input therefore means
 * at least one domain node's field literal actually references it. */
function buildInputParams(
  boundaryNodes: Node<CanvasNodeData>[],
  domain: Node<CanvasNodeData>[],
): { params: TemplateParam[]; unconnected: string[] } {
  const params: TemplateParam[] = [];
  const unconnected: string[] = [];
  const literalValues = domain
    .flatMap((n) => Object.values(n.data.values))
    .filter((v): v is string => typeof v === "string");
  for (const marker of boundaryNodes.filter((n) => n.data.boundaryKind === "input")) {
    const name = marker.data.paramName?.trim();
    const referenced = !!name && literalValues.some((v) => v.includes(paramPlaceholder(name)));
    if (!name || !referenced) {
      unconnected.push(name || marker.id);
      continue;
    }
    params.push({ name, output_port: null });
  }
  return { params, unconnected };
}

export interface AuthoringModeProps {
  compositeId: string | null;
  onSaved: (composite: CompositeDetail) => void;
  onCancel: () => void;
}

/** Canvas-editing surface for one composite template's internal graph — reuses FlowCanvas the
 * same way App.tsx does for a normal flow (controlled nodes/edges), plus: a name field, controls
 * to drop named input/output TemplateBoundaryNode markers, and a Save action that walks the
 * canvas into a CreateCompositeRequest and calls createComposite. */
export function AuthoringMode({ compositeId, onSaved, onCancel }: AuthoringModeProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<CanvasNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [name, setName] = useState("Новый составной блок");
  const [paramNameDraft, setParamNameDraft] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!compositeId) return;
    Promise.all([getComposite(compositeId), fetchCatalog()])
      .then(([composite, catalog]) => {
        setName(composite.name);
        const loaded = compositeToCanvas(composite, catalog);
        setNodes(loaded.nodes);
        setEdges(loaded.edges);
        setLoadError(null);
      })
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : "не удалось загрузить составной блок"));
  }, [compositeId, setNodes, setEdges]);

  const addBoundary = useCallback(
    (kind: TemplateBoundaryKind) => {
      const paramName = paramNameDraft.trim();
      if (!paramName) return;
      const id = nextBoundaryId(kind);
      const node: Node<CanvasNodeData> = {
        id,
        type: "templateBoundary",
        position: { x: kind === "input" ? -60 : 900, y: 40 + nodes.length * 60 },
        data: { catalogKey: id, category: "action", label: paramName, values: {}, boundaryKind: kind, paramName },
      };
      setNodes((nds) => [...nds, node]);
      setParamNameDraft("");
    },
    [paramNameDraft, nodes.length, setNodes],
  );

  async function handleSave() {
    const domain = nodes.filter((n) => !isBoundaryNode(n));
    const boundary = nodes.filter(isBoundaryNode);
    const domainIds = new Set(domain.map((n) => n.id));

    if (domain.length === 0) {
      setSaveError("добавьте хотя бы один внутренний блок");
      return;
    }
    const entryNodeId = findEntryNodeId(domain, edges, domainIds);
    if (!entryNodeId) {
      setSaveError("не удалось определить точку входа — во внутреннем графе не должно быть цикла без входного узла");
      return;
    }

    const outputs = buildOutputParams(boundary, edges);
    const inputs = buildInputParams(boundary, domain);
    const unconnected = [...inputs.unconnected, ...outputs.unconnected];
    if (unconnected.length > 0) {
      setSaveError(
        `Параметры не подключены: ${unconnected.join(", ")}. ` +
          "Вход подключается так: скопируйте {{param.имя}} с маркера входа и вставьте в поле того блока, " +
          "который должен его принять. Выход подключается ребром от блока к маркеру выхода.",
      );
      return;
    }

    setSaving(true);
    setSaveError(null);
    try {
      const composite = await createComposite({
        name,
        nodes: buildNodeSpecs(domain, edges, domainIds),
        entry_node_id: entryNodeId,
        inputs: inputs.params,
        outputs: outputs.params,
      });
      onSaved(composite);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "не удалось сохранить составной блок");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="authoring-mode">
      <header className="authoring-mode__header">
        <button type="button" className="authoring-mode__back" onClick={onCancel} aria-label="Назад к списку">
          ←
        </button>
        <input
          className="authoring-mode__name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Название составного блока"
        />
        <div className="authoring-mode__boundary-controls">
          <input
            className="authoring-mode__param-input"
            placeholder="имя параметра"
            value={paramNameDraft}
            onChange={(e) => setParamNameDraft(e.target.value)}
            aria-label="Имя нового параметра"
          />
          <button
            type="button"
            className="authoring-mode__boundary-btn"
            disabled={!paramNameDraft.trim()}
            onClick={() => addBoundary("input")}
          >
            + вход
          </button>
          <button
            type="button"
            className="authoring-mode__boundary-btn"
            disabled={!paramNameDraft.trim()}
            onClick={() => addBoundary("output")}
          >
            + выход
          </button>
        </div>
        <button type="button" className="authoring-mode__save" disabled={saving} onClick={() => void handleSave()}>
          {saving ? "сохранение…" : "сохранить"}
        </button>
      </header>
      {loadError ? <p className="authoring-mode__error">{loadError}</p> : null}
      {saveError ? <p className="authoring-mode__error">{saveError}</p> : null}
      <div className="authoring-mode__canvas">
        <FlowCanvas
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          setNodes={setNodes}
          setEdges={setEdges}
          variant="template"
          emptyHint="Соберите внутренний граф блока из действий слева, затем объявите его входы и выходы."
        />
      </div>
    </div>
  );
}
