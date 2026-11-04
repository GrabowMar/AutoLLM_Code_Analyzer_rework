<script lang="ts">
	/**
	 * PrevalenceTable — feature × condition prevalence of a PatternRun (spec 7.9, 10.3 §4): p with a Wilson CI
	 * bar (repeated-looks adjusted for `adaptive_n` cells), measured n beside the ITT n, what the measured n
	 * leaves out (detector errors, n/a, no output), flags (`adaptive_n`, `low_effective_n`, bounds,
	 * provisional), the audit's precision / NPV, and correctness for security features. Prevalence is
	 * presence/absence: no severity is computed or shown.
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
	import { tableFrame, TableToolbar } from '$lib/components/table';
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
	let showPerTarget = $state(true);

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

	const hasPooled = $derived(rows.some((r) => r.pooled));
	const shown = $derived(
		rows.filter((r) => (showConditional || !r.conditional_on) && (showPerTarget || r.pooled || !hasPooled)),
	);
	const groups = $derived(groupBy(shown, (r) => `${r.feature}:${r.scope}${r.conditional_on ? '|cond' : ''}`));

	function conditionName(row: PrevalenceRow): string {
		return row.condition_label || conditionLabels[String(row.condition)] || `condition ${row.condition}`;
	}

	function targetName(id: string): string {
		return id === 'all' ? 'all targets (pooled)' : (targetLabels[id] ?? id.slice(0, 8));
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<TableToolbar>
		{#snippet summary()}
			<span>{shown.length} rows · Wilson 95% intervals; measured n excludes detector errors, n/a and missing outputs (ITT n counts every planned variant)</span>
		{/snippet}
		{#snippet controls()}
			<label class="flex items-center gap-1.5 text-xs" title="Among variants whose slot tests all pass">
				<Checkbox bind:checked={showConditional} />| tests pass
			</label>
			{#if hasPooled}
				<label class="flex items-center gap-1.5 text-xs">
					<Checkbox bind:checked={showPerTarget} />Per target
				</label>
			{/if}
		{/snippet}
	</TableToolbar>
	{#if loading}
		<LoadingState label="loading prevalence" />
	{:else if error}
		<div class="p-4"><ErrorAlert message={error} /></div>
	{:else if rows.length === 0}
		<EmptyState compact title="No prevalence rows" description="The pattern run measured no feature on these targets." />
	{:else}
		<div class="table-frame" {@attach tableFrame}>
			<table>
				<thead>
					<tr>
						<th>Condition</th>
						<th>Target</th>
						<th class="num">Prevalence</th>
						<th class="w-40"><span class="sr-only">95% CI</span></th>
						<th class="num">n</th>
						<th>Not measured</th>
						<th>Flags</th>
						<th>Audit</th>
					</tr>
				</thead>
				<tbody>
					{#each groups as [key, list] (key)}
						{@const head = list[0]}
						<tr class="bg-muted/40">
							<td colspan="8" class="text-xs">
								<span class="font-mono font-medium">{head.feature}</span>
								<span class="text-muted-foreground">· {head.scope}{head.polarity ? ` · ${head.polarity}` : ''}{head.conditional_on ? ' · conditional on tests passing' : ''}</span>
								{#if head.detector}<span class="ml-1 font-mono text-muted-foreground">{head.detector}</span>{/if}
								{#if onaudit && !head.conditional_on}
									<Button variant="ghost" size="sm" class="ml-2 h-6" onclick={() => onaudit(head.feature)}>Audit</Button>
								{/if}
							</td>
						</tr>
						{#each list as row, i (i)}
							{@const bar = ciBar(row.ci_low, row.ci_high, row.p)}
							<tr>
								<td class="text-xs">{conditionName(row)}</td>
								<td class="text-xs">{targetName(row.target)}</td>
								<td class="num" title={fmtMethod(row.method)}>
									{fmtPrevalence(row)}
									{#if row.correctness}<div class="cell-sub" title="Share of variants whose slot tests all pass">tests pass {fmtProportion(row.correctness.p)}</div>{/if}
								</td>
								<td>
									{#if bar}
										<div class="relative h-3 w-36 rounded-sm bg-muted" role="img" aria-label={`95% interval ${fmtProportion(row.ci_low)} to ${fmtProportion(row.ci_high)}`}>
											<div class="absolute top-1/2 h-0.5 -translate-y-1/2 bg-foreground/70" style={`left:${bar.left}%;width:${bar.width}%`}></div>
											<div class="absolute top-0 h-3 w-px bg-foreground/70" style={`left:${bar.left}%`}></div>
											<div class="absolute top-0 h-3 w-px bg-foreground/70" style={`left:${bar.left + bar.width}%`}></div>
											{#if bar.point !== null}
												<div class="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[color:var(--chart-1)]" style={`left:${bar.point}%`}></div>
											{/if}
										</div>
									{:else}
										<span class="text-xs text-muted-foreground">—</span>
									{/if}
								</td>
								<td class="num text-xs">{fmtN(row)}</td>
								<td class="text-xs text-muted-foreground">{fmtUnmeasured(row) || '—'}</td>
								<td>
									<div class="flex flex-wrap gap-1">
										{#each prevalenceFlags(row) as flag (flag.code)}
											<Badge variant="secondary" title={flag.title}>{flag.label}</Badge>
										{/each}
									</div>
								</td>
								<td class="text-xs">{fmtAudit(row)}</td>
							</tr>
						{/each}
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</Card.Root>
