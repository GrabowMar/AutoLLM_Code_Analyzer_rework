# Design System: LLM Eval Lab

Pattern: **Data-Dense Dashboard** — KPI cards, real charts, dense tables, minimal
padding, 12-col bento grid, maximum data visibility. Identity: **code dark + run
green**, extended with a cyan/violet duotone. Founded with ui-ux-pro-max-skill;
chart color and interaction rules follow the dataviz skill (palettes are
validator-checked, not eyeballed).

## Colors

Tokens live in `src/app.css` (`:root` light / `.dark`); Tailwind v4 reads them via
`@theme inline`.

| Role              | Light     | Dark      |
| ----------------- | --------- | --------- |
| Primary / CTA     | `#16a34a` | `#22c55e` |
| Accent 2 (cyan)   | `#0e7490` | `#22d3ee` |
| Accent 3 (violet) | `#6d28d9` | `#a78bfa` |
| Background        | `#f8fafc` | `#162233` |
| Card              | `#ffffff` | `#1a2d44` |
| Text              | `#0f172a` | `#f1f5f9` |

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

## Page anatomy (every page under `(app)/`)

```svelte
<div class="space-y-6">
  <PageHeader title="Experiments" description="One sentence on what this page is for."
              back={{ href: '/experiments', label: 'Experiments' }}>   <!-- detail/create pages only -->
    {#snippet meta()}<StatusBadge status={x.status} />{/snippet}         <!-- optional -->
    {#snippet actions()}<Button href="/experiments/new" size="sm"><Plus />New experiment</Button>{/snippet}
  </PageHeader>

  {#if error}<ErrorAlert message={error} onretry={load} />{/if}
  {#if loading}<LoadingState label="loading experiments" />
  {:else if items.length === 0}<EmptyState title="No experiments yet" description="…">{#snippet action()}…{/snippet}</EmptyState>
  {:else} …content… {/if}
</div>
```

- `PageHeader` owns the `<h1>` and the browser `<title>` (`"{title} · LLM Lab"`) — no
  separate `<svelte:head><title>` and no hand-rolled `.page-header` markup.
- Vertical rhythm: `space-y-6` between page sections, `gap-4` inside grids, `gap-3` in KPI rows.
- Primary action is the last button in `actions`, `size="sm"`; secondary actions `variant="outline"`.

## Shared components (`src/lib/components/`)

| Need                         | Use                                                                          | Not                                                    |
| ---------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------ |
| Page title/actions/back link | `PageHeader`                                                                 | ad-hoc `<h1>` rows, `ArrowLeft` links                  |
| Any status/lifecycle         | `StatusBadge status=…` (`tones={containerTones}` for containers)             | per-page status color maps, `Badge class={…Colors[x]}` |
| Severity                     | `Badge class={severityColors[s]}`                                            | raw red/orange/amber classes                           |
| Error banner                 | `ErrorAlert message onretry?`                                                | `div.bg-destructive/10` boxes                          |
| Loading a region             | `LoadingState label` (buttons keep an inline `LoaderCircle`)                 | centered spinners / “Loading…” text                    |
| Nothing to show              | `EmptyState title description icon? action?` (`compact` inside cards)        | bare “No X found.” paragraphs                          |
| Tabs                         | `ui/tabs` (`Tabs.Root bind:value` / `List` / `Trigger` / `Content`)          | button rows with `activeTab` classes                   |
| Section container            | `Card.Root` + `Card.Header`/`Card.Title`/`Card.Description` + `Card.Content` | bespoke bordered divs                                  |
| Clickable card               | add `.card-lift`                                                             | hover effects on static cards                          |
| KPI                          | `dashboard/KpiCard` or `.kpi-card` / `.kpi-label` / `.kpi-value`             | one-off stat boxes                                     |
| Small group label            | `.section-label`                                                             | `text-[10px] uppercase …` variants                     |
| Pick one of a list           | `ui/select` (`Select.Simple` for plain lists, parts for rich rows)           | raw `<select>` with copied class strings               |
| Boolean / multi-choice field | `ui/checkbox` (`Checkbox` in a `<label>`, or with `<Label for>`)             | `<input type=checkbox>`, hand-drawn check boxes        |
| Mode switch (2–4 options)    | `ui/segmented` (`Segmented`)                                                 | pill rows with `aria-pressed` and active classes       |

Navigation lives in `src/lib/nav.ts` (sidebar, mobile menu, breadcrumbs). Add routes there.

### Form primitives (`src/lib/components/ui/`, bits-ui 2)

- **Select** — `import * as Select from '$lib/components/ui/select'`.
  - Plain list: `<Select.Simple id bind:value items={[{ value, label, description? }]} placeholder clearLabel? size? />`.
    `value` is a string; `''` shows the placeholder. `clearLabel` adds an "unset" option (e.g. "Inherit").
  - Rich rows: `Select.Root type="single" value onValueChange items` → `Select.Trigger id` (render the
    selected row yourself) → `Select.Content` → `Select.Group`/`Select.GroupHeading`/`Select.Separator` →
    `Select.Item value label` with a children snippet. Always pass `label` (typeahead) and `items` on Root.
  - Two-line triggers: add `class="min-h-9 py-1.5 data-[size=default]:h-auto"`.
- **Checkbox** — `<label class="flex items-center gap-2"><Checkbox bind:checked /> Free only</label>`;
  `indeterminate` supported. Inside a clickable row (`<button role="checkbox">`) draw the box with spans
  instead — a button may not contain another button.
