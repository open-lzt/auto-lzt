import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusDot, type TaskHealth } from "./StatusDot";

const ALL: TaskHealth[] = ["idle", "running", "failing", "paused"];

describe("StatusDot", () => {
  it("renders all four states distinctly", () => {
    const classes = ALL.map((health) => {
      const { container } = render(<StatusDot health={health} />);
      return container.querySelector(".status-dot")!.className;
    });

    expect(new Set(classes).size).toBe(4);
    ALL.forEach((health, i) => expect(classes[i]).toContain(`status-dot--${health}`));
  });

  it("marks ONLY running as the animated state", () => {
    // The animation is CSS, so what the component can assert is that exactly one state carries the
    // class the keyframes hang off. A pulsing `failing` would make a stuck task look busy.
    const animated = ALL.filter((health) => {
      const { container } = render(<StatusDot health={health} />);
      return container.querySelector(".status-dot--running") !== null;
    });

    expect(animated).toEqual(["running"]);
  });

  it("names the state in Russian for anyone who cannot see the colour", () => {
    const { container } = render(<StatusDot health="failing" />);
    expect(container.querySelector(".status-dot-wrap")!.getAttribute("title")).toBe("Ошибка");
  });

  it("shows the label inline when asked", () => {
    const { getByText } = render(<StatusDot health="paused" withLabel />);
    expect(getByText("На паузе")).toBeInTheDocument();
  });

  it("keeps the state readable without the label, via a screen-reader-only name", () => {
    const { container } = render(<StatusDot health="running" />);
    expect(container.querySelector(".sr-only")!.textContent).toBe("Выполняется");
  });
});
