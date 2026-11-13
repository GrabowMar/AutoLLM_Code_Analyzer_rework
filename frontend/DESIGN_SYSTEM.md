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

### List pages and headline figures

- **Header stats:** every list page puts its counts in `PageHeader` as `.header-stats` (`<span class="header-stat"><b>203</b> apps</span>`), computed for the current filters.
- **List toolbar:** the top strip of the table card is `.tb-bar`: a `.tb-search` input first, then one `FilterMenu`
  (`$lib/components/FilterMenu.svelte`, single or `multiple`, `hint` = count) per facet, toggles as `.fm-trigger` buttons,
  and "Clear N" when filters are set. Filters and sort live in the URL. No hidden "Filters ▾" panels, no stacked full-width selects.
- **Figure strip:** headline figures are one card, `.fig-strip` > `.fig-cell` > `.fig-label` / `.fig-value` / `.fig-sub`;
  `data-tone="good|warn|bad"` tints a verdict. Not measured is "—", never 0.
- **Rows:** long ids shortened with the full value in `title`; model ids show the last segment with the endpoint muted;
  ratios get a thin bar beside the number; slow loads show skeleton rows, not a bare "loading …" line.

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
  study page's numbered sections (`StudySection` wraps it). Sections sit `space-y-10` apart; heading → body
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

Laid out like the Library (`/library`): `PageHeader`, a bookmark rail card in the Library nav's
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

## Study building blocks (`src/lib/components/studies/`)

Import from `$lib/components/studies` (or a single component by path); the generic pickers come from
`$lib/components/studies/shared`, the Budget preset editor and library pickers from `$lib/components/library`.
Each file documents its props at the top. Conventions: selections are `$bindable` and replaced (never
mutated); `onchange` fires only on user edits; catalogs load once per page (`loadModelCatalog` — a
browser-only cache); loading uses an inline spinner or `LoadingState`, failures `ErrorAlert` with retry.

| Component / module                        | Use for                                                                                                                                    |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `StudyBuilder`                            | the step-by-step experiment builder (`/experiments/new`); see “Experiment builder” below                                                   |
| `BuilderStepper`, `StepPanel`             | the builder's non-linear progress row (state + summary per step) and the frame of the open step (heading, lead, Back / Continue)           |
| `BlueprintRail`                           | the builder's sticky plan: cells as arithmetic, cost vs cap, to-dos and problems per step, Review & launch                                 |
| `OptionCards`, `BuilderGroup`             | a one-of-N choice that needs a sentence per option (radiogroup of cards); a titled group in a step, optionally a folded `<details>`        |
| `ComposerSection`, `ComposerSectionNav`   | numbered form sections with a section index (the analysis composer, `/analysis/create`)                                                    |
| `FactorTable`, `LevelPicker`              | factors × levels; the role (crossed / fixed / fraction) follows the level count; an empty factor shows "default: …"                        |
| `ResourcePolicyPicker`                    | tier cards (Frugal / Standard / Generous) + Custom + `ResourcePolicyEditor`; "Save as preset" pins a new immutable preset                  |
| `EvaluationSection`                       | the evaluation level and what it measures; unmeasured parts preview as "not measured", never as 0                                          |
| `LaunchDialog`                            | the cost confirmation before Start: expected / p90 / worst case; typed p90 confirmation above the preset's threshold                       |
| `study/StudyMasthead`                     | the study page header (replaces `PageHeader` there): accession id, hypothesis, spec strip                                                  |
| `study/StudySection`, `study/StudyFigure` | a numbered section ("2.3 Conditions ───") with a lead; a numbered figure / table with title, controls and a caption                        |
| `study/tabs.ts`                           | the record's sections in paper order, numbered per mode, plus the aliases older links use                                                  |
| `monitor/*`                               | `LiveSpendStrip`, `PilotGateCard`, `ConditionGrid`, `FailureSummary`, `ActivityFeed`, `RunStreamPanel`, `CapacityStrip`                    |
| `monitor/live.svelte.ts`                  | `StudyLive`: SSE + polling fallback feeding the monitor pieces; never hand-roll polling on study pages                                     |
| `budget/*`                                | `LedgerTable`, `SpendCharts`, `CapsCard`: the spend ledger, spend charts, caps vs spent                                                    |
| `layout/SpendChip`                        | the top-bar spend indicator                                                                                                                |
| `shared/*`                                | generic pickers moved from v2 `generator/`: `ModelPicker`, `LLMParamsEditor`, `HarnessOptionsEditor`, `OpenRouterKeyBanner`, `NumberField` |

