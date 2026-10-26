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
  `.lab-canvas` graph paper (app/auth layouts).
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
  `h2`, page titles, card titles. Italic + primary for one emphasised phrase.
- **UI/body:** IBM Plex Sans (`--font-sans`). `h3`–`h6` stay sans.
- **Data (mono):** IBM Plex Mono (`--font-mono`, `font-mono`) — KPI values, big
  stat numbers, eyebrows, `.section-label`, `.kpi-label`, table headers, IDs.
- Page titles: `.page-header h1` (lg/24px).
- KPI values: `.kpi-value` (2xl mono, tabular). Big numbers elsewhere use
  `font-mono font-medium tabular-nums`, never the serif.
- Eyebrow: `.page-eyebrow` (mono, uppercase, 0.14em tracking).

## Page anatomy (every page under `(app)/`)

```svelte
<div class="space-y-4">
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

- `PageHeader` is a lean card (bordered `bg-card`, `px-4 py-2.5`, `text-lg` serif title): `[back] [leading]
Title [?] [meta] … [actions]`, then a one-line `description` (xs, muted). The optional `children` line
  (stats chips / IDs) renders as a hairline-separated footer strip of the card; `leading` takes an avatar
  or entity icon. No eyebrow: orientation (`Section › Parent › Page`) is the top bar breadcrumb, which ends
  on the `<h1>` PageHeader publishes (`$lib/stores/page-title.svelte`). `back` renders an icon button;
  glossaries / how-it-works go in the `help` snippet (popover behind the `?`), never as extra paragraphs.
- Nothing sits between the header card and the content. Page-level tabs or a mode switch go in the
  `nav` snippet (flush bottom strip; wrap the page in `Tabs.Root`); plain counts go in `children` as
  `.header-stats` / `.header-stat` (`<b>16</b> total`), not as a KPI row; one-line notices go right-aligned
  in the `nav` strip or into `children`. Search/filter controls for a table are the top strip of that
  table's card (`FilterBar embedded`, or a `.table-toolbar`), never a separate card above it.
- `PageHeader` owns the `<h1>` and the browser `<title>` (`"{title} · LLM Lab"`) — no
  separate `<svelte:head><title>` and no hand-rolled `.page-header` markup.
- Vertical rhythm: `space-y-4` between page sections, `gap-4` inside grids, `gap-3` in KPI rows.
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
| Heading that divides content | `SectionHeading` (`size="lg"` on the canvas, `size="sm"` for groups)         | `border-t pt-3` + label, `border-b` under a title      |
| Small group label            | `.section-label` (a label over one field/value, no divider)                  | `text-[10px] uppercase …` variants                     |
| Pick one of a list           | `ui/select` (`Select.Simple` for plain lists, parts for rich rows)           | raw `<select>` with copied class strings               |
| Boolean / multi-choice field | `ui/checkbox` (`Checkbox` in a `<label>`, or with `<Label for>`)             | `<input type=checkbox>`, hand-drawn check boxes        |
| Mode switch (2–4 options)    | `ui/segmented` (`Segmented`)                                                 | pill rows with `aria-pressed` and active classes       |

Navigation lives in `src/lib/nav.ts` (sidebar, mobile menu, breadcrumbs). Add routes there.

### Section headings (`SectionHeading`)

One labelled divider for the whole app, in the sidebar's group motif: `[number] Title ───── [count | aside]`,
then an optional `lead` flush below. Styles are `.section-heading*` in `app.css`.

- **`lg`** (default) — a section of the page on the canvas: mono number, serif `text-xl` title, ink rule. The
  experiment page's numbered sections (`StudySection` wraps it). Sections sit `space-y-10` apart; heading → body
  `space-y-3`.
- **`sm`** — mono caps label + lighter rule: a group of cards on the canvas (Settings' Account / Preferences /
  Connections, Docs' "Start here") or a group inside a card (endpoint form, spec editor, "Key findings", "History").
  `count` puts a figure at the end of the rule ("API endpoints ─── 3").
- One left edge: number, title, lead and the cards below all start at the same x — no hanging number column, no
  indented lead.
- The rule is ink (`--foreground` at 30% / 22%), never `--border`: a border-coloured line vanishes into the
  graph-paper grid. Don't add a `border-t`/`border-b` around a `SectionHeading`; the rule is the divider.
- A section's title and the title of the figure or card directly under it must differ ("4.1 Runs" → "Table D1.
  Every run, in launch order"), never repeat.
- Card titles stay `Card.Title`/strip headers; nav rails (Library, Docs, Settings) keep their own group labels.

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

### Settings page (`/users/settings`)

Laid out like the Generation Library (`/templates`): `PageHeader`, a bookmark rail card in the Library nav's
shape (`.section-label` groups, active = `bg-primary/10 text-primary` + left tick, a status dot where the
Library shows counts), and every section as one card — heading strip (serif title, `?` popover with the
description, `StatusBadge`, copy-link) with its rows flush below — in a single scrolling list.

- The rail and the mobile sticky chips are bookmarks: they scroll to a section, follow the scroll position
  (scroll-spy) and keep `#id` in the address bar; deep links re-align while sections load.
