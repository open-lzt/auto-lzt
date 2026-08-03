import { Button, Modal } from "@open-lzt/ui";
import { useEffect, useMemo, useState } from "react";
import { accountName, fetchAccounts } from "../../api/accountsClient";
import type { ParamSpec, ParamValue } from "../../api/paramSpec";
import { ParamSurface, type AccountRef } from "../ParamSurface";
import { isVisible, validateParam } from "../paramTypes";

export interface RunParamsDialogProps {
  params: ParamSpec[];
  onSubmit: (values: Record<string, ParamValue>) => void;
  onCancel: () => void;
}

function initialValues(params: ParamSpec[]): Record<string, ParamValue> {
  const values: Record<string, ParamValue> = {};
  for (const spec of params) {
    if (spec.default != null) values[spec.key] = spec.default;
  }
  return values;
}

/** A key sent as null is a value to the backend — resolve_params won't fall back to the default. */
function payload(params: ParamSpec[], values: Record<string, ParamValue>): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const spec of params) {
    const value = values[spec.key];
    if (value !== undefined && value !== null && value !== "") out[spec.key] = value;
  }
  return out;
}

export function RunParamsDialog({ params, onSubmit, onCancel }: RunParamsDialogProps) {
  const [values, setValues] = useState<Record<string, ParamValue>>(() => initialValues(params));
  const [accounts, setAccounts] = useState<AccountRef[]>([]);

  const needsAccounts = params.some((p) => p.control === "account_picker");
  useEffect(() => {
    if (!needsAccounts) return;
    let alive = true;
    fetchAccounts()
      .then((all) => {
        if (alive) {
          setAccounts(
            all.filter((a) => a.status === "active").map((a) => ({ id: a.id, label: accountName(a) })),
          );
        }
      })
      .catch(() => {
        /* the picker degrades to an empty list, which its own empty state explains */
      });
    return () => {
      alive = false;
    };
  }, [needsAccounts]);

  // Mirrors the verdict resolve_params reaches server-side, or the refusal reads as a server fault.
  const blocking = useMemo(
    () =>
      params.filter(
        (spec) =>
          // Hidden by visible_if means neither required nor validated, server-side too. Without
          // this a hidden required field disabled the button while its error stayed invisible —
          // the flow could not be published at all.
          isVisible(spec, values) &&
          validateParam(spec, values[spec.key] ?? spec.default ?? null) !== null,
      ),
    [params, values],
  );

  return (
    <Modal
      open
      onClose={onCancel}
      title="Значения параметров"
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            Отмена
          </Button>
          <Button
            variant="primary"
            disabled={blocking.length > 0}
            onClick={() => onSubmit(payload(params, values))}
          >
            Запустить
          </Button>
        </>
      }
    >
      <ParamSurface
        params={params}
        values={values}
        accounts={accounts}
        onChange={(key, value) => setValues((v) => ({ ...v, [key]: value }))}
      />
    </Modal>
  );
}
