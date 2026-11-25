# packages/ui

Small React component set for the desktop UI (B11, decision 2): plain CSS with variables
(`src/tokens.css`) and CSS modules, plus Radix UI primitives where accessibility is hard to get
right by hand (dialogs). No Tailwind, no generated component code.

Theme: `tokens.css` follows Windows light/dark (`prefers-color-scheme`); `data-theme="light|dark"`
on `<html>` overrides it (Settings → Theme).
