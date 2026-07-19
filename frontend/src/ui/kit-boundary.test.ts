import * as kit from "@open-lzt/ui";
import { describe, expect, it } from "vitest";
import * as Countdown from "./Countdown";
import * as StatusDot from "./StatusDot";

/**
 * The guard against quietly re-implementing the design system.
 *
 * `src/ui/` exists for components that are DOMAIN-shaped — a countdown bound to a task's next fire,
 * a dot bound to TaskHealth — and for nothing else. The failure this catches is gradual: someone
 * needs a Card, does not know the kit has one, writes `src/ui/Card.tsx`, and the app ends up with
 * two of everything and two design languages. A name collision is the earliest visible symptom, so
 * it is what fails the build.
 */
describe("the boundary between src/ui and @open-lzt/ui", () => {
  const local = [...Object.keys(Countdown), ...Object.keys(StatusDot)];
  const kitExports = new Set(Object.keys(kit));

  it("defines nothing the kit already exports", () => {
    const collisions = local.filter((name) => kitExports.has(name));
    expect(collisions, `re-implements kit components: ${collisions.join(", ")}`).toEqual([]);
  });

  it("stays small — src/ui is for domain controls, not a second kit", () => {
    // Not a style preference: this number going up is the signal that the rule above is being
    // worked around by picking different names rather than reusing the kit.
    expect(local.length).toBeLessThanOrEqual(4);
  });

  it("confirms the kit really does supply what we chose not to write", () => {
    for (const name of ["Card", "Badge", "Skeleton", "Empty", "Alert", "Tabs", "Modal", "Input"]) {
      expect(kitExports.has(name), `kit is missing ${name}`).toBe(true);
    }
  });
});
