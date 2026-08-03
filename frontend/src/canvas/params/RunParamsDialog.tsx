// The values half of flow parameters: asked once, right before the run that needs them.
import { Button, Modal } from "@open-lzt/ui";
import { useEffect, useMemo, useState } from "react";
import { accountName, fetchAccounts } from "../../api/accountsClient";
import type { ParamSpec, ParamValue } from "../../api/paramSpec";
import { ParamSurface, type AccountRef } from "../ParamSurface";
import { validateParam } from "../paramTypes";

export interface RunParamsDialogProps {
  params: ParamSpec[];
  onSubmit: (values: Record<string, ParamValue>) => void;
  onCancel: () => void;
}

/** Prefilled from the declared defaults so the common case is one click. */
function initialValues(params: ParamSpec[]): Record<string, ParamValue> {
  const values: Record<string, ParamValue> = {};
  for (const spec of params) {
    if (spec.default != null) values[spec.key] = spec.default;
  }
  return values;
}

/** Only what the operator actually set travels: a key sent as `null` is a value to the backend,
 * and resolve_params would coerce it rather than fall back to the declared default. */
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

  // The same verdict resolve_params will reach, reached here — otherwise the operator submits a
  // green form and reads the refusal as a server fault.
  const blocking = useMemo(
    () => params.filter((spec) => validateParam(spec, values[spec.key] ?? spec.default ?? null) !== null),
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
