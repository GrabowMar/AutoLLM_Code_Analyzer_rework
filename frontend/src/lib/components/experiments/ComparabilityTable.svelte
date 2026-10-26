<script lang="ts">
	/**
	 * ComparabilityTable — what each condition will actually run with (harness,
	 * sampling, forced params, output cap, endpoint …), with the columns where
	 * conditions differ highlighted and every problem listed (blocking ones
	 * marked). Extracted from the old experiment page.
	 *
	 * Props:
	 * - `comparability`: the preview's `comparability`.
	 * - `compact`: smaller header and no explanatory lines (inside a Sheet).
	 * - `highlightDiffers` (default true): tint columns listed in `differs_on`.
	 * - `experimentStack`: the experiment-wide stack (hides a redundant stack column).
	 * - `rowLabel(row)`: override the condition label.
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import type { ExperimentComparability, ExperimentComparabilityRow } from '$lib/api/experiments';

	interface Props {
		comparability: ExperimentComparability;
		compact?: boolean;
		highlightDiffers?: boolean;
		experimentStack?: string | null;
		rowLabel?: (row: ExperimentComparabilityRow) => string;
	}

	let { comparability, compact = false, highlightDiffers = true, experimentStack = null, rowLabel }: Props = $props();

	// Older backends sent fewer keys (and `stack` for the stack); every key is optional here.
	type CompRow = Pick<ExperimentComparabilityRow, 'condition_id' | 'model' | 'problems'> &
		Partial<Omit<ExperimentComparabilityRow, 'condition_id' | 'model' | 'problems'>> & { stack?: string | null };

	interface CompColumn {
		id: string;
		label: string;
		keys: string[];
		cell: (row: CompRow) => string;
	}

	function formatParams(params: unknown): string {
		if (!params || typeof params !== 'object') return params == null ? '—' : String(params);
		const entries = Object.entries(params as Record<string, unknown>);
		return entries.length
			? entries.map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ')
			: '—';
	}

	const rows = $derived(comparability.conditions as CompRow[]);
	const differsOn = $derived(new Set(comparability.differs_on));

	const columns = $derived.by((): CompColumn[] => {
		const has = (key: keyof CompRow) => rows.some((r) => r[key] !== undefined && r[key] !== null);
		const list: CompColumn[] = [];
		if (has('endpoint')) {
			list.push({
				id: 'endpoint',
				label: 'Endpoint',
				keys: ['endpoint', 'max_concurrency'],
				cell: (r) => (r.endpoint ? r.endpoint.slug : 'openrouter'),
			});
		}
		list.push(
			{
				id: 'harness',
				label: 'Harness',
				keys: ['harness', 'harness_version', 'harness_options'],
				cell: (r) => {
					if (!r.harness) return '—';
					const options = Object.keys(r.harness_options ?? {}).length ? ` ${formatParams(r.harness_options)}` : '';
					return `${r.harness}@${r.harness_version ?? '?'}${options}`;
				},
			},
			{
				id: 'sampling',
				label: 'Sampling',
				keys: ['sampling_policy'],
				cell: (r) => (r.sampling_policy ?? '—').replace(/_/g, ' '),
			},
			{ id: 'forced', label: 'Forced params', keys: ['forced_params'], cell: (r) => formatParams(r.forced_params) }
		);
		if (has('output_cap')) {
			list.push({
				id: 'cap',
				label: 'Output cap',
				keys: ['output_cap'],
				cell: (r) => (r.output_cap != null ? r.output_cap.toLocaleString() : '—'),
			});
		}
		if (has('context_window') && (differsOn.has('context_window') || rows.some((r) => r.endpoint))) {
			list.push({
				id: 'ctx',
				label: 'Context',
				keys: ['context_window'],
				cell: (r) => (r.context_window ? r.context_window.toLocaleString() : '—'),
			});
		}
		// The stack is experiment-wide; its column only matters for legacy (per-profile) stacks.
		if ((has('stack_slug') || has('stack')) && (!experimentStack || differsOn.has('stack_slug'))) {
			list.push({ id: 'stack', label: 'Stack', keys: ['stack', 'stack_slug'], cell: (r) => r.stack_slug ?? r.stack ?? '—' });
		}
		if (has('profile')) {
			list.push({ id: 'profile', label: 'Profile', keys: ['profile'], cell: (r) => r.profile ?? '—' });
		}
		const routed = (r: CompRow) =>
			r.provider_routing != null && r.provider_routing !== 'default' && r.provider_routing !== 'not_applicable';
		if (rows.some(routed) || differsOn.has('provider_routing')) {
			list.push({
				id: 'routing',
				label: 'Provider routing',
				keys: ['provider_routing'],
				cell: (r) => (routed(r) ? formatParams(r.provider_routing) : String(r.provider_routing ?? 'default').replace(/_/g, ' ')),
			});
		}
		if (rows.some((r) => Object.keys(r.param_overrides ?? {}).length > 0)) {
			list.push({ id: 'overrides', label: 'Overrides', keys: ['param_overrides'], cell: (r) => formatParams(r.param_overrides) });
		}
		list.push({
			id: 'seed',
			label: 'Seed',
			keys: ['seed_forwarded'],
			cell: (r) => (r.seed_forwarded ? 'forwarded' : 'not supported'),
		});
		return list;
	});

	const NUMERIC = new Set(['cap', 'ctx']);

	function differs(column: CompColumn): boolean {
		return highlightDiffers && column.keys.some((k) => differsOn.has(k));
	}

	function label(row: CompRow): string {
		if (rowLabel) return rowLabel(row as ExperimentComparabilityRow);
		const preset = row.harness_preset?.name ?? '';
		return row.label || (preset ? `${row.model_name ?? row.model} × ${preset}` : (row.model_name ?? row.model));
	}
</script>

<div class="space-y-2">
	{#if !compact}
		{#if comparability.has_problems}
			<div class="rounded-md border px-3 py-2 text-xs {toneClasses.warning}" role="status">
				Some conditions will not run exactly as configured. Fix these before launching, or report them with the results.
			</div>
		{/if}
		{#if comparability.differs_on.length}
			<p class="text-xs text-muted-foreground">
				Conditions differ on: <span class="font-mono text-foreground">{comparability.differs_on.join(', ')}</span>
			</p>
		{:else}
			<p class="text-xs text-muted-foreground">Conditions differ only in model and harness preset / profile.</p>
		{/if}
	{/if}
	<!-- Plain scroll frame (no fit attachment): this table also renders inside a Sheet. -->
	<div class="table-frame rounded-md border border-border bg-card">
		<table class="min-w-[40rem]">
			<thead>
				<tr>
					<th scope="col" class="col-sticky">Condition</th>
					{#each columns as column (column.id)}
						<th scope="col" class={cn('whitespace-nowrap', NUMERIC.has(column.id) && 'num', differs(column) && 'text-warning-fg')} title={differs(column) ? 'Conditions differ on this column' : undefined}>
							{column.label}{#if differs(column)}{' '}<span class="normal-case tracking-normal">· differs</span>{/if}
						</th>
					{/each}
					<th scope="col">Problems</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.condition_id)}
					<tr class="group">
						<th scope="row" class="col-sticky top-auto z-[1] min-w-44 max-w-56 border-b-border/60 bg-card group-hover:bg-[color-mix(in_oklab,var(--primary)_5%,var(--card))] text-xs font-medium normal-case tracking-normal text-foreground backdrop-blur-none">
							<span class="block truncate" title={label(row)}>{label(row)}</span>
						</th>
						{#each columns as column (column.id)}
							<td class={cn('font-mono text-xs', NUMERIC.has(column.id) && 'num', differs(column) && 'bg-warning/5')}>
								{#if column.cell(row) === '—'}<span class="text-muted-foreground">—</span>{:else}{column.cell(row)}{/if}
							</td>
						{/each}
						<td class="min-w-[12rem] text-xs">
							{#if row.problems.length === 0}
								<span class="text-muted-foreground">—</span>
							{:else}
								<ul class="space-y-0.5">
									{#each row.problems as problem, i (i)}
										{@const blocking = row.errors?.includes(problem)}
										<li class={blocking ? 'text-danger-fg' : 'text-warning-fg'}>
											{#if blocking}<span class="font-medium">Blocks launch:</span>{' '}{/if}{problem}
										</li>
									{/each}
								</ul>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>
