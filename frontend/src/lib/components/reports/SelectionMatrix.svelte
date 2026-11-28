<script lang="ts">
	/**
	 * SelectionMatrix — the cross-experiment sample as a group × task grid (~10 × 30): one glyph per unit
	 * (`SelectionMark`: shape + hue, never hue alone), a legend whose entries count each kind and, pressed,
	 * highlight it, per-group totals at the row ends and per-task coverage under the columns (runs in the
	 * sample only). Tasks left out by complete cases are struck through and dimmed; a selected run left out
	 * (dropped task, or truncated) has its own glyph and says why in its label.
	 *
	 * Accessibility: an ARIA grid with one tab stop (roving focus; arrows, Home/End, Ctrl+Home/End); every
	 * cell has an `aria-label` and tooltip (group, task, status, outcome, run, experiment, superseded
	 * attempts). Hover or focus shows the unit in the detail strip below; click or Enter pins it, Escape
	 * unpins. Narrow screens scroll the grid sideways with the group column pinned; while more of the grid is
	 * off to the right its edge fades and a cue under it says so.
	 *
	 * Props: `selection` (normalised `report_data.selection`), `matrix` (`selectionMatrix(selection)`), `id`.
	 */
	import X from '@lucide/svelte/icons/x';
	import Pin from '@lucide/svelte/icons/pin';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import type { ReportSelection } from '$lib/api/reports';
	import { scrollHint } from '$lib/utils/scroll-hint';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import SelectionMark from './SelectionMark.svelte';
	import { CELL_KINDS, CELL_KIND_LABEL, cellKind, cellLabel, outcomeText, shortId, type CellKind, type SelectionMatrix } from './selection';

	interface Props {
		selection: ReportSelection;
		matrix: SelectionMatrix;
		id: string;
	}

	let { selection, matrix, id }: Props = $props();

	type Pos = [number, number];
	let active = $state<Pos>([0, 0]);
	let focused = $state<Pos | null>(null);
	let hovered = $state<Pos | null>(null);
	let pinned = $state<Pos | null>(null);
	let highlight = $state<CellKind | 'superseded' | null>(null);

	const G = $derived(matrix.groups.length);
	const T = $derived(matrix.tasks.length);
	const shown = $derived(pinned ?? hovered ?? focused);
	const cellId = (g: number, t: number) => `${id}-c${g}-${t}`;
	const same = (a: Pos | null, g: number, t: number) => !!a && a[0] === g && a[1] === t;

	function move(g: number, t: number) {
		active = [Math.max(0, Math.min(G - 1, g)), Math.max(0, Math.min(T - 1, t))];
		document.getElementById(cellId(active[0], active[1]))?.focus();
	}

	function onKey(e: KeyboardEvent) {
		const [g, t] = active;
		switch (e.key) {
			case 'ArrowRight':
				move(g, t + 1);
				break;
			case 'ArrowLeft':
				move(g, t - 1);
				break;
			case 'ArrowDown':
				move(g + 1, t);
				break;
			case 'ArrowUp':
				move(g - 1, t);
				break;
			case 'Home':
				move(e.ctrlKey || e.metaKey ? 0 : g, 0);
				break;
			case 'End':
				move(e.ctrlKey || e.metaKey ? G - 1 : g, T - 1);
				break;
			case 'Enter':
			case ' ':
				pinned = same(pinned, g, t) ? null : [g, t];
				break;
			case 'Escape':
				if (!pinned) return;
				pinned = null;
				break;
			default:
				return;
		}
		e.preventDefault();
	}

	function dim(g: number, t: number): boolean {
		if (!highlight) return false;
		const c = matrix.cells[g][t];
		if (highlight === 'superseded') return !c || c.superseded_run_ids.length === 0;
		return cellKind(c) !== highlight;
	}

	const detail = $derived.by(() => {
		if (!shown) return null;
		const [g, t] = shown;
		const group = matrix.groups[g];
		const task = matrix.tasks[t];
		if (!group || !task) return null;
		const cell = matrix.cells[g][t];
		return { group, task, cell, kind: cellKind(cell) };
	});
	const studyName = (sid: string | null) => selection.studies.find((s) => s.id === sid)?.name ?? null;
	const notAll = (covered: number) => covered < G;
