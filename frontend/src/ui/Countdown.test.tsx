import { render, screen, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Countdown } from "./Countdown";

const NOW = new Date("2026-07-20T12:00:00.000Z");

function at(offsetSeconds: number): string {
  return new Date(NOW.getTime() + offsetSeconds * 1000).toISOString();
}

describe("Countdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("ticks down and crosses into the accent colour at 60s", () => {
    const { container } = render(<Countdown targetAt={at(90)} serverTime={NOW.toISOString()} />);
    const el = () => container.querySelector(".countdown")!;

    expect(el().textContent).toBe("1 мин 30 с");
    expect(el().className).not.toContain("countdown--urgent");

    act(() => {
      vi.advanceTimersByTime(30_000);
    });

    expect(el().textContent).toBe("1 мин 00 с");
    expect(el().className).toContain("countdown--urgent");
  });

  it("renders «сейчас» for a target that has passed, never a negative number", () => {
    render(<Countdown targetAt={at(-5)} serverTime={NOW.toISOString()} />);
    const text = screen.getByTitle(/2026/).textContent ?? "";

    expect(text).toBe("сейчас");
    expect(text).not.toContain("-");
  });

  it("counts from the SERVER clock, so a skewed browser still shows the right remainder", () => {
    // The browser is five minutes fast. Anchoring on Date.now() would show 00:00 — five minutes
    // early, silently, on every card.
    const serverTime = new Date(NOW.getTime() - 5 * 60_000).toISOString();
    const { container } = render(<Countdown targetAt={at(-4 * 60)} serverTime={serverTime} />);

    expect(container.querySelector(".countdown")!.textContent).toBe("1 мин 00 с");
  });

  it("labels every unit, so mm:ss can never be mistaken for hours", () => {
    const { container } = render(
      <Countdown targetAt={at(2 * 3600 + 5 * 60 + 30)} serverTime={NOW.toISOString()} />,
    );
    expect(container.querySelector(".countdown")!.textContent).toBe("2 ч 05 мин");
  });

  it("renders a dash for a paused task rather than a stopped clock", () => {
    const { container } = render(<Countdown targetAt={null} serverTime={NOW.toISOString()} />);
    const el = container.querySelector(".countdown")!;

    expect(el.textContent).toBe("—");
    expect(el.className).toContain("countdown--idle");
  });

  it("stops its interval on unmount", () => {
    const clear = vi.spyOn(window, "clearInterval");
    const { unmount } = render(<Countdown targetAt={at(90)} serverTime={NOW.toISOString()} />);
    unmount();
    expect(clear).toHaveBeenCalled();
  });
});