- Section bodies are built only from `users-settings/` primitives, never their own `Card`s:
  - `SettingsBlock` — a shaded group strip (`title`, `description` string or snippet, `actions`) like the
    Library's group rows, then its rows, then an optional `footer` action strip.
  - `SettingsRow` — label + help left, control right (`stack` for long controls); inputs `sm:w-72`.
  - Custom rows (tokens, providers, channels) and stray notes use `.settings-row`; `.settings-footer` is the
    action strip, `.settings-link` the inline link style.
  - `CredentialRow` — a stored provider key (status, test, replace, remove, inline form); `CopyField` — a
    one-line copyable value.
- Snippets passed to a block (`footer`, `actions`) must be its direct children, not inside an `{#if}`.

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

### Sample-generator composer (`generator/composer/`)

All three modes (agent, template scaffolding, single prompt) share one layout:

- **Steps** are `ComposerSection`s. Required steps pass `done` (the number badge turns into a check) and
  `meta` ("3 selected"); a step holding an unapplied value passes `invalid` (alert badge, red border).
- **Optional steps** (model settings, budget) are `collapsible`: folded to a one-line `summary` of what
  applies right now ("Provider defaults v1 · no overrides · …") unless the draft already overrides
  something. The body stays mounted so its fields keep validating, and an `invalid` step unfolds itself.
