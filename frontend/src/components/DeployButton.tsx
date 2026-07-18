import type { Edge, Node } from "@xyflow/react";
import { useState } from "react";
import {
  ApiError,
  FlowBuildError,
  buildFlowSpec,
  compileFlow,
  createFlow,
  createRun,
  createTrigger,
  updateFlow,
} from "../api/flowClient";
import type { CanvasNodeData } from "../canvas/canvasTypes";
import "./deploy-button.css";

type DeployStage = "idle" | "saving" | "compiling" | "attaching" | "running" | "done";

interface DeployButtonProps {
  flowId: string | null;
  flowName: string;
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
  setNodes: (updater: (nodes: Node<CanvasNodeData>[]) => Node<CanvasNodeData>[]) => void;
  onDeployed: (flowId: string) => void;
}

const STAGE_LABEL: Record<DeployStage, string> = {
  idle: "Опубликовать",
  saving: "Сохраняем…",
  compiling: "Компилируем…",
  attaching: "Подключаем триггер…",
  running: "Запускаем…",
  done: "Опубликовано",
};

function clearNodeErrors(nodes: Node<CanvasNodeData>[]): Node<CanvasNodeData>[] {
  return nodes.map((n) => (n.data.errorMessage ? { ...n, data: { ...n.data, errorMessage: undefined } } : n));
}

/** save (POST /flows) -> compile (POST /flows/{id}/compile) -> attach trigger (POST
 * /flows/{id}/triggers, skipped for kind=manual) -> fire an immediate run so the LiveBadge has
 * something to show within seconds, per the wave-06 acceptance criterion. A 400 CompileError
 * highlights the offending node inline instead of just toasting. */
export function DeployButton({ flowId, flowName, nodes, edges, setNodes, onDeployed }: DeployButtonProps) {
  const [stage, setStage] = useState<DeployStage>("idle");
  const [error, setError] = useState<string | null>(null);

  const busy = stage !== "idle";

  async function handleDeploy() {
    setError(null);
    setNodes(clearNodeErrors);

    let built: ReturnType<typeof buildFlowSpec>;
    try {
      built = buildFlowSpec(flowName, nodes, edges);
    } catch (err) {
      if (err instanceof FlowBuildError) {
        setError(err.message);
        if (err.nodeId) {
          setNodes((nds) => nds.map((n) => (n.id === err.nodeId ? { ...n, data: { ...n.data, errorMessage: err.message } } : n)));
        }
        return;
      }
      throw err;
    }

    try {
      setStage("saving");
      const { flow_id } = flowId
        ? await updateFlow(flowId, built.spec)
        : await createFlow(built.spec);

      setStage("compiling");
      await compileFlow(flow_id);

      const trigger = built.triggerData.triggerConfig;
      if (trigger && trigger.kind !== "manual") {
        setStage("attaching");
        await createTrigger(flow_id, {
          kind: trigger.kind,
          schedule_cron: trigger.kind === "schedule" ? trigger.schedule_cron : null,
          event_type: trigger.kind === "event" ? trigger.event_type : null,
        });
      }

      setStage("running");
      await createRun({ flow_id });

      setStage("done");
      onDeployed(flow_id);
      window.setTimeout(() => setStage("idle"), 1600);
    } catch (err) {
      setStage("idle");
      if (err instanceof ApiError) {
        setError(err.message);
        if (err.nodeId) {
          setNodes((nds) => nds.map((n) => (n.id === err.nodeId ? { ...n, data: { ...n.data, errorMessage: err.message } } : n)));
        }
      } else {
        setError(err instanceof Error ? err.message : "неизвестная ошибка деплоя");
      }
    }
  }

  return (
    <div className="deploy">
      <button type="button" className="deploy__button" disabled={busy} onClick={() => void handleDeploy()}>
        {STAGE_LABEL[stage]}
      </button>
      {error ? <span className="deploy__error">{error}</span> : null}
    </div>
  );
}
