import { Icon, useTheme } from "@open-lzt/ui";
import "./theme-toggle.css";

// Replaces the kit's own ThemeToggle (its "Dark"/"Light" text label reads as status, not action);
// overridden here rather than upstream because @open-lzt/ui is a separate package consumed built.
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