- **Rail** (≥ 56rem container): `ComposerSectionNav` (the experiments composer's section index) above the
  Run summary. The summary opens with `RunFormula` — `2 app specs × 2 models × 1 harness × 3 trials = 12
runs` — whose empty factors are dashed and link to their step; it replaces the old checklist (neutral, no
  error before the user acts). Narrower, the summary follows the form and `StartBar` sticks to the bottom.

## Experiments building blocks (`src/lib/components/experiments/`)

Import from `$lib/components/experiments`. Endpoint pieces (`SlotMeter`, `EndpointChip`, …) come from
`$lib/components/inference`.

| Component / module            | Use for                                                                                                             |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `ExperimentComposer`          | the single-page design form (`/experiments/new`); sticky `PlanRail` on ≥ 56rem, `StartBar` below                    |
| `ComposerSectionNav`          | the composer's section index above the Plan: current step, required done / to do, invalid flags                     |
| `ExperimentRow`               | one experiment in a list (progress, ETA, endpoints, inline lifecycle buttons)                                       |
| `CapacityStrip`               | one-line executor / lanes / endpoint capacity summary                                                               |
| `monitor/ConditionGrid`       | condition × app × repeat squares (CSS grid, sticky label column, endpoint groups); `framed={false}` inside a figure |
| `monitor/EndpointLane`        | one endpoint's live slots, loaded models, this user's active jobs, blocked reason                                   |
| `monitor/FailureSummary`      | Abstract: failed runs grouped by error message, with a likely cause (`failureHint`) and a run link                  |
| `Leaderboard`, `PairedDeltas` | results: ranked conditions with CIs (table only); Δ vs the baseline as an ink forest plot                           |
| `study/StudyMasthead`         | the experiment page header (replaces `PageHeader` there): accession id, hypothesis, spec strip                      |
| `study/StudySection`          | a numbered section ("2.3 Conditions ───") with a lead: a `<section>` around a `SectionHeading`                      |
| `study/StudyFigure`           | a numbered figure / table ("Figure 1", "Table M2") with title, controls and a caption                               |
| `study/PassRatePlot`          | dot-and-whisker figure of pass rate with 95% CIs (stacked rows on phones)                                           |
| `study/StudyRecord`           | the Abstract's record panel (dates, design counts, provenance, copyable reference)                                  |
| `study/findings.ts`           | design shape, accession id and the Abstract's key findings / limitations (pure, unit-tested)                        |
| `study/results.svelte.ts`     | `ResultsFeed`: one tab's `GET /results/` (abort-on-supersede, tick throttle)                                        |
| `ComparabilityTable`          | what each condition actually runs with; differing columns highlighted                                               |
| `live.svelte.ts`              | `ExperimentLive`: SSE + polling fallback; never hand-roll polling on experiment pages                               |
| `cells.ts`, `format.ts`       | cell codes (`CELL_STATES` square styles), progress mapping, ETA / hours / Δ / p formats                             |

- Scaling to ~30 models: everything is grouped by endpoint (picker, selection chips, plan rows, lanes, grid);
  condition labels are `model · harness`; compare against one baseline, never N² pairs.
- Cell squares carry meaning by shape as well as hue (solid = finished, ring = waiting, dashed = waiting
  for a model load) and always have an `aria-label`/`title`.
- Statistics: never show a pooled pass rate across conditions; p-values only when results are not interim.

### Experiment page (`/experiments/[id]`) — the study record

Laid out like a paper, not a dashboard (ui-ux-pro-max query `"scientific research experiment results academic
paper data" --design-system --density 7 --variance 3 --motion 2`, mapped onto this system's tokens and fonts):

- **Masthead** (`StudyMasthead`): eyebrow `EXPERIMENT · AGENT · EXP-955A5BD5` (first UUID block as an accession
  number), serif title, status + registered / launched / finished, the hypothesis as a serif-italic pull quote
  with an ochre (`accent-2`) rule, then a spec strip — Design (`k conditions × n apps × r repeats = N cells`) ·
  Baseline · Outcome · Randomisation — then the data-collection strip (counts, ETA, `LiveStrip`) and the tabs.
- **Sections** (`?tab=`, numbered in the tab row): 1 Abstract (`summary`) · 2 Method (`method`) · 3 Results ·
  4 Data (`data`) · 5 Operations (`operations`). Older names (`overview`, `design`, `runs`, `automation`) still
  resolve (`study/tabs.ts`). Science first: execution limits, endpoints, activity and automation live in
  Operations, never in Method.
- **Abstract**: description and a generated paragraph (design, baseline, outcome, estimator) in the display
  serif (15px, `max-w-3xl`), a status sentence, numbered key findings and a "Limitations" list derived from the
  pre-registered analysis (`keyFindings`); Figure 1 (pass rate), Figure 2 (data collection); the record panel
  sticks on the right from `xl`.
- **Figures and tables** are numbered and captioned: Abstract Figures 1–2, Results Table 1 · Figure 3 · Table 2,
  Method Tables M1–M3, Data Table D1. A caption says what the mark is, the estimator, n and the exclusions —
  explanations go in the caption, not in paragraphs above the figure.
- **Plots** are ink on paper: one series in `chart-1`, the baseline as a hollow diamond plus a hairline through
  every row, CI whiskers with end caps; forest-plot squares are filled when the 95% interval excludes 0 and hollow
  when it includes it. Never red/green for "better/worse" — the axis carries the sign. Values are printed beside
  the marks (tooltips only repeat them); below `sm` plots switch to stacked rows instead of scrolling sideways.
- **Results** opens with the analysis specification ("Pre-registered — common cells only · …"); any URL option
  turns it into "Exploratory" with a way back to the pre-registered analysis. Server notes are lettered footnotes.
- Sections whose parts can be hidden number themselves from what is shown (no "5.1 → 5.3" gaps).

## Analysis building blocks (`src/lib/components/analysis/`)

Used by `/analysis`, `/analysis/create`, `/analysis/[id]` and the app page's Analyses card.

| Component / module        | Use for                                                                                               |
| ------------------------- | ----------------------------------------------------------------------------------------------------- |
| `LevelBadge`              | a finding's level exactly as its tool printed it (`tool_severity`); nothing for tools that rate none  |
| `RunStatusCell`           | run status in tables: badge, and while active a bar + "3/12 tools · Semgrep" / queue / phase          |
| `RunFindingsCell`         | total findings (so far, while active) + up to three tool-reported levels                              |
| `RunLiveStrip`            | an active run's progress: one segment per tool, running tool + clock, connection state                |
| `ToolPipeline`            | a run's tools in execution order: state, time, version, findings, warnings, error, Output/Findings    |
| `ToolOutputDialog`        | one tool's raw result (command, exit code, stdout, options)                                           |
| `RunOverview`             | coverage KPIs, attention notes, levels/tools bars, top files/rules (each opens filtered findings)     |
| `FindingsExplorer`        | facet rail + search + sort + expandable rows + CSV/JSON export; filters are bindable (URL-synced)     |
| `FindingDetail`           | everything a tool said about one finding, from `finding-data.ts` (standard + legacy keys merged)      |
| `MetricsPanel`            | metric tools' aggregates, generic over keys                                                           |
| `AppPicker`, `ToolPicker` | the creator's app multi-select (running-app filter) and tool selection with preview applicability     |
| `live.svelte.ts`          | `RunWatcher`: SSE on `analysis:<id>` channels + polling fallback + 1 s clock; never hand-roll polling |
| `format.ts`, `tools.ts`   | status/phase/result-state labels, clocks, level ordering and tints; the tool catalog (loaded once)    |

- **Never display the normalized `severity`.** It is a heuristic for tools that rate nothing and exists only
  for aggregate statistics. Show `tool_severity` with `LevelBadge`; count unrated findings apart ("Not rated by
  tool"), never fold them into a level. `levelClasses` tints a tool's own word, it doesn't rate it.
- A skipped tool measured nothing unless its `skip_reason` is `not_applicable`: say why (`resultState`), never
  show it as a clean "0 findings".
- Live pages show elapsed clocks from `started_at` and the run's `phase`; a long tool is legitimately quiet, so
  the watcher keeps a slow poll even with SSE open.

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
- `.lab-canvas` (graph paper) only on layout wrappers. No hero panels or
  decorative figures: the dashboard opens with a plain `PageHeader` like every page.
- Density: catalogues and inventories (tools, containers, specs) are tables, not card
  grids; zero-state panels use `EmptyState compact` or one muted line, never a
  full-height placeholder.
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
