import { Button, Modal } from "@open-lzt/ui";
import type { ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** What the operator loses. Shown as the body — not a restatement of the title. */
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** The gate in front of a delete that the server cannot undo.
 *
 * Both call sites delete rows the backend has no restore endpoint for, so an undo toast would be a
 * lie — the only honest guard is asking first. Cancel is the default action: it is the first button
 * in the DOM, so Tab lands there and Escape (Modal's own handler) resolves to it. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            Отмена
          </Button>
          <Button variant="danger" loading={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  );
}