- **Segmented** — `<Segmented aria-label="Mode" bind:value items={[{ value, label, icon?, badge?, title? }]} />`;
  a radiogroup (arrow keys move and select), `fullWidth` stretches items, narrow screens scroll sideways.
- Number inputs bind numbers (`null` when empty): hand `<Input type="number">` the number itself, never
  `String(n)`, or Svelte rewrites partial input such as `-0` while the user types.

## Generation building blocks (`src/lib/components/generator/`)

Shared by the composer, batch pages, Library and experiments; import from `$lib/components/generator`.
Each file documents its props at the top. Conventions: selections are `$bindable` and replaced (never
mutated); `onchange` fires only on user edits; catalogs load once per page (`loadModelCatalog`,
`loadAppSpecCatalog`, `loadPresets` — browser-only caches); loading uses an inline spinner or
`LoadingState`, failures `ErrorAlert` with retry.

| Component              | Use for                                                                                                         |
| ---------------------- | --------------------------------------------------------------------------------------------------------------- |
| `ModelPicker`          | models from the whole catalog; `mode` single/multi, `requireTools` pins ineligible picks with "Remove N"        |
| `AppSpecPicker`        | app specs (graded tasks): search, category chips, difficulty, select shown / clear                              |
| `StackSelect`          | stack slug; own unapproved stacks marked "Pending approval — only you can run it"; `defaultSlug`                |
| `PresetPicker`         | one harness / sampling / budget preset; `pinned` keeps an old version ("v1 · latest is v3 · Use latest")        |
| `HarnessOptionsEditor` | schema-driven harness options; sparse (default values are removed)                                              |
| `LLMParamsEditor`      | sparse sampling params; blank = inherit, placeholder "0.3 · mode default"; `mode` hides fields that don't apply |
| `ProfilePicker`        | scaffolding prompt profile or "Auto" (with suggestions) and a rendered-prompt preview                           |
| `OpenRouterKeyBanner`  | key status: own key, deployment fallback, missing, rejected, or "couldn't check" (never a false "required")     |

- Sparse editors never emit `null`/`''`; a field holding a value that wasn't applied (out of range, bad
  JSON) sets the bindable `invalid` output — block submit on it.
- Vocabulary: **Harness preset**, **Sampling preset**, **Budget preset**; sampling settings apply
  identically to every model (per-model support is shown in previews, not tuned per model).
- After creating a preset outside a picker, call `invalidatePresets(kind)`; mounted pickers reload.
  "Manage" links open `/templates?tab=presets&kind=…` in a new tab and refresh on window focus.
- API errors: show `formatApiError(e)` (handles `{detail}`, 422 `{detail: [{loc, msg}]}`, `{message}`,
  `{detail, remediation}`); map 422s onto fields with `apiFieldErrors(e)`; ignore `isAbortError(e)` from
  superseded requests.

## Experiments building blocks (`src/lib/components/experiments/`)

Import from `$lib/components/experiments`. Endpoint pieces (`SlotMeter`, `EndpointChip`, …) come from
`$lib/components/inference`.

| Component / module            | Use for                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------ |
| `ExperimentComposer`          | the single-page design form (`/experiments/new`); sticky `PlanRail` on ≥ 56rem, `StartBar` below |
| `ExperimentRow`               | one experiment in a list (progress, ETA, endpoints, inline lifecycle buttons)                    |
| `CapacityStrip`               | one-line executor / lanes / endpoint capacity summary                                            |
| `monitor/ConditionGrid`       | condition × app × repeat squares (CSS grid, sticky label column, endpoint groups)                |
| `monitor/EndpointLane`        | one endpoint's live slots, loaded models, this user's active jobs, blocked reason                |
| `Leaderboard`, `PairedDeltas` | results: ranked conditions with CIs; Δ vs the pre-registered baseline (forest plot)              |
| `ComparabilityTable`          | what each condition actually runs with; differing columns highlighted                            |
| `live.svelte.ts`              | `ExperimentLive`: SSE + polling fallback; never hand-roll polling on experiment pages            |
| `cells.ts`, `format.ts`       | cell codes (`CELL_STATES` square styles), progress mapping, ETA / hours / Δ / p formats          |

- Scaling to ~30 models: everything is grouped by endpoint (picker, selection chips, plan rows, lanes, grid);
  condition labels are `model · harness`; compare against one baseline, never N² pairs.
- Cell squares carry meaning by shape as well as hue (solid = finished, ring = waiting, dashed = waiting
  for a model load) and always have an `aria-label`/`title`.
- Statistics: never show a pooled pass rate across conditions; p-values only when results are not interim.

## Color & type rules

- Components use tokens only: `primary`, `muted-foreground`, `success|warning|destructive|info`
  (fills/borders) and `success-fg|warning-fg|danger-fg|info-fg` (text on tints). No raw
  `red-500`/`emerald-400`/`blue-600` etc. outside `constants/colors.ts` and chart theme.
- Minimum text size 11px (`text-[11px]`); body/table text `text-sm`; no `text-[9px]`/`text-[10px]`.
- Status is never color alone: `StatusBadge` always shows a dot + text.
- `font-display`/`font-mono` via classes, never inline `style="font-family…"`.

## Decoration (usage rules)

- Swiss-minimal data surfaces: no glass, glow or gradients on cards, tables or lists.
- `.hero-panel` and `--gradient-brand` stay on the dashboard hero only.

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

- Ornate decoration; glass/glow on data-dense surfaces; hover lift on non-clickable cards
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
