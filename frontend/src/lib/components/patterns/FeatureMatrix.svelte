<script lang="ts">
	/**
	 * FeatureMatrix — variants × features of one target (spec 10.3 §4): one row per variant (condition, repeat,
	 * outcome / parse status, cluster) and one cell per feature column — present ●, absent ○, detector error ✕,
	 * n/a –, not run (blank). Rows are virtualised (a fixed row height and a scroll window), so hundreds of
	 * variants stay smooth. Errors are never shown as absent.
	 *
	 * Props: `studyId`, `targetId`, `features` (`{key, feature, scope}` columns, e.g. the run summary's),
	 * `conditionLabels`.
	 */
	import * as Card from '$lib/components/ui/card';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { cn } from '$lib/utils';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listSlotVariants } from '$lib/api/v3/slots';
	import type { SlotVariantRow } from '$lib/api/v3/types';
	import { shortSha } from '$lib/components/runs/format';
	import { FEATURE_STATUS_TEXT } from './format';

	interface Props {
		studyId: string;
		targetId: string;
		features: { key: string; feature: string; scope: string }[];
		conditionLabels?: Record<string, string>;
	}

	let { studyId, targetId, features, conditionLabels = {} }: Props = $props();

	const ROW_H = 26;
	const VIEW_H = 420;
	const OVERSCAN = 8;
	const MAX_ROWS = 5000;

	let rows = $state<SlotVariantRow[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');
	let scrollTop = $state(0);

	$effect(() => {
		const controller = new AbortController();
		loading = true;
		error = '';
		rows = [];
		(async () => {
			const all: SlotVariantRow[] = [];
			for (let page = 1; page < 100; page++) {
				const out = await listSlotVariants(targetId, { study: studyId, page, page_size: 200 }, { signal: controller.signal });
				all.push(...out.items);
				total = out.total;
				if (all.length >= out.total || out.items.length === 0 || all.length >= MAX_ROWS) break;
			}
			rows = all;
		})()
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the variants.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	// Feature columns: the run's features plus any key the variants carry that the list missed.
	const columns = $derived.by(() => {
		const keys = new Map<string, string>(features.map((f) => [`${f.feature}:${f.scope}`, f.feature]));
		for (const row of rows) for (const key of Object.keys(row.features)) if (!keys.has(key)) keys.set(key, key.split(':')[0]);
		return [...keys.entries()].map(([key, label]) => ({ key, label, scope: key.split(':').pop() ?? '' }));
	});

	const start = $derived(Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN));
	const end = $derived(Math.min(rows.length, Math.ceil((scrollTop + VIEW_H) / ROW_H) + OVERSCAN));
	const windowRows = $derived(rows.slice(start, end));

	const GLYPH: Record<string, string> = { present: '●', absent: '○', error: '✕', not_applicable: '–' };
	const presentCounts = $derived(
		new Map(columns.map((c) => [c.key, rows.filter((r) => r.features[c.key] === 'present').length])),
	);
	const gridTemplate = $derived(`7rem 9rem 4rem 7rem 4rem ${columns.map(() => '2.25rem').join(' ')}`);
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<div class="table-toolbar">
		<span class="text-xs text-muted-foreground">
			{rows.length} of {total} variants · ● present · ○ absent · ✕ detector error · – n/a · blank: not measured
		</span>
	</div>
	{#if loading}
		<LoadingState label="loading variants" />
	{:else if error}
		<div class="p-4"><ErrorAlert message={error} /></div>
	{:else if rows.length === 0}
		<EmptyState compact title="No variants yet" description="Variants appear as the slot study runs." />
	{:else}
		<div class="overflow-x-auto">
			<div class="min-w-max text-xs" role="table" aria-label="Variants by feature" aria-rowcount={rows.length + 1}>
				<div class="grid border-b border-border bg-muted/40 font-mono text-[11px] text-muted-foreground" style={`grid-template-columns:${gridTemplate}`} role="row">
					<span class="px-2 py-1" role="columnheader">variant</span>
					<span class="px-2 py-1" role="columnheader">condition</span>
					<span class="px-2 py-1" role="columnheader">repeat</span>
					<span class="px-2 py-1" role="columnheader">parse / outcome</span>
					<span class="px-2 py-1" role="columnheader">cluster</span>
					{#each columns as col (col.key)}
						<span class="relative h-24 px-1" role="columnheader" title={`${col.key} · present in ${presentCounts.get(col.key)} of ${rows.length}`}>
							<span class="absolute bottom-1 left-1/2 origin-bottom-left -rotate-60 whitespace-nowrap">{col.label}</span>
						</span>
					{/each}
				</div>
				<div class="relative overflow-y-auto" style={`height:${Math.min(VIEW_H, rows.length * ROW_H)}px`} onscroll={(e) => (scrollTop = (e.currentTarget as HTMLElement).scrollTop)}>
					<div style={`height:${rows.length * ROW_H}px;position:relative`}>
						{#each windowRows as row, i (row.run_id)}
							<div
								class="absolute inset-x-0 grid items-center border-b border-border/50 hover:bg-accent/40"
								style={`top:${(start + i) * ROW_H}px;height:${ROW_H}px;grid-template-columns:${gridTemplate}`}
								role="row"
								aria-rowindex={start + i + 2}
							>
								<a class="truncate px-2 font-mono text-primary hover:underline" href={`/samples/${row.run_id}`} role="cell">{shortSha(row.run_id, 8)}</a>
								<span class="truncate px-2" role="cell">{row.condition !== null ? (conditionLabels[String(row.condition)] ?? `#${row.condition}`) : '—'}</span>
								<span class="px-2 tabular-nums" role="cell">{row.repeat_index ?? '—'}</span>
								<span class="truncate px-2 font-mono" role="cell" title={row.outcome_reason}>{row.parse_status ?? row.outcome ?? '—'}</span>
								<span class="px-2 font-mono" role="cell">{row.cluster_id ?? '—'}</span>
								{#each columns as col (col.key)}
									{@const status = row.features[col.key]}
									<span
										class={cn('text-center', status === 'error' && 'text-danger-fg', status === 'present' && 'text-[color:var(--chart-1)]')}
										role="cell"
										title={`${col.key}: ${status ? (FEATURE_STATUS_TEXT[status] ?? status) : 'not measured'}`}
									>
										{status ? (GLYPH[status] ?? '?') : ''}
									</span>
								{/each}
							</div>
						{/each}
					</div>
				</div>
			</div>
		</div>
	{/if}
</Card.Root>
