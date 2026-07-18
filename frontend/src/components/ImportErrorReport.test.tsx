import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImportErrorReport } from "./ImportErrorReport";
import type { ImportError } from "../api/flowClient";

describe("ImportErrorReport", () => {
  it("groups errors by stage with Russian badges and shows node id or a dash", () => {
    const errors: ImportError[] = [
      { node_id: "n1", stage: "schema", message: "отсутствует обязательное поле" },
      { node_id: null, stage: "compile", message: "цикл в графе" },
      { node_id: "n3", stage: "dry_run", message: "таймаут выполнения" },
    ];
    render(<ImportErrorReport errors={errors} onDismiss={vi.fn()} />);

    expect(screen.getByText(/Не удалось импортировать флоу — 3 ошибок/)).toBeInTheDocument();
    expect(screen.getByText("Схема")).toBeInTheDocument();
    expect(screen.getByText("Компиляция")).toBeInTheDocument();
    expect(screen.getByText("Пробный запуск")).toBeInTheDocument();
    expect(screen.getByText("n1")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("отсутствует обязательное поле")).toBeInTheDocument();
  });

  it("calls onDismiss when the close button is clicked", () => {
    const onDismiss = vi.fn();
    render(
      <ImportErrorReport
        errors={[{ node_id: null, stage: "schema", message: "ошибка" }]}
        onDismiss={onDismiss}
      />,
    );

    fireEvent.click(screen.getByLabelText("Закрыть отчёт об ошибках"));
    expect(onDismiss).toHaveBeenCalled();
  });
});
