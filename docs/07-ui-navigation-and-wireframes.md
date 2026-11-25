# 07 · UI navigation and wireframes

Low-fidelity, structure only. Visual design (tokens, dark/light themes) comes after this structure is approved. Only screens whose backend exists in a phase are built in that phase — no placeholder screens (spec §24.18).

## 1. Navigation map
| Spec nav item | Web/Desktop | Mobile | Phase |
|---|---|---|---|
| Dashboard | sidebar | Home tab | 1 (counters), grows |
| All Assets | sidebar | Library tab | 1 |
| Screenshots | sidebar | Library filter chip | 2 |
| Knowledge Vault | sidebar | Knowledge tab | 2 |
| Smart Search | top bar (⌘K / Ctrl+K) | Search tab | 2 (FTS), 3 (semantic) |
| Collections | sidebar | Collections tab | 1 |
| Duplicate Manager | sidebar | — (desktop/web only) | 3 |
| People | sidebar | Library filter | 7 |
| Architecture / Design References | sidebar | Library filter | 7 |
| Cloud Connections | Settings › Sources | Settings | 4 (local), 6 (cloud) |
| Processing Queue | sidebar (badge) | Settings › Processing | 1 |
| Reports | sidebar | — | 3+ |
| Settings | sidebar footer | Settings tab | 1 |

Mobile bottom tabs: **Home · Library · Search · Collections · More**, plus a floating **+** (camera, gallery, scan document, scan business card).

## 2. Web / desktop shell
```
┌───────────────────────────────────────────────────────────────────────────┐
│ ▣ ImageVault   [ Search assets, text, people…        ⌘K ]   ⇪Upload  ●Me │
├──────────────┬────────────────────────────────────────────────────────────┤
│ Dashboard    │ All Assets              Sort: Taken ▾   ▦ Grid ☰ List  ⫶ │
│ All Assets   │ [Kind ▾][Date ▾][Tags ▾][Format ▾][Size ▾][+ Filter]  ✕   │
│ Screenshots  │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐                       │
│ Knowledge    │ │    ││    ││    ││    ││    ││    │  virtualized grid      │
│ Collections ▸│ └────┘└────┘└────┘└────┘└────┘└────┘  (date headers)       │
│ Duplicates   │ ┌────┐┌────┐┌────┐┌────┐┌────┐┌────┐                       │
│ Processing ③ │ │ ⟳  ││    ││ ⚠  ││    ││    ││    │  ⟳ processing ⚠ failed │
│ ─────────    │ └────┘└────┘└────┘└────┘└────┘└────┘                       │
│ Settings     │ ── 3 selected:  Tag · Add to collection · Favourite · Hide │
└──────────────┴────────────────────────────────────────────────────────────┘
```
Keyboard: `/` search, `j/k` move, `space` quick-look, `x` select, `t` tag, `c` add to collection, `f` favourite, `Esc` clear.

## 3. Asset detail (right drawer on desktop, full screen on mobile)
```
┌──────────────────────────────────────┬────────────────────────────────┐
│                                      │ Title  [editable]          ☆    │
│           preview (zoom/pan)         │ ── Source metadata ──────────── │
│   [ draw box → Find similar ] (Ph3)  │ 4032×3024 · HEIC · 3.1 MB       │
│                                      │ Taken 12 Mar 2026 · Pixel 8     │
│                                      │ GPS 18.52, 73.85  (map)         │
│                                      │ ── AI (unconfirmed) ─────── ⓘ  │
│                                      │ Tags: concrete✓? glass✓? ✕      │
│                                      │ Category: Exterior render 0.71  │
│                                      │ ── Yours ────────────────────── │
│                                      │ Tags: villa-ref  · Notes …      │
│                                      │ Collections: Facade ideas       │
│                                      │ ── Found at ─────────────────── │
│                                      │ Upload · PC-1 D:\Refs\a.jpg     │
└──────────────────────────────────────┴────────────────────────────────┘
```
AI suggestions show confidence and ✓ (confirm) / ✕ (reject) — the provenance model made visible.

## 4. Knowledge Card (Phase 2)
```
┌ Knowledge card ─────────────────────────────── AI-generated · 0.82 ─┐
│ "Postgres: connection refused on port 5432"            [Edit] [⋯]   │
│ Short: Error from psql when server not listening…                   │
│ ┌ screenshot thumb ┐  Extracted text (click line → highlights box)  │
│ │  ▭▭▭ highlighted │  psql: error: connection to server at …        │
│ └──────────────────┘  [Copy text]                                   │
│ Entities: host=localhost · port=5432 · cmd: psql -h …               │
│ Category: Programming › Database   Tags: postgresql, supabase       │
│ Notes: …                         Related: 3 screenshots ▸           │
│ [Export .md] [Export PDF] [Add to collection]                        │
└──────────────────────────────────────────────────────────────────────┘
```

## 5. Search results (Phase 2–3)
```
[ postgres connection errors in 2026                 ] Mode: Screenshots ▾
Exact text matches (4)       ─ highlighted OCR snippet under each thumb
Related by meaning (AI) (23) ─ labelled "AI-suggested", score bar
```
Exact and AI-suggested results are separate sections, never interleaved without labels.

## 6. Duplicate manager (Phase 3)
Side-by-side: file, size, resolution, source location, modified date, similarity %, level (exact/perceptual/visual). Actions: **Keep both · Hide this copy in ImageVault**. No delete action exists.

## 7. Upload + processing queue (Phase 1)
Upload sheet: drag-drop zone, per-file progress, "already in library" chips for duplicates, resumable after reload. Processing Queue: table (asset · stage · status · attempts · error · Retry), filters by status/stage.

## 8. Mobile key screens (Phase 5)
- **Library:** 3-column grid, pinch to change density, filter chips; long-press multi-select.
- **Capture (+):** Camera · Pick from gallery · Scan document (edge detect → crop) · Scan business card (→ contact entities, "Add to Contacts" requires explicit tap).
- **Share sheet target:** "Save to ImageVault" → choose collection → queued offline if no network.
- **Offline:** banner "Offline – showing cached items"; unavailable originals greyed with cloud icon.
- **App lock:** biometric gate on open when enabled.

## 9. States every screen must implement
Loading (skeleton) · Empty (first-use guidance with one primary action) · Error (retry, request id) · Partial (some thumbs failed) · Permission denied (viewer role, read-only controls) · Offline (desktop/mobile).
