# Design System: LLM Eval Lab

Pattern: **Research Lab** — Swiss Modernism 2.0 × Data-Dense Dashboard: an
editorial "journal" layer (serif titles, mono eyebrows, hairline rules) over
dense data surfaces (KPI tiles, real charts, sortable tables, 12-col bento).
Identity: **ink & paper + research indigo**, with ochre/teal supporting accents
and a faint graph-paper canvas. Generated with ui-ux-pro-max-skill (vendored at
`.claude/skills/ui-ux-pro-max`, query: `"AI research lab developer tool analytics"
--design-system --density 8 --variance 4 --motion 3`); chart color and
interaction rules follow the dataviz skill (palettes are validator-checked, not
eyeballed).

## Colors

Tokens live in `src/app.css` (`:root` light "paper" / `.dark` "observatory");
Tailwind v4 reads them via `@theme inline`. Dark stays the default mode.

| Role             | Light     | Dark      |
| ---------------- | --------- | --------- |
| Primary / CTA    | `#2c44bd` | `#7b91ff` |
| Accent 2 (ochre) | `#8a5a00` | `#e0b252` |
| Accent 3 (teal)  | `#0f6b62` | `#4fd1c5` |
| Info (sky)       | `#0284c7` | `#38bdf8` |
| Background       | `#f5f5f1` | `#0b111d` |
| Card             | `#ffffff` | `#111a2b` |
| Sidebar          | `#efefe9` | `#080d17` |
| Text             | `#141a26` | `#e4e8f0` |

- Indigo is the only action color. Ochre/teal are supporting accents (KPI ticks,
  figure strokes, brand dot) — never buttons. `info` is sky so "running" never
  reads as the action color.
- No gradients, glass or glow anywhere. The only decorative surfaces are the
  `.lab-canvas` graph paper (app/auth layouts) and the dashboard `.hero-figure`.
- All text tokens were checked ≥ 4.5:1 on their surfaces (most ≥ 6:1).

### Chart palette (validated)

`--chart-1..5` are categorical slots (indigo, teal, ochre, rose, violet),
validated with the dataviz six-checks script against each mode's surface:

- Light (on `#ffffff`): `#3b5bdb #0d9488 #c2850c #c2255c #7048e8`
- Dark (on `#121b2d`): `#4c6ef5 #0d9488 #c2850c #d6336c #7950f2`

Re-validate whenever surfaces or slots change:

