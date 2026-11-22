// Persisted light/dark theme, applied via the [data-theme] attribute on <html>.
export type Theme = "dark" | "light";

const KEY = "arag:theme";

export function getTheme(): Theme {
  const saved = localStorage.getItem(KEY);
  // Default to the calm light theme; only honor an explicit saved "dark".
  return saved === "dark" ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(KEY, theme);
}

/** Call once before render to avoid a flash of the wrong theme. */
export function initTheme(): void {
  applyTheme(getTheme());
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}
