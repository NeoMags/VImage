// Theme: follow Windows (default) or a fixed light/dark choice, remembered on this PC.
import { useCallback, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "iv.theme";

export function readTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [theme, setThemeState] = useState<ThemeChoice>(readTheme);
  const setTheme = useCallback((t: ThemeChoice) => {
    setThemeState(t);
    applyTheme(t);
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* private mode: applies for this session only */
    }
  }, []);
  return [theme, setTheme];
}