```
node <dataviz-skill>/scripts/validate_palette.js "<hex,hex,…>" --mode dark --surface "#121b2d"
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

- **Display (serif):** Source Serif 4 (`--font-display`, `font-display`) — `h1`,
  `h2`, page titles, card titles, hero. Italic + primary for one emphasised phrase.
- **UI/body:** IBM Plex Sans (`--font-sans`). `h3`–`h6` stay sans.
- **Data (mono):** IBM Plex Mono (`--font-mono`, `font-mono`) — KPI values, big
  stat numbers, eyebrows, `.section-label`, `.kpi-label`, table headers, IDs.
- Hero scale: `.display-xl`. Page titles: `.page-header h1` (2xl/30px).
- KPI values: `.kpi-value` (3xl mono, tabular). Big numbers elsewhere use
  `font-mono font-medium tabular-nums`, never the serif.
- Eyebrow: `.page-eyebrow` (mono, uppercase, 0.14em tracking).

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

- `PageHeader` renders the section eyebrow (`02 · Generate`, from `navSectionFor` in `nav.ts`) or the
  `back` link, the serif `<h1>`, and the masthead rule (hairline + short primary bar).
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
| Logo / wordmark              | `BrandMark` (`compact`, `tagline`, `size="lg"`)                              | icon + hand-built "LLM Lab" spans                      |
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

## Tables and lists (`src/lib/components/table/`)

Import from `$lib/components/table`. Base `table`/`th`/`td` styles are global (`app.css`); don't restate
`px-3 py-2` or header classes on cells — explicit padding utilities defeat compact density.

| Need                               | Use                                                                                                                                                                                               |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Table inside a card                | `Card.Root class="gap-0 overflow-clip py-0"` → `TableToolbar` → `div.table-frame {@attach tableFrame}` → `table` (fits: page scrolls, header sticks under the app bar; wider: bounded scroll box) |
| Card title above a flush table     | `Card.Header class="border-b border-border py-3"` + `Card.Content class="p-0"`                                                                                                                    |
| Table inside a padded area         | `table-frame` + `rounded-md border border-border` (no card)                                                                                                                                       |
| Short, fixed tables (≤ ~8 rows)    | plain `overflow-x-auto`; a `max-h-*` box can't use `table-frame` (it un-bounds when the table fits)                                                                                               |
| Sortable header                    | `SortableTh label field sortBy sortDir onSort align? help? ascOnly?` (renders `<th aria-sort>`)                                                                                                   |
| Summary / bulk actions / density   | `TableToolbar` (`summary`, `bulk`, `controls` snippets; `selectedCount`, `onClearSelection`)                                                                                                      |
| Whole row opens a page             | `<tr class="row-link" onclick={rowLink(href)}>` + a real `<a>` in the name cell                                                                                                                   |
| Selection                          | `ui/checkbox` per row + `indeterminate` header box; `<tr data-selected={…}>`                                                                                                                      |
| Numbers                            | `td.num` / `th.num` (right, mono, tabular); missing value `—` in `text-muted-foreground`                                                                                                          |
| 0–1 scores                         | `ScoreCell` (number + neutral meter; `best` only for a leader held by ≤ 2 rows)                                                                                                                   |
| Secondary row actions              | wrap in `.row-actions-more` (revealed on hover/focus, always visible on touch)                                                                                                                    |
| Second line under a cell           | `.cell-sub` (hidden in compact density)                                                                                                                                                           |
| Wide table                         | `.col-sticky` on the identity column                                                                                                                                                              |
| Model names next to a provider     | `shortModelName()` / `modelNamePrefix()` ("Amazon: Nova 2 Lite" → "Nova 2 Lite" · Amazon)                                                                                                         |
| List of records (not tabular data) | `.data-list` (+ `@container`) with `.data-list-head` and `.data-list-row[data-active]`                                                                                                            |
| Mobile rendering of a table        | `.data-list` rows: identity line, status + numbers line, one next step + icon actions (≥ 36px)                                                                                                    |

- Density: the "Compact tables" preference sets `data-density` on the app shell; the toolbar toggle writes it.
- Scores are never colored red/amber/green by value; color marks status, not "low number".
- One primary next step per row (Open, Provision, Edit) as a ghost button; no outline buttons repeated per row.
- Hide columns that are empty on the whole page, and say so in the toolbar.

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

## Analysis building blocks (`src/lib/components/analysis/`, `src/lib/components/insights/`)

Runs, studies, profiles, tools and the insights pages (rankings, statistics, reports) share these.
The API client is `$lib/api/analysis` (domain modules `runs`, `studies`, `profiles`, `catalog`,
`targets`, `plans`).

| Component / module                   | Use for                                                                             |
| ------------------------------------ | ----------------------------------------------------------------------------------- |
| `analysis/format.ts`                 | the one set of number / time / hash formatters; missing → `NA` ("—"), never "0"     |
| `insights/format.ts`                 | the same formatters returning `null` for `MaybeValue`, outcome labels, report links |
| `NaValue`                            | a missing value: "—" plus the reason in an accessible tooltip                       |
| `Hint`                               | inline content with an accessible tooltip (lower bounds "≥ n", unverified versions) |
| `insights/MaybeValue`                | a formatted value, or `NaValue` when the formatter returned null                    |
| `insights/PlanHash`                  | a plan hash: short form, copy, link to the runs measured under it                   |
| `insights/CoverageFunnel`            | coverage of a job set under one plan (plan rule, measured / excluded / other plan)  |
| `ToolIcon`                           | a tool's brand mark (`toolLogos`) or catalog icon (falls back to the category icon) |
| `RunDataBadge`, `run-utils.ts` cells | the runs list "data" badge and `staticFindingsCell` / `aiConsensusCell`             |

- Research validity: missing data is NA with a reason, never 0; a count from a run whose tools did not
  all succeed is a lower bound ("≥ n"); AI consensus is always shown apart from static counts; an
  unknown AI spend is "—", a partial one "≥ $x".
- Runs are comparable only under one plan: show the plan hash next to every aggregated number.

## Color & type rules

- Components use tokens only: `primary`, `muted-foreground`, `success|warning|destructive|info`
  (fills/borders) and `success-fg|warning-fg|danger-fg|info-fg` (text on tints). No raw
  `red-500`/`emerald-400`/`blue-600` etc. outside `constants/colors.ts` and chart theme.
- Minimum text size 11px (`text-[11px]`); body/table text `text-sm`; no `text-[9px]`/`text-[10px]`.
- Status is never color alone: `StatusBadge` always shows a dot + text.
- `font-display`/`font-mono` via classes, never inline `style="font-family…"`.

## Decoration (usage rules)

- Swiss-minimal data surfaces: crisp 1px borders, `--shadow-card` hairline, no
  glass, glow or gradients on cards, tables or lists. Radius 0.3rem.
- `.lab-canvas` (graph paper) only on layout wrappers; `.hero-panel` +
  `.hero-figure` only on the dashboard.
- KPI tiles: left accent tick via `--kpi-accent`; hover = border + faint tint,
  no lift.

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
entrances, live status dots (`.status-dot-live`), KPI count-ups, indeterminate
scan-bar loader (`LoadingState` / `.loading-mono`).

## Avoid (anti-patterns)

- Ornate decoration; glass/glow/gradients; hover lift on non-clickable cards
- Terminal gimmicks (`$` prompts, blinking cursors, `// comments`) in UI copy
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
