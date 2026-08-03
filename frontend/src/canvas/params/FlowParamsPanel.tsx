// Declaring a flow's parameters. The values half lives in RunParamsDialog; this half only says
// what exists. Preview is the very component the run form uses — a separate preview renderer would
// be a second source of truth for how a param looks.
import { Alert, Button, Input, Select, Textarea } from "@open-lzt/ui";
import type { Node } from "@xyflow/react";
import { useMemo, useState } from "react";
import type { ParamControl, ParamSpec, ParamValue } from "../../api/paramSpec";
import { ParamSurface } from "../ParamSurface";
import type { CanvasNodeData } from "../canvasTypes";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import {
  CONTROLS,
  CONTROL_LABELS,
  coerceDefault,
  emptyParam,
  formatOptions,
  hasOptions,
  hasRange,
  moveParam,
  parseOptions,
  removeParam,
  updateParam,
  validateKey,
} from "./paramSpecEditor";
import { nodesReferencing } from "./varRefs";
import "./flow-params.css";

export interface FlowParamsPanelProps {
  params: ParamSpec[];
  onChange: (params: ParamSpec[]) => void;
  /** Read only to name the nodes a deletion would break. */
  nodes: Node<CanvasNodeData>[];
}

export function FlowParamsPanel({ params, onChange, nodes }: FlowParamsPanelProps) {
  const [previewValues, setPreviewValues] = useState<Record<string, ParamValue>>({});
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);

  const breakage = useMemo(
    () => (pendingDelete == null ? [] : nodesReferencing(params[pendingDelete].key, nodes)),
    [pendingDelete, params, nodes],
  );

  function patch(index: number, next: Partial<ParamSpec>) {
    onChange(updateParam(params, index, next));
  }

  return (
    <div className="flow-params">
      <div className="flow-params__head">
        <h3 className="flow-params__title">Параметры флоу</h3>
        <p className="flow-params__hint">
          Объявленный параметр доступен в полях блоков как <code>{"{{vars.ключ}}"}</code> и
          спрашивается перед запуском. Ссылка работает, только если занимает поле целиком.
        </p>
        <Button variant="outline" size="sm" onClick={() => onChange([...params, emptyParam(params)])}>
          Добавить параметр
        </Button>
      </div>

      {params.length === 0 ? (
        <p className="flow-params__empty">
          Пока ни одного. Параметр стоит завести, когда одно и то же число приходится править сразу
          в нескольких блоках.
        </p>
      ) : null}

      <ol className="flow-params__list">
        {params.map((spec, index) => {
          const keyError = validateKey(spec.key, index, params);
          return (
            <li className="flow-params__item" key={index}>
              <div className="flow-params__row">
                <label className="flow-params__field flow-params__field--key">
                  <span>Ключ</span>
                  <Input
                    value={spec.key}
                    invalid={keyError !== null}
                    onChange={(e) => patch(index, { key: e.target.value })}
                  />
                  {keyError ? <span className="flow-params__error">{keyError}</span> : null}
                </label>

                <label className="flow-params__field">
                  <span>Название</span>
                  <Input value={spec.label} onChange={(e) => patch(index, { label: e.target.value })} />
                </label>

                <label className="flow-params__field">
                  <span>Контрол</span>
                  <Select
                    value={spec.control}
                    onChange={(e) => patch(index, { control: e.target.value as ParamControl })}
                  >
                    {CONTROLS.map((control) => (
                      <option key={control} value={control}>
                        {CONTROL_LABELS[control]}
                      </option>
                    ))}
                  </Select>
                </label>
              </div>

              <div className="flow-params__row">
                <label className="flow-params__field">
                  <span>Значение по умолчанию</span>
                  <Input
                    value={spec.default == null ? "" : String(spec.default)}
                    onChange={(e) => patch(index, { default: coerceDefault(spec.control, e.target.value) })}
                  />
                </label>

                {hasRange(spec.control) ? (
                  <>
                    <label className="flow-params__field flow-params__field--num">
                      <span>Минимум</span>
                      <Input
                        inputMode="decimal"
                        value={spec.minimum ?? ""}
                        onChange={(e) =>
                          patch(index, { minimum: e.target.value === "" ? null : Number(e.target.value) })
                        }
                      />
                    </label>
                    <label className="flow-params__field flow-params__field--num">
                      <span>Максимум</span>
                      <Input
                        inputMode="decimal"
                        value={spec.maximum ?? ""}
                        onChange={(e) =>
                          patch(index, { maximum: e.target.value === "" ? null : Number(e.target.value) })
                        }
                      />
                    </label>
                    <label className="flow-params__field flow-params__field--num">
                      <span>Шаг</span>
                      <Input
                        inputMode="decimal"
                        value={spec.step ?? ""}
                        onChange={(e) =>
                          patch(index, { step: e.target.value === "" ? null : Number(e.target.value) })
                        }
                      />
                    </label>
                  </>
                ) : null}
              </div>

              {hasOptions(spec.control) ? (
                <label className="flow-params__field">
                  <span>Варианты — по одному в строке, «значение|подпись»</span>
                  <Textarea
                    rows={3}
                    value={formatOptions(spec.options)}
                    onChange={(e) => patch(index, { options: parseOptions(e.target.value) })}
                  />
                </label>
              ) : null}

              <div className="flow-params__row flow-params__row--foot">
                <label className="flow-params__check">
                  <input
                    type="checkbox"
                    checked={spec.required}
                    onChange={(e) => patch(index, { required: e.target.checked })}
                  />
                  <span>Обязательный</span>
                </label>
                <label className="flow-params__field flow-params__field--grow">
                  <span>Пояснение</span>
                  <Input
                    value={spec.description ?? ""}
                    onChange={(e) => patch(index, { description: e.target.value || null })}
                  />
                </label>
                <div className="flow-params__actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={index === 0}
                    aria-label="Выше"
                    title="Выше"
                    onClick={() => onChange(moveParam(params, index, -1))}
                  >
                    ↑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={index === params.length - 1}
                    aria-label="Ниже"
                    title="Ниже"
                    onClick={() => onChange(moveParam(params, index, 1))}
                  >
                    ↓
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setPendingDelete(index)}>
                    Удалить
                  </Button>
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {params.length > 0 ? (
        <section className="flow-params__preview">
          <h4 className="flow-params__preview-title">Как это увидит оператор перед запуском</h4>
          <ParamSurface
            params={params}
            values={previewValues}
            onChange={(key, value) => setPreviewValues((v) => ({ ...v, [key]: value }))}
          />
        </section>
      ) : null}

      {pendingDelete != null ? (
        <ConfirmDialog
          open
          title={`Удалить параметр «${params[pendingDelete].label}»?`}
          confirmLabel="Удалить"
          onConfirm={() => {
            onChange(removeParam(params, pendingDelete));
            setPendingDelete(null);
          }}
          onCancel={() => setPendingDelete(null)}
        >
          {breakage.length > 0 ? (
            <Alert tone="warning" title="На него ссылаются блоки">
              {breakage.join(", ")} — их поля останутся с текстом{" "}
              <code>{`{{vars.${params[pendingDelete].key}}}`}</code>, который больше ничем не
              подставится.
            </Alert>
          ) : (
            <p>Ни один блок на него не ссылается.</p>
          )}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
