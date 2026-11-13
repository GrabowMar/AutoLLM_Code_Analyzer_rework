<script lang="ts">
	/**
	 * PrevalenceTable — feature × condition prevalence of a PatternRun (spec 7.9, 10.3 §4): p with a Wilson CI
	 * bar (repeated-looks adjusted for `adaptive_n` cells), measured n beside the ITT n, what the measured n
	 * leaves out (detector errors, n/a, no output), flags (`adaptive_n`, `low_effective_n`, bounds,
	 * provisional), the audit's precision / NPV, and correctness for security features. Prevalence is
	 * presence/absence: no severity is computed or shown.
	 *
	 * Layout (progressive disclosure): one block per feature. The pooled "all targets" row of each condition
	 * leads; the per-target rows open with the block's "N per target" toggle. Features that nothing measured
	 * (every row "not measured") are listed on one line under "Measured only" (on by default) instead of as
	 * blocks of empty rows. A not-measured row is always shown as "not measured", never as 0.
	 *
	 * Props (spec 10.8 contract): `patternRunId`, `target?`, `feature?`. Optional: `conditionLabels` and
	 * `targetLabels` (from the run summary), `onaudit(featureKey)` (shows an "Audit" action per feature).
	 */
	import * as Card from '$lib/components/ui/card';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Search from '@lucide/svelte/icons/search';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getPrevalence } from '$lib/api/v3/patterns';
	import type { PrevalenceRow } from '$lib/api/v3/types';
	import {
		ciBar,
		fmtAudit,
		fmtMethod,
		fmtN,
		fmtPrevalence,
		fmtProportion,
		fmtUnmeasured,
		groupBy,
		prevalenceFlags,
	} from './format';

	interface Props {
		patternRunId: string;
		target?: string;
		feature?: string;
		conditionLabels?: Record<string, string>;
		targetLabels?: Record<string, string>;
		onaudit?: (featureKey: string) => void;
	}

	let { patternRunId, target, feature, conditionLabels = {}, targetLabels = {}, onaudit }: Props = $props();

	let rows = $state<PrevalenceRow[]>([]);
	let loading = $state(true);
	let error = $state('');
	let showConditional = $state(false);
	let measuredOnly = $state(true);
	let query = $state('');
	let expanded = $state(new Set<string>());

	$effect(() => {
		const controller = new AbortController();
		loading = true;
		error = '';
		getPrevalence(patternRunId, { target, feature }, { signal: controller.signal })
			.then((out) => (rows = out))
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load prevalence.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	const isMeasured = (r: PrevalenceRow) => !!r.n_measured && r.p !== null && Number.isFinite(r.p);
	const isPooled = (r: PrevalenceRow) => !!r.pooled || r.target === 'all';
	const hasPooled = $derived(rows.some(isPooled));

	interface Block {
		key: string;
		head: PrevalenceRow;
		lead: PrevalenceRow[];
		perTarget: PrevalenceRow[];
		measured: boolean;
		/** Measured somewhere, and no measured variant has it (k = 0 on every measured row). */
		absent: boolean;
		size: number;
	}

	const blocks = $derived.by<Block[]>(() => {
		const q = query.trim().toLowerCase();
		const pool = rows.filter(
			(r) => (showConditional || !r.conditional_on) && (!q || `${r.feature} ${r.detector ?? ''}`.toLowerCase().includes(q)),
		);
		return groupBy(pool, (r) => `${r.feature}:${r.scope}${r.conditional_on ? '|cond' : ''}`).map(([key, list]) => {
			const pooled = list.filter(isPooled);
			return {
				key,
				head: list[0],
				lead: pooled.length ? pooled : list,
				perTarget: pooled.length ? list.filter((r) => !isPooled(r)) : [],
				measured: list.some(isMeasured),
				absent: list.some(isMeasured) && list.filter(isMeasured).every((r) => r.k === 0),
				size: list.length,
			};
		});
	});
	const shownBlocks = $derived((measuredOnly ? blocks.filter((b) => b.measured) : blocks).filter((b) => !b.absent));
	const absentBlocks = $derived(blocks.filter((b) => b.absent));
	let absentOpen = $state(new Set<string>());
	function toggleAbsent(key: string) {
		const next = new Set(absentOpen);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		absentOpen = next;
	}
	/** "0/60 ≤6.0%" per lead row: the count and the upper Wilson bound, which is what a 0 means here. */
	const absentSummary = (b: Block) =>
		b.lead.filter(isMeasured).map((r) => `${r.k}/${r.n_measured} (≤ ${fmtProportion(r.ci_high)})`).join(' · ');
	const unmeasuredBlocks = $derived(blocks.filter((b) => !b.measured));
	const rowCount = $derived(blocks.reduce((a, b) => a + b.size, 0));

	function toggle(key: string) {
		const next = new Set(expanded);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		expanded = next;
	}
	function expandAll(on: boolean) {
		expanded = on ? new Set(shownBlocks.filter((b) => b.perTarget.length).map((b) => b.key)) : new Set();
	}

	function conditionName(row: PrevalenceRow): string {
		return row.condition_label || conditionLabels[String(row.condition)] || `condition ${row.condition}`;
	}

	function targetName(id: string): string {
		return id === 'all' ? 'all targets (pooled)' : (targetLabels[id] ?? id.slice(0, 8));
	}
</script>

{#snippet prevRow(row: PrevalenceRow, label: string, sub: string)}
	{@const bar = ciBar(row.ci_low, row.ci_high, row.p)}
	{@const unmeasured = fmtUnmeasured(row)}
	<div class="pv-row" data-measured={isMeasured(row)}>
		<span class="pv-label" title={sub ? `${label} · ${sub}` : label}>
			{label}
			{#if sub}<span class="pv-sub">{sub}</span>{/if}
		</span>
		<span class="pv-ci">
			{#if bar}
				<span class="pv-track" role="img" aria-label={`95% interval ${fmtProportion(row.ci_low)} to ${fmtProportion(row.ci_high)}`}>
					<span class="pv-whisker" style={`left:${bar.left}%;width:${bar.width}%`}></span>
					<span class="pv-cap" style={`left:${bar.left}%`}></span>
					<span class="pv-cap" style={`left:${bar.left + bar.width}%`}></span>
					{#if bar.point !== null}<span class="pv-point" style={`left:${bar.point}%`}></span>{/if}
				</span>
			{:else}
				<span class="pv-track pv-track-empty" aria-hidden="true"></span>
			{/if}
		</span>
		<span class="pv-value" title={fmtMethod(row.method)}>
			<span class={isMeasured(row) ? '' : 'text-muted-foreground'}>{fmtPrevalence(row)}</span>
			{#if row.correctness}<span class="pv-sub" title="Share of variants whose slot tests all pass">tests pass {fmtProportion(row.correctness.p)}</span>{/if}
		</span>
		<span class="pv-n">
			{fmtN(row)}
			{#if unmeasured}<span class="pv-sub">{unmeasured}</span>{/if}
		</span>
		<span class="pv-flags">
			{#each prevalenceFlags(row) as flag (flag.code)}<Badge variant="secondary" title={flag.title}>{flag.label}</Badge>{/each}
			<span class="pv-audit">{fmtAudit(row)}</span>
		</span>
	</div>
{/snippet}

<Card.Root class="gap-0 overflow-clip py-0">
	<div class="pv-toolbar">
		<div class="pv-search">
			<Search class="pv-search-icon" aria-hidden="true" />
			<input type="search" placeholder="Filter features…" aria-label="Filter features" bind:value={query} />
		</div>
		<label class="flex items-center gap-1.5 text-xs" title="Hide features that no variant measured (they are listed on one line instead)">
			<Checkbox bind:checked={measuredOnly} />Measured only
		</label>
		<label class="flex items-center gap-1.5 text-xs" title="Among variants whose slot tests all pass">
			<Checkbox bind:checked={showConditional} />| tests pass
		</label>
		{#if hasPooled}
			<Button variant="ghost" size="sm" class="ml-auto h-7 text-xs" onclick={() => expandAll(expanded.size === 0)}>
				{expanded.size === 0 ? 'Expand every target' : 'Collapse all'}
			</Button>
		{/if}
	</div>
	<p class="pv-legend">
		{rowCount} rows in {blocks.length} features{hasPooled ? ' · pooled rows first, per-target rows on demand' : ''} · Wilson 95% intervals; measured n excludes detector errors, n/a and missing outputs (ITT n counts every planned variant)
	</p>
	{#if loading}
		<LoadingState label="loading prevalence" />
	{:else if error}
		<div class="p-4"><ErrorAlert message={error} /></div>
	{:else if rows.length === 0}
		<EmptyState compact title="No prevalence rows" description="The pattern run measured no feature on these targets." />
	{:else}
		{#if shownBlocks.length === 0}
			<p class="px-4 py-6 text-center text-sm text-muted-foreground">No measured feature matches.</p>
		{/if}
		<div>
			{#each shownBlocks as b (b.key)}
				{@const open = expanded.has(b.key)}
				<section class="pv-block">
					<header class="pv-head">
						<span class="pv-feature">{b.head.feature}</span>
						<span class="pv-meta">
							{b.head.scope}{b.head.polarity ? ` · ${b.head.polarity}` : ''}{b.head.conditional_on ? ' · conditional on tests passing' : ''}{#if b.head.detector}<span class="font-mono">{` · ${b.head.detector}`}</span>{/if}
						</span>
						{#if !b.measured}<span class="pv-nm">not measured</span>{/if}
						<span class="ml-auto flex items-center gap-1">
							{#if onaudit && !b.head.conditional_on}
								<Button variant="ghost" size="sm" class="h-6 text-xs" onclick={() => onaudit(b.head.feature)}>Audit</Button>
							{/if}
							{#if b.perTarget.length}
								<button type="button" class="pv-toggle" aria-expanded={open} onclick={() => toggle(b.key)}>
									{open ? 'Hide' : 'Show'} {b.perTarget.length} per target
									<ChevronDown class="size-3.5 transition-transform {open ? 'rotate-180' : ''}" aria-hidden="true" />
								</button>
							{/if}
						</span>
					</header>
					<div class="pv-rows">
						{#each b.lead as row, i (i)}
							{@render prevRow(row, conditionName(row), isPooled(row) ? 'all targets (pooled)' : targetName(row.target))}
						{/each}
					</div>
					{#if open}
						<div class="pv-rows pv-rows-target">
							{#each b.perTarget as row, i (i)}
								{@render prevRow(row, conditionName(row), targetName(row.target))}
							{/each}
						</div>
					{/if}
				</section>
			{/each}
		</div>
		{#if absentBlocks.length}
			<section class="pv-absent" aria-labelledby="pv-absent-h">
				<h3 id="pv-absent-h" class="pv-absent-h">
					Absent from every measured variant <span class="font-mono text-muted-foreground">{absentBlocks.length}</span>
					<span class="pv-meta">— measured, 0 hits; the bracket is the upper end of the Wilson 95% interval</span>
				</h3>
				<ul class="pv-absent-list">
					{#each absentBlocks as b (b.key)}
						{@const open = absentOpen.has(b.key)}
						<li class:pv-absent-open={open}>
							<button type="button" class="pv-absent-row" aria-expanded={open} onclick={() => toggleAbsent(b.key)}>
								<span class="pv-feature">{b.head.feature}</span>
								<span class="pv-meta">{b.head.scope}{b.head.polarity ? ` · ${b.head.polarity}` : ''}</span>
								<span class="pv-absent-sum">{absentSummary(b)}</span>
								<ChevronDown class="ml-auto size-3.5 shrink-0 transition-transform {open ? 'rotate-180' : ''}" aria-hidden="true" />
							</button>
							{#if open}
								<div class="pv-rows pv-rows-target">
									{#each [...b.lead, ...b.perTarget] as row, i (i)}
										{@render prevRow(row, conditionName(row), isPooled(row) ? 'all targets (pooled)' : targetName(row.target))}
									{/each}
									{#if onaudit && !b.head.conditional_on}
										<Button variant="ghost" size="sm" class="h-6 text-xs" onclick={() => onaudit(b.head.feature)}>Audit {b.head.feature}</Button>
									{/if}
								</div>
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/if}
		{#if measuredOnly && unmeasuredBlocks.length}
			<p class="pv-unmeasured">
				<span class="font-medium text-foreground">{unmeasuredBlocks.length} feature{unmeasuredBlocks.length === 1 ? '' : 's'} not measured</span>
				on any variant (hidden):
				{#each unmeasuredBlocks as b, i (b.key)}<span class="font-mono">{b.head.feature}</span>{i < unmeasuredBlocks.length - 1 ? ', ' : ''}{/each}.
				<button type="button" class="text-primary hover:underline" onclick={() => (measuredOnly = false)}>Show them</button>
			</p>
		{/if}
	{/if}
</Card.Root>

<style>
	.pv-toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1rem;
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid var(--border);
	}
	.pv-search {
		position: relative;
		flex: 0 1 16rem;
	}
	.pv-search :global(.pv-search-icon) {
		position: absolute;
		left: 0.5rem;
		top: 50%;
		width: 0.875rem;
		height: 0.875rem;
		transform: translateY(-50%);
		color: var(--muted-foreground);
	}
	.pv-search input {
		height: 1.875rem;
		width: 100%;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--input);
		background: var(--surface-1, var(--card));
		padding: 0 0.5rem 0 1.75rem;
		font-size: 0.8125rem;
	}
	.pv-legend {
		padding: 0.375rem 0.875rem;
		border-bottom: 1px solid var(--border);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.pv-block {
		border-bottom: 1px solid var(--border);
	}
	.pv-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 0.625rem;
		padding: 0.5rem 0.875rem 0.25rem;
	}
	.pv-feature {
		font-family: var(--font-mono);
		font-size: 0.8125rem;
		font-weight: 600;
	}
	.pv-meta {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.pv-nm {
		border-radius: 9999px;
		background: var(--muted);
		padding: 0 0.375rem;
		font-size: 0.625rem;
		line-height: 1rem;
		color: var(--muted-foreground);
	}
	.pv-toggle {
		display: inline-flex;
		height: 1.5rem;
		cursor: pointer;
		align-items: center;
		gap: 0.25rem;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--border);
		padding-inline: 0.5rem;
		font-size: 0.6875rem;
	}
	.pv-toggle[aria-expanded='true'] {
		background: var(--muted);
	}
	.pv-toggle:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.pv-rows {
		padding: 0 0.875rem 0.5rem;
	}
	.pv-rows-target {
		margin: 0 0.875rem 0.625rem;
		padding: 0.25rem 0.625rem;
		border-left: 2px solid var(--border);
		background: color-mix(in oklab, var(--muted) 30%, transparent);
	}
	.pv-row {
		display: grid;
		grid-template-columns: minmax(11rem, 1.3fr) minmax(8rem, 1.3fr) 9rem minmax(9rem, 1fr) minmax(7rem, 1fr);
		align-items: center;
		gap: 0.75rem;
		padding: 0.25rem 0;
		font-size: 0.75rem;
	}
	.pv-row[data-measured='false'] .pv-label {
		color: var(--muted-foreground);
	}
	.pv-label {
		display: flex;
		min-width: 0;
		flex-direction: column;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}
	.pv-label .pv-sub {
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.pv-track {
		position: relative;
		display: block;
		height: 0.75rem;
		border-radius: 0.125rem;
		background: var(--muted);
	}
	.pv-track-empty {
		background: repeating-linear-gradient(90deg, var(--muted) 0 4px, transparent 4px 8px);
		opacity: 0.6;
	}
	.pv-whisker {
		position: absolute;
		top: 50%;
		height: 2px;
		transform: translateY(-50%);
		background: color-mix(in oklab, var(--foreground) 70%, transparent);
	}
	.pv-cap {
		position: absolute;
		top: 0;
		height: 0.75rem;
		width: 1px;
		background: color-mix(in oklab, var(--foreground) 70%, transparent);
	}
	.pv-point {
		position: absolute;
		top: 50%;
		width: 0.5rem;
		height: 0.5rem;
		transform: translate(-50%, -50%);
		border-radius: 9999px;
		background: var(--chart-1);
	}
	.pv-value,
	.pv-n {
		display: flex;
		flex-direction: column;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
	.pv-value {
		text-align: right;
	}
	.pv-sub {
		font-family: var(--font-sans);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.pv-flags {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem;
	}
	.pv-audit {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.pv-absent {
		border-top: 1px solid var(--border);
	}
	.pv-absent-h {
		padding: 0.625rem 0.875rem 0.25rem;
		font-size: 0.8125rem;
		font-weight: 600;
	}
	.pv-absent-list {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		padding-bottom: 0.5rem;
	}
	.pv-absent-list > li.pv-absent-open {
		grid-column: 1 / -1;
	}
	@media (max-width: 900px) {
		.pv-absent-list {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	.pv-absent-row {
		display: flex;
		width: 100%;
		min-width: 0;
		cursor: pointer;
		align-items: baseline;
		gap: 0.625rem;
		padding: 0.25rem 0.875rem;
		text-align: left;
		font-size: 0.75rem;
	}
	.pv-absent-row:hover {
		background: var(--surface-2, var(--muted));
	}
	.pv-absent-row:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.pv-absent-row .pv-feature {
		font-weight: 500;
		font-size: 0.75rem;
	}
	.pv-absent-sum {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.pv-unmeasured {
		padding: 0.625rem 0.875rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	@media (max-width: 900px) {
		.pv-row {
			grid-template-columns: minmax(0, 1fr) 8rem;
		}
		.pv-label,
		.pv-flags {
			grid-column: 1 / -1;
		}
	}
</style>