</script>

<div class="space-y-3">
	<!-- Legend: counts per kind; pressing one highlights it in the grid. -->
	<div id="{id}-legend" class="flex flex-wrap items-center gap-x-1 gap-y-1" role="group" aria-label="Legend: highlight one kind of cell">
		{#each CELL_KINDS as k (k.kind)}
			{@const n = matrix.kindCounts[k.kind]}
			{#if n > 0 || k.kind === 'tested' || k.kind === 'missing'}
				<button
					type="button"
					class="sm-legend"
					aria-pressed={highlight === k.kind}
					title={k.description}
					onclick={() => (highlight = highlight === k.kind ? null : k.kind)}
				>
					<SelectionMark kind={k.kind} />
					<span>{k.label}</span>
					<b class="font-mono font-medium tabular-nums text-foreground">{n}</b>
				</button>
			{/if}
		{/each}
		{#if matrix.supersededCells > 0}
			<button
				type="button"
				class="sm-legend"
				aria-pressed={highlight === 'superseded'}
				title="Cells with at least one older attempt (a retry, a rerun in a later experiment) superseded by the newest; the Superseded count above counts the attempts themselves"
				onclick={() => (highlight = highlight === 'superseded' ? null : 'superseded')}
			>
				<svg viewBox="0 0 16 16" class="size-3.5" aria-hidden="true"><circle cx="8" cy="8" r="3.2" class="sm-legend-dot" /></svg>
				<span>Cells with superseded attempts</span>
				<b class="font-mono font-medium tabular-nums text-foreground">{matrix.supersededCells}</b>
			</button>
		{/if}
	</div>

	<div class="sm-scroll scroll-hint" {@attach scrollHint({ focusable: false })}>
		<div
			role="grid"
			tabindex="-1"
			aria-label="Sample coverage: {G} groups by {T} tasks"
			aria-describedby="{id}-legend {id}-help"
			aria-rowcount={G + 2}
			aria-colcount={T + 2}
			class="sm-grid"
			style:--sm-n={T}
			onmouseleave={() => (hovered = null)}
		>
			<div role="row" class="sm-row sm-head" aria-rowindex={1}>
				<div role="columnheader" class="sm-sticky sm-corner" aria-colindex={1}>
					<span class="section-label">Group</span>
					<span class="section-label">Tasks</span>
				</div>
				{#each matrix.tasks as task, t (task.ref)}
					<div
						role="columnheader"
						aria-colindex={t + 2}
						class="sm-th"
						data-tick={t % 5 === 4 && t < T - 1 ? '' : undefined}
						data-active={shown?.[1] === t ? '' : undefined}
						data-dropped={task.dropped ? '' : undefined}
						title="{task.ref}{task.dropped ? ' — dropped: not every group has a selected run (complete cases)' : ''}"
					>
						<span class="sm-th-text">{task.label}</span>
						{#if task.dropped}<span class="sr-only">(dropped)</span>{/if}
					</div>
				{/each}
				<div role="columnheader" aria-colindex={T + 2} class="sm-count-h"><span class="sm-th-text">Selected</span></div>
			</div>

			{#each matrix.groups as group, g (group.label)}
				<div role="row" class="sm-row sm-body" aria-rowindex={g + 2}>
					<div
						role="rowheader"
						aria-colindex={1}
						class="sm-sticky sm-rh"
						data-active={shown?.[0] === g ? '' : undefined}
						title={group.label}
						aria-label={group.label}
					>
						<span class="min-w-0 truncate font-medium text-foreground">{group.name}</span>
						{#if group.treatment}<span class="shrink-0 rounded-sm border border-border px-1 font-mono text-[11px] leading-4 text-muted-foreground">t {group.treatment}</span>{/if}
						{#if group.prefix}<span class="min-w-0 shrink-[2] truncate text-[11px] text-muted-foreground max-sm:hidden">{group.prefix}</span>{/if}
					</div>
					{#each matrix.tasks as task, t (task.ref)}
						{@const cell = matrix.cells[g][t]}
						{@const kind = cellKind(cell)}
						{@const label = cellLabel(group.label, task.ref, cell)}
						<div
							id={cellId(g, t)}
							role="gridcell"
							aria-colindex={t + 2}
							tabindex={active[0] === g && active[1] === t ? 0 : -1}
							aria-label={label}
							aria-selected={same(pinned, g, t)}
							title={label}
							class="sm-cell"
							data-tick={t % 5 === 4 && t < T - 1 ? '' : undefined}
							data-dim={dim(g, t) ? '' : undefined}
							data-dropped={task.dropped ? '' : undefined}
							data-pinned={same(pinned, g, t) ? '' : undefined}
							onfocus={() => {
								active = [g, t];
								focused = [g, t];
							}}
							onblur={() => (focused = null)}
							onkeydown={onKey}
							onmouseenter={() => (hovered = [g, t])}
							onclick={() => {
								active = [g, t];
								pinned = same(pinned, g, t) ? null : [g, t];
							}}
						>
							<SelectionMark {kind} superseded={!!cell && cell.superseded_run_ids.length > 0} />
						</div>
					{/each}
					<div role="gridcell" aria-colindex={T + 2} class="sm-count" aria-label="{group.selected} of {group.inScope} tasks with a run in the sample">
						<span class={cn(group.selected < group.inScope && 'text-warning-fg font-semibold')}>{group.selected}</span><span class="text-muted-foreground">/{group.inScope}</span>
					</div>
				</div>
			{/each}

			<div role="row" class="sm-row sm-foot" aria-rowindex={G + 2}>
				<div role="rowheader" aria-colindex={1} class="sm-sticky sm-rh sm-foot-h">Groups with a run</div>
				{#each matrix.tasks as task, t (task.ref)}
					<div
						role="gridcell"
						aria-colindex={t + 2}
						class={cn('sm-foot-cell', notAll(task.covered) && 'text-warning-fg font-semibold')}
						data-tick={t % 5 === 4 && t < T - 1 ? '' : undefined}
						aria-label="{task.ref}: {task.covered} of {G} groups have a run in the sample"
						title="{task.covered} of {G} groups have a run in the sample"
					>
						{task.covered}
					</div>
				{/each}
				<div role="gridcell" aria-colindex={T + 2} class="sm-count"></div>
			</div>
		</div>
	</div>
	<!-- Shown only while the grid continues past the right edge (the edge also fades). -->
	<p class="sm-cue" aria-hidden="true">Scroll sideways for all {T} tasks and the totals<ArrowRight class="size-3.5" /></p>

	<p id="{id}-help" class="sr-only">Arrow keys move between cells; Enter pins a cell's details below the grid, Escape unpins.</p>

	<!-- Detail strip: the hovered, focused or pinned unit. -->
	<div class="sm-detail" data-pinned={pinned ? '' : undefined}>
		{#if detail}
			{@const c = detail.cell}
			<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
				<SelectionMark kind={detail.kind} superseded={!!c && c.superseded_run_ids.length > 0} class="size-4" />
				<span class="font-medium" title={detail.group.label}>{detail.group.name}</span>
				<span class="text-muted-foreground" aria-hidden="true">×</span>
				<span class="font-mono text-[13px]">{detail.task.ref}</span>
				<span class="text-xs text-muted-foreground">· {CELL_KIND_LABEL[detail.kind]}</span>
				{#if detail.task.dropped}<span class="rounded-sm border border-border px-1 text-[11px] text-muted-foreground">task dropped</span>{/if}
				{#if c?.excluded === 'truncated'}<span class="rounded-sm border border-border px-1 text-[11px] text-muted-foreground">truncated, left out</span>{/if}
				{#if pinned}
					<button type="button" class="ml-auto inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring" onclick={() => (pinned = null)}>
						<X class="size-3.5" aria-hidden="true" />Unpin
					</button>
				{/if}
			</div>
			{#if c && c.status !== 'missing'}
				<dl class="mt-2 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]">
					<div class="min-w-0">
						<dt class="section-label">{c.status === 'selected' ? 'Selected run' : 'Newest attempt'}</dt>
						<dd class="font-mono">
							{#if c.run_id}<a class="settings-link" href="/samples/{c.run_id}" title={c.run_id}>{shortId(c.run_id)}</a>{:else}—{/if}
						</dd>
					</div>
					<div class="min-w-0">
						<dt class="section-label">Experiment</dt>
						<dd class="truncate">
							{#if c.study_id}<a class="settings-link" href="/experiments/{c.study_id}" title={c.study_name ?? c.study_id}>{c.study_name ?? studyName(c.study_id) ?? shortId(c.study_id)}</a>{:else}—{/if}
						</dd>
					</div>
					<div>
						<dt class="section-label">Outcome</dt>
						<dd class="first-letter:uppercase">{outcomeText(c.outcome)}</dd>
					</div>
					<div>
						<dt class="section-label">Created</dt>
						<dd class="font-mono tabular-nums">{c.created_at ? formatDateTime(c.created_at) : '—'}</dd>
					</div>
					<div class="min-w-0">
						<dt class="section-label">Attempts</dt>
						<dd>
							<span class="font-mono tabular-nums">{c.attempts}</span>
							{#if c.superseded_run_ids.length}
								<span class="text-muted-foreground">· superseded</span>
								{#each c.superseded_run_ids as rid, i (rid)}{i > 0 ? ', ' : ' '}<a class="settings-link font-mono" href="/samples/{rid}" title={rid}>{shortId(rid)}</a>{/each}
							{/if}
						</dd>
					</div>
				</dl>
			{:else}
				<p class="mt-1.5 text-xs text-muted-foreground">No run of this group for this task in the pooled experiments.</p>
			{/if}
		{:else}
			<p class="flex items-center gap-1.5 text-xs text-muted-foreground">
				<Pin class="size-3.5 shrink-0" aria-hidden="true" />Hover or focus a cell to see which run was selected, where it came from and what it superseded; click or
				press Enter to pin it. Arrow keys move between cells.
			</p>
		{/if}
	</div>
</div>

<style>
	.sm-scroll {
		overflow-x: auto;
		overscroll-behavior-x: contain;
		padding-bottom: 2px;
	}
	.sm-cue {
		display: none;
		align-items: center;
		justify-content: flex-end;
		gap: 0.25rem;
		margin-top: -0.25rem;
		font-size: 11px;
		color: var(--muted-foreground);
	}
	.sm-scroll:global([data-overflow-end]) + .sm-cue {
		display: flex;
	}
	/* One column template shared by every row (subgrid): the group column fits its longest label up to
	   --sm-label-max (longer labels truncate, with the full id in the title), narrower where it is sticky. */
	.sm-grid {
		--sm-cell: 1.375rem;
		--sm-label-max: 20rem;
		--sm-count: 3.25rem;
		display: grid;
		grid-template-columns: fit-content(var(--sm-label-max)) repeat(var(--sm-n), var(--sm-cell)) var(--sm-count);
		width: max-content;
		min-width: 100%;
		outline: none;
	}
	@media (max-width: 1023px) {
		.sm-grid {
			--sm-label-max: 13.5rem;
		}
	}
	@media (max-width: 639px) {
		.sm-grid {
			--sm-label-max: 9.5rem;
		}
	}
	.sm-row {
		display: grid;
		grid-column: 1 / -1;
		grid-template-columns: subgrid;
		align-items: stretch;
	}
	.sm-body:hover > * {
		background-color: color-mix(in oklab, var(--primary) 4%, var(--card));
	}
	.sm-sticky {
		position: sticky;
		left: 0;
		z-index: 1;
		background: var(--card);
		box-shadow: 1px 0 0 color-mix(in oklab, var(--border) 70%, transparent);
	}
	.sm-corner {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0 0.625rem 0.375rem 0;
	}
	.sm-th,
	.sm-count-h {
		display: flex;
		align-items: flex-end;
		justify-content: center;
		padding-bottom: 0.375rem;
		border-bottom: 1px solid var(--border);
		color: var(--muted-foreground);
	}
	.sm-corner {
		border-bottom: 1px solid var(--border);
	}
	.sm-th-text {
		writing-mode: vertical-rl;
		transform: rotate(180deg);
		font-family: var(--font-mono);
		font-size: 11px;
		line-height: 1;
		white-space: nowrap;
		max-height: 10.5rem;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.sm-th[data-active] {
		color: var(--foreground);
		background: color-mix(in oklab, var(--primary) 9%, transparent);
	}
	.sm-th[data-dropped] .sm-th-text {
		text-decoration: line-through;
		opacity: 0.6;
	}
	.sm-rh {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		min-width: 0;
		padding: 0 0.625rem 0 0.125rem;
		font-size: 0.8125rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 45%, transparent);
	}
	.sm-rh[data-active] {
		box-shadow:
			inset 2px 0 0 var(--primary),
			1px 0 0 color-mix(in oklab, var(--border) 70%, transparent);
	}
	.sm-cell {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 1.5rem;
		cursor: pointer;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 45%, transparent);
		transition: opacity 150ms ease;
	}
	.sm-cell:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
		border-radius: 3px;
	}
	.sm-cell[data-pinned] {
		box-shadow: inset 0 0 0 1.5px var(--primary);
		border-radius: 3px;
	}
	.sm-cell[data-dropped] {
		opacity: 0.4;
	}
	.sm-cell[data-dim] {
		opacity: 0.18;
	}
	[data-tick] {
		box-shadow: inset -1px 0 0 color-mix(in oklab, var(--border) 80%, transparent);
	}
	.sm-count {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		padding-right: 0.25rem;
		font-family: var(--font-mono);
		font-size: 11px;
		font-variant-numeric: tabular-nums;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 45%, transparent);
	}
	.sm-foot > * {
		border-bottom: 0;
		padding-top: 0.25rem;
	}
	.sm-foot-h {
		font-size: 11px;
		color: var(--muted-foreground);
	}
	.sm-foot-cell {
		display: flex;
		justify-content: center;
		font-family: var(--font-mono);
		font-size: 11px;
		font-variant-numeric: tabular-nums;
	}
	.sm-legend {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		height: 1.75rem;
		padding: 0 0.5rem;
		border: 1px solid transparent;
		border-radius: 0.375rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
		cursor: pointer;
		transition:
			background-color 150ms ease,
			border-color 150ms ease;
	}
	.sm-legend:hover {
		background: var(--muted);
		color: var(--foreground);
	}
	.sm-legend:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.sm-legend[aria-pressed='true'] {
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
		background: color-mix(in oklab, var(--primary) 8%, transparent);
		color: var(--foreground);
	}
	.sm-legend-dot {
		fill: var(--foreground);
	}
	.sm-detail {
		min-height: 4.25rem;
		border: 1px solid var(--border);
		border-radius: 0.375rem;
		padding: 0.625rem 0.75rem;
		background: color-mix(in oklab, var(--muted) 35%, var(--card));
		font-size: 0.875rem;
	}
	.sm-detail[data-pinned] {
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
	}
</style>