- **Experiment builder** (`/experiments/new`): seven steps — Setup · Models · Tasks (slot: Targets) ·
  Treatments · Runs & budget · Measurement · Review — one open at a time in a `StepPanel`, under a
  `BuilderStepper` and beside the `BlueprintRail` (≥ 56rem container; narrower, a sticky bottom bar). Step
  names, required choices and the issue → step mapping live in `builder/steps.ts` (unit-tested).
  - Non-linear: any step opens from the stepper; the open step is the URL hash (`#models`); opening one
    focuses its heading. A fresh `?models=` link opens on Tasks, a resumed complete draft on Review.
  - Required choices are a neutral to-do in the rail, never red errors before the user acts. "Continue"
    past an unfinished required step first explains what is missing (a second press goes on) and the step
    turns amber in the stepper; server errors turn it red.
  - Launch is one action on Review: save → freeze the current plan → `LaunchDialog` (cost confirmation) →
    attach the post-run chosen in Measurement (`launch.ts`, never twice) → launch.
  - Before the first server save (it needs models, tasks and a stack) the design is kept in
    `sessionStorage` and restored with a "Start over" option; after it, the draft autosaves server-side.
    An edit that empties a required choice of a saved draft is held (`BuilderState.held`), shown as a to-do,
    and never sent as a save the server would refuse.
  - Accessibility (audited with axe + Playwright at 1440, 375 and 812×375): one atomic `role=status`
    message for plan updates (never a bare number per tile); `scroll-padding` on the root while the builder
    is mounted so focus never lands under the sticky app bar or bottom bar; the bottom bar stops being
    sticky on short (landscape) viewports; checkboxes and switches extend their pointer target to ≥ 24px
    with an `::after`; truncated text carries a `title`; no text under 11px; state never by color alone.
  - Bulk clears (all models, all tasks) are undoable with a toast action instead of a confirmation.
- **Pick lists** (`ModelPicker`, `TaskPicker`, `LibraryRefPicker`): search matches every word anywhere
  (`$lib/utils/search`) and marks it (`Highlight`); Escape in the search clears it; "no match" offers a
  clear-filters action. `ModelPicker` adds capability chips with live counts (reasoning, vision, open
  weights, free, new), a sort (by vendor / cheapest / longest context / newest; routers with variable
  prices sort last), no accordions: rows sit under light sticky vendor/endpoint labels below a sticky
  column header (Model · Abilities · Context · Price / 1M) on one grid, so numbers align; absent abilities
  stay as faint placeholders; selected rows carry an accent bar; rows render 80 per page ("Show more").
  The builder's Models step puts the chosen models in a full-width tray above the catalog. Long chip rows show the 8 most common
  plus an operable "+N more". Logos beside their own name are `ProviderIcon decorative`.
- **Collapsible groups** (`BuilderGroup collapsible`) fold settings with good defaults (seeds, grid &
  scheduling) to a one-line `summary` of what applies now; they open by themselves when they hold a
  non-default value.
- Sparse editors never emit `null`/`''`; a field holding a value that wasn't applied (out of range, bad
  JSON) sets the bindable `invalid` output — block submit on it.
- Vocabulary: **Harness preset**, **Sampling preset**, **Budget preset** (the `ResourcePolicy` library
  kind; never "resource policy" in UI copy); sampling settings apply identically to every model
  (per-model support is shown in previews, not tuned per model).
- Presets are library kinds. After creating or versioning any library entity outside a picker, call
  `invalidateLibrary(kind)` from `$lib/components/library`; mounted pickers and lists reload. "Manage"
  links open `/library/{kind}` (the API kind name: `harness-presets`, `sampling-presets`,
  `resource-policies`, …) in a new tab and refresh on window focus.
- API errors: show `formatApiError(e)` (handles `{detail}`, 422 `{detail: [{loc, msg}]}`, `{message}`,
  `{detail, remediation}`); map 422s onto fields with `apiFieldErrors(e)`; ignore `isAbortError(e)` from
  superseded requests.
