# 07 · UI navigation and wireframes (Windows desktop)

Structure only; visual design (tokens, dark/light) follows once this is approved. Screens are built only in the phase whose backend exists — no placeholder screens.

## 1. Navigation (spec §14 items, desktop subset)
| Item | Phase | Notes |
|---|---|---|
| Dashboard | D1 | counters: assets, GB indexed, new today, queue, failed, offline sources |
| All Assets | D1 | grid / list / timeline |
| Collections | D1 | nested tree in sidebar |
| Sources (replaces "Cloud Connections") | D1 | folders, drives, NAS; status, scan, pause |
| Processing Queue | D1 | badge with queued/failed count |
| Settings | D1 | library location, resources, exclusions, backup |
| Screenshots · Knowledge Vault · Smart Search | D2 | |
| Duplicate Manager · Similar search | D3 | |
| Map view | D3 **[proposed]** | needs an offline/free tile source — decide later |
| People · Architecture · Design References | D6 | |
| Reports | later | |
Global search box (Ctrl+K) from D2; before that, filters only.

## 2. Main window
```
┌──────────────────────────────────────────────────────────────────────────┐
│ ▣ ImageVault        [ Ctrl+K  Search… (D2) ]          ⏸ Queue 1,204 ▾  ⚙ │
├───────────────┬──────────────────────────────────────────────────────────┤
│ Dashboard     │ All Assets · 84,312     Sort: Taken ▾  ▦ Grid ☰ List ⏱ │
│ All Assets    │ [Kind▾][Date▾][Source▾][Tags▾][Format▾][Size▾][+]   ✕  │
│ ── Collections│ ── March 2026 ───────────────────────────────────────── │
│  ▸ Facades    │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐               │
│  ▸ Learning   │ │    ││ ⟳  ││    ││ ⚠  ││ ⏏  ││    ││    │  ⟳ processing │
│ ── Sources    │ └────┘└────┘└────┘└────┘└────┘└────┘└────┘  ⚠ failed     │
│  ● Pictures   │ ── February 2026 ──────────────────────────  ⏏ drive off │
│  ⏏ Backup HDD │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐               │
│  ● NAS Refs   │ └────┘└────┘└────┘└────┘└────┘└────┘└────┘               │
│ Processing ③  │                                                          │
│ Settings      │ ── 12 selected: Tag · Add to collection · ☆ · Hide ──── │
└───────────────┴──────────────────────────────────────────────────────────┘
```
Keyboard: `j/k`/arrows move, `Space` quick-look, `Enter` detail, `x` select, `t` tag, `c` collection, `f` favourite, `h` hide, `Ctrl+Shift+E` show in Explorer, `Esc` clear.

## 3. Asset detail (right panel; Enter = full window)
```
┌──────────────────────────────────────┬────────────────────────────────┐
│                                      │ Title [editable]           ☆   │
│        preview (zoom / pan)          │ ── From the file ───────────── │
│  (D3) drag a box → Find similar      │ 4032×3024 · HEIC · 3.1 MB      │
│                                      │ Taken 12 Mar 2026 · Pixel 8    │
│                                      │ GPS 18.52, 73.85               │
│                                      │ ── AI suggestions (D2+) ─── ⓘ │
│                                      │ concrete 0.81 ✓ ✕ · glass ✓ ✕ │
│                                      │ ── Yours ───────────────────── │
│                                      │ Tags · Notes · Collections     │
│                                      │ ── Locations ───────────────── │
│                                      │ ● D:\Refs\villa.heic  [Show]   │
│                                      │ ⏏ E:\Backup\villa.heic (offline)│
└──────────────────────────────────────┴────────────────────────────────┘
```

## 4. Sources (D1)
```
Sources                                                   [+ Add folder]
┌──────────────┬────────────────────────┬─────────┬────────┬───────────┐
│ Name         │ Path                   │ Files   │ Status │           │
│ Pictures     │ C:\Users\Neo\Pictures  │ 41,220  │ ● Live │ Scan  ⏸  │
│ Backup HDD   │ (E:) vol 7A3F…         │ 38,900  │ ⏏ Off  │           │
│ NAS Refs     │ \\nas\refs             │ 4,192   │ ⟳ 62%  │ ⏸         │
└──────────────┴────────────────────────┴─────────┴────────┴───────────┘
Add folder dialog: pick folder → include/exclude patterns → "Watch for changes" ☑ → "Process faces" ☐ (D6)
```

## 5. Processing queue + resources (D1)
Table: asset · stage · status · attempts · error · Retry. Header controls: Pause all · Threads [3 ▾] · RAM cap [4 GB ▾] · ☐ Only when idle · ☐ Only on AC power.

## 6. Knowledge card (D2) and search results (D2–D3)
Card: title, short description, extracted text (click a line → box highlighted on the image), entities, category, tags, notes, related, [Copy text] [Export .md] [Export PDF]. Labelled "AI-generated" until you edit it.
Search: **Exact matches** section first, then **Related by meaning (AI-suggested)** section with scores — never mixed without labels.

## 7. Duplicate manager (D3)
Side-by-side compare: path, size, resolution, modified date, similarity %, level. Actions: **Keep both · Hide this copy in ImageVault**. There is no delete button (files stay untouched).

## 8. Tray (D4)
Tray icon: status (Idle / Indexing 1,204 / Paused), Pause/Resume, Open, Quit. Close-to-tray when background indexing is on.

## 9. States every screen implements
Loading skeleton · Empty (first run: "Add your first folder") · Error (retry + log link) · Partial (some thumbs failed) · Offline source.
