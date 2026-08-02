import type { Edge, Node } from "@xyflow/react";
import { useState } from "react";
import { compileFlow, createFlow, createRun, createTrigger, updateFlow } from "../api/flowClient";
import { ApiError } from "../api/httpClient";
import type { ParamSpec, ParamValue } from "../api/paramSpec";
import { FlowBuildError, buildFlowSpec } from "../canvas/buildFlowSpec";
import type { CanvasNodeData } from "../canvas/canvasTypes";
import { RunParamsDialog } from "../canvas/params/RunParamsDialog";
import "./deploy-button.css";

type DeployStage = "idle" | "saving" | "compiling" | "attaching" | "running" | "done";

interface DeployButtonProps {
  flowId: string | null;
  flowName: string;
  nodes: Node<CanvasNodeData>[];
  edges: Edge[];
  setNodes: (updater: (nodes: Node<CanvasNodeData>[]) => Node<CanvasNodeData>[]) => void;
  onDeployed: (flowId: string) => void;
  /** Declared by the params panel. Empty for every flow that predates them — such a flow takes the
   * exact path it took before, dialog included (there is none) and request body unchanged. */
  params?: ParamSpec[];
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
export function DeployButton({
  flowId,
  flowName,
  nodes,
  edges,
  setNodes,
  onDeployed,
  params = [],
}: DeployButtonProps) {
  const [stage, setStage] = useState<DeployStage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [askingValues, setAskingValues] = useState(false);

  const busy = stage !== "idle";

  // The graph is validated BEFORE the values dialog opens: filling a form and only then being told
  // the canvas has two triggers is work thrown away.
  function handleClick() {
    setError(null);
    setNodes(clearNodeErrors);
    try {
      buildFlowSpec(flowName, nodes, edges, params);
    } catch (err) {
      reportBuildError(err);
      return;
    }
    if (params.length > 0) {
      setAskingValues(true);
      return;
    }
    void handleDeploy({});
  }

  function reportBuildError(err: unknown) {
    if (err instanceof FlowBuildError) {
      setError(err.message);
      if (err.nodeId) {
        setNodes((nds) =>
          nds.map((n) => (n.id === err.nodeId ? { ...n, data: { ...n.data, errorMessage: err.message } } : n)),
        );
      }
      return;
    }
    throw err;
  }

  async function handleDeploy(paramValues: Record<string, ParamValue>) {
    setError(null);
    setNodes(clearNodeErrors);

    let built: ReturnType<typeof buildFlowSpec>;
    try {
      built = buildFlowSpec(flowName, nodes, edges, params);
    } catch (err) {
      reportBuildError(err);
      return;
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
      await createRun(
        Object.keys(paramValues).length > 0 ? { flow_id, params: paramValues } : { flow_id },
      );

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
      <button type="button" className="deploy__button" disabled={busy} onClick={handleClick}>
        {STAGE_LABEL[stage]}
      </button>
      {error ? <span className="deploy__error">{error}</span> : null}
      {askingValues ? (
        <RunParamsDialog
          params={params}
          onCancel={() => setAskingValues(false)}
          onSubmit={(values) => {
            setAskingValues(false);
            void handleDeploy(values);
          }}
        />
      ) : null}
    </div>
  );
}