- Scaling to ~30 models: condition labels are `model · harness`; compare against one baseline, never N² pairs.
- Cell squares carry meaning by shape as well as hue (solid = finished, ring = waiting) and always have an
  `aria-label`/`title`.
- Statistics: never show a pooled pass rate across conditions; p-values only when results are not interim;
  anything not measured is "—" / "not measured", never 0.

### Study page (`/experiments/[id]`) — the study record

Laid out like a paper, not a dashboard (ui-ux-pro-max query `"scientific research experiment results academic
paper data" --design-system --density 7 --variance 3 --motion 2`, mapped onto this system's tokens and fonts):

- **Masthead** (`StudyMasthead`): eyebrow `STUDY · APP · STU-955A5BD5` (first UUID block as an accession
  number), serif title, status + registered / launched / finished, the hypothesis as a serif-italic pull quote
  with an ochre (`accent-2`) rule, then a spec strip — Design (`k conditions × n apps × r repeats = N cells`) ·
  Baseline · Outcome · Randomisation — then the data-collection strip (counts, ETA, `LiveStrip`) and the tabs.
- **Sections** (`?tab=`, numbered in the tab row from what the mode shows): Abstract (`summary`) · Method
  (`method`) · Results · Patterns (slot studies only) · Data (`data`) · Operations (`operations`). Older names
  (`overview`, `design`, `plan`, `runs`, `monitor`, `budget`, `automation`) still resolve (`study/tabs.ts`). Science first: execution limits, endpoints, activity and automation live in
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

## Library building blocks (`src/lib/components/library/`)

The Library (`/library`, `/library/[kind]`, `/library/[kind]/[slug]`, `/library/prompts/playground`) and the
pieces the Study builder, sample and slot screens reuse. Import from `$lib/components/library`; each file
documents its props at the top. API types and clients are `$lib/api/v3/{types,library,harnesses}.ts`.

| Component / module                                 | Use for                                                                                                       |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `PinnedRefBadge`                                   | any library reference: mono `slug@v`, hash in the tooltip, "latest vN" when older; links to the version       |
| `LibraryRefPicker`                                 | pick one / several entities of a kind; emits **pinned** refs; an older pin stays selected with "Use latest"   |
| `ResourcePolicyEditor`, `KnobField`                | the Budget preset editor: Key limits always visible, accordion groups, class badges, overrides vs a base      |
| `PromptTemplateEditor`                             | a Jinja template: variable autocomplete, declared-variable lint, live render on a sample cell, diff vs base   |
| `StimulusText`                                     | rendered prompt text with its source spans tinted by owner (legend, click-through to the owning entity)       |
| `PromptPlayground`, `RenderContextPicker`          | render any prompt set on any cell; compare two cells (per-role diff, prompt hash)                             |
| `TaskEditor` (+ `task.ts`)                         | the structured TaskSpec editor (Entities / Endpoints / UI / Acceptance / Probes / Prose ×3 / Preview)         |
| `ContentEditor`                                    | the right editor or viewer per kind, with a "Raw JSON" switch                                                 |
| `VersionHistory`, `VersionDiff`                    | versions of a slug (pick two to compare); unified + structured diffs with line numbers                        |
| `LibraryNav`, `LibraryList`                        | the kind rail (grouped by what each kind answers about a run) and a kind's table with server-side filters     |
| `PackagesDialog`                                   | YAML package v4 export (with dependencies) and import (dry run first, never overwrites)                       |
| `libraryStore`, `invalidateLibrary`                | per-kind cache shared by pickers, badges and counts; `ensureMe()`, `ensureKnobs()` (KNOB_META + tier presets) |
| `policy.ts`, `spans.ts`, `diff.ts`, `variables.ts` | pure helpers (unit-tested): knob paths/groups/classes/summary line, span segmentation, diffs, autocomplete    |

- **Immutability is visible.** Saving always says "Save as vN+1"; old versions open read-only (`?v=`) with a
  link to the latest and a compare action; archiving hides a slug from pickers but never deletes a version.
  Seeded (system) rows are read-only for non-staff; the action is **Fork**, which records `derived_from`.
- **References are pinned.** Show refs with `PinnedRefBadge` (mono), never a bare name; pickers store the
  pinned form `slug@v#hash12` returned by the API.
- **Budget preset vocabulary.** Tiers are **Frugal** (the default for new studies), **Standard**, **Generous**
  and **Custom**; the one-line summary is `summaryLine()` ("Frugal v1 · $0.30/run · auto study cap · 2 parallel
  · effort low · no overrides", the same text the backend's `summary_line` returns). The **Key limits** group
  (study cap incl. `auto`, run cap, cell cap, max parallel, reasoning, serving tier, pilot on/off; repeats live in
  the study) is always visible; the other knobs sit in accordion groups that open when they hold an override.
- **Validity classes are labelled, not colour-coded.** C/R knobs carry an outlined "affects treatment" badge
  (indigo text) whose tooltip explains the class; S knobs "study design"; O knobs only their class letter.
  Editing a C/R knob of a preset used by studies (or in a cloned/running study) shows the warning "New
  fingerprint — not comparable with earlier runs". Overrides show "Base: <value>" with a Reset.
- **Budget never enters a prompt.** The playground and editors say so where it matters; the render context has no
  budget, seed or id, so changing only the Budget preset leaves the prompt hash unchanged.
- **Disabled harnesses are listed with their blocked reason**, never hidden.
- Validation is server-side and live: drafts are posted to `/library/{kind}/validate/` (debounced, aborted when
  superseded) and the issues (registered codes, never invented severities) are listed beside the editor.

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

## Report building blocks (`src/lib/components/reports/`)

Reports are set like papers, in the study record's idiom (ui-ux-pro-max query `"research report document academic
data tables" --design-system --density 7 --variance 3 --motion 2`: Swiss minimal + serif headings, mapped onto
this system's tokens and fonts).

| Component / module  | Use for                                                                                                                                                                                                                                                                     |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ReportMasthead`    | report header (replaces `PageHeader`): `REPORT · TYPE · RPT-XXXXXXXX` eyebrow, serif title, status + dates, note as a pull quote, spec strip (scope · window · analysis · randomisation) from the config                                                                    |
| `ReportKeyFigures`  | the summary as one hairline-divided panel: leader (wide), rates with a neutral meter, counts, `by_*` maps as ranked share bars                                                                                                                                              |
| `ReportTableFigure` | one server table as "Table N" (`StudyFigure`), laid out by shape: Item/Value → definition list, Level/Finding → threats list, else a data table (shares get meters, tool levels chips, all-blank columns hidden and named in the caption, long tables folded after 12 rows) |
| `ReportContents`    | numbered, scroll-following contents: sticky rail from `xl`, folded disclosure below                                                                                                                                                                                         |
| `ReportChart`       | a table as horizontal bars (Table / Chart switch): one measure at a time (rates with an interval first), `chart-1` bars, 95% CI whiskers, values printed beside, per-row hover/focus tooltip; compare overlay in `chart-2` with a legend                                    |
| `WhatIfRanking`     | study reports: TOPSIS re-ranked live from weight sliders (`ranking.ts`, a port of `mcdm.topsis_scores`; the report's weights reproduce its scores), rank moves as arrow + number                                                                                            |
| `ReportNotebook`    | the Notebook tab: JupyterLite in a same-origin iframe; writes `reports/RPT-…/report.json` + a generated `report.ipynb` (`notebook.ts`) through `window.jupyterapp`, opens it in the app's theme                                                                             |
| `compare.ts`        | `?compare=<id>`: tables matched by title (model ≡ condition grouping), rows by first cell, signed deltas between same-unit numbers                                                                                                                                          |
| `catalog.ts`        | report types: label, icon, group (research / platform), purpose, what it needs, what it contains                                                                                                                                                                            |
| `format.ts`         | pure helpers (`planTable`, `shareOf`, `specItems`, `keyFigures`, `headline`, …), unit-tested                                                                                                                                                                                |

- Cells stay as the server formatted them (the Markdown/LaTeX exports' text); the page only decides layout.
  `-` renders `—` (not measured), never 0. Meters are neutral; only a leader held by ≤ 2 rows (and not by every
  row) turns primary.
- Every data table has a toolbar strip: filter (> 8 rows), Columns (> 4), CSV and copy-as-Markdown of what is shown;
  headers sort on click (numbers descending first, blanks last). Compare deltas are neutral ink ("+0.011", "−5 pp").
- `/reports/create` is one page: type (radio cards, grouped) → scope and options → optional title/note, with a
  sticky preview (auto title, scope, contents, what is missing) holding the Generate button.

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
