# Design System: LLM Eval Lab

Pattern: **Data-Dense Dashboard** — KPI cards, real charts, dense tables, minimal
padding, 12-col bento grid, maximum data visibility. Identity: **code dark + run
green**, extended with a cyan/violet duotone. Founded with ui-ux-pro-max-skill;
chart color and interaction rules follow the dataviz skill (palettes are
validator-checked, not eyeballed).

## Colors

Tokens live in `src/app.css` (`:root` light / `.dark`); Tailwind v4 reads them via
`@theme inline`.

| Role            | Light     | Dark      |
| --------------- | --------- | --------- |
| Primary / CTA   | `#16a34a` | `#22c55e` |
| Accent 2 (cyan) | `#0e7490` | `#22d3ee` |
| Accent 3 (violet) | `#6d28d9` | `#a78bfa` |
| Background      | `#f8fafc` | `#162233` |
| Card            | `#ffffff` | `#1a2d44` |
| Text            | `#0f172a` | `#f1f5f9` |

- Green stays the only action color. Cyan/violet are supporting accents (icon
  tints, gradients, KPI chips) — never buttons.
- `--gradient-brand` (green→cyan) is reserved for the brand chip, hero name,
  panel hairlines (`.gradient-border-top`), and the header underline.

### Chart palette (validated)

`--chart-1..5` are categorical slots, validated with the dataviz six-checks
script against each mode's surface:

- Light (on `#ffffff`): `#16a34a #0891b2 #7c3aed #d97706 #e11d48`
- Dark (on `#1a2d44`): `#16a34a #0891b2 #8b5cf6 #d97706 #f43f5e`

Re-validate whenever surfaces or slots change:

```
node <dataviz-skill>/scripts/validate_palette.js "<hex,hex,…>" --mode dark --surface "#1a2d44"
```

Rules (see `src/lib/components/charts/theme.ts`):

- **Status/severity colors are reserved**, never reused as "series n". Severity
  hues match `src/lib/constants/colors.ts`; run-status series use
  `--success`/`--destructive`.
- Bright 400-grade hues fail the dark lightness band — text/glow only, never
  chart marks.
- Text never wears the series color; identity comes from a colored mark beside
  text tokens. Legends always present for ≥2 series; counts always visible.

## Typography

- **Display/headings:** Fira Code (`--font-display`); **body:** Fira Sans.
- Hero scale: `.display-xl` (3xl/4xl). Page titles: `.page-header h1` (2xl/3xl).
- KPI values: `.kpi-value` (3xl, tabular).

## Glass & glow (usage rules)

- `.glass-panel` / `.hero-panel`: hero, tooltips, overlays **only** — never on
  dense data surfaces (tables, lists).
- `--glow-primary` (`.glow-primary`, `.hover-glow`): primary CTAs, active nav
  rail, KPI-card hover **only**. Glow is seasoning, not a default shadow.

## Motion

- Hover micro-interactions: 150–300ms, transform/opacity only, ≤4px displacement.
- Entrances: `.rise-in` with `--stagger-i` (35ms/step, cap ~8 steps).
- Count-ups: `svelte/motion` `Tween`, 400ms, only between real values.
- CSS animations are killed globally by `prefers-reduced-motion`; Svelte
  `Tween`/`transition:` must be gated with `prefersReducedMotion` explicitly.
- Silent auto-refresh keeps the previous render — no skeleton flash, no layout
  jump.

## Charts

- LayerChart 2 (`AreaChart`, `PieChart`) wrapped in `src/lib/components/charts/`.
  Colors are passed as CSS `var()` strings so dark/light theming is free.
- Trend lines 2px; area fills ~12% opacity; recessive 1px solid gridlines;
  crosshair + tooltip on by default. Donuts: padAngle surface gaps, center total,
  HTML legend with counts.
- Empty and loading states are mandatory (`ChartEmpty`, `ChartSkeleton`).
- layerstack semantic vars (`--color-surface-100/200/300/content`) are mapped to
  app tokens in `app.css`.

## Key effects

Hover tooltips, row highlighting, smooth filter animations, staggered section
entrances, live status dots (`.status-dot-live`), KPI count-ups.

## Avoid (anti-patterns)

- Ornate decoration; glass/glow on data-dense surfaces
- Light mode as default; slow animations (>300ms UI transitions)
- Dual-axis charts; cycled/generated categorical hues; color-only identity

## Pre-Delivery Checklist

- [ ] No emojis as icons (use SVG: Lucide)
- [ ] cursor-pointer on all clickable elements
- [ ] Hover states with smooth transitions (150–300ms)
- [ ] Text contrast 4.5:1 minimum (both modes)
- [ ] Focus states visible for keyboard nav
- [ ] prefers-reduced-motion respected (incl. Svelte tweens/transitions)
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] New/changed chart palettes re-run through the dataviz validator
