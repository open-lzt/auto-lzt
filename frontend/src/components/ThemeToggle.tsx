import { Icon, useTheme } from "@open-lzt/ui";
import "./theme-toggle.css";

/** Sun/moon theme switch.
 *
 * Replaces the kit's own `ThemeToggle`, which renders the literal words "Dark"/"Light" — a
 * label that reads as a status ("you are in dark mode") when the control is actually an action
 * ("switch to light"). An icon of the theme you'd get avoids the ambiguity entirely.
 *
 * Overridden here rather than fixed upstream because @open-lzt/ui is a separate package and the
 * panel consumes its built copy; a one-button change is not worth a release of the design system.
 */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "светлую" : "тёмную";
  return (
    <button
      type="button"
      className="panel-theme-toggle"
      onClick={toggle}
      aria-label={`Переключить на ${next} тему`}
      title={`Переключить на ${next} тему`}
    >
      <Icon name={theme === "dark" ? "moon" : "sun"} size={16} />
    </button>
  );
}
