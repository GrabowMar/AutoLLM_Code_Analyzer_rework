<script lang="ts">
	/**
	 * ModelVariabilityPanel — the model's run-to-run variability
	 * (`GET /generation/models/{id}/variability/`): spread within each group of
	 * the caller's completed runs that share one setup (a configuration
	 * fingerprint, else an approximate legacy key), never across setups.
	 * KPI tiles (groups and runs, pass agreement, median within-group CV,
	 * median code similarity between repeats, seeded replays reproduced), one
	 * row per group, then the server's caveats. Distinct from the between-app
	 * spread on the results tab. Self-fetching; descriptive, no p-values.
	 *
	 * Props:
	 * - `modelId`: the model's numeric primary key.
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getModelVariability, type ModelVariability } from '$lib/api/variability';
	import { DASH, formatRatio, formatShare } from '$lib/components/experiments/variability';
	import Repeat from '@lucide/svelte/icons/repeat';

	interface Props {
		modelId: number;
	}

	let { modelId }: Props = $props();

	let data = $state<ModelVariability | null>(null);
	let loading = $state(false);
	let error = $state('');
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		loading = true;
		error = '';
		try {
			const next = await getModelVariability(modelId, { signal: ctrl.signal });
			if (controller === ctrl) data = next;
		} catch (e) {
			if (!isAbortError(e) && controller === ctrl) error = formatApiError(e, 'Could not load run-to-run variability');
		} finally {
			if (controller === ctrl) loading = false;
		}
	}

	$effect(() => {
		void modelId;
		untrack(() => void load());
		return () => controller?.abort();
	});

	const CV_COLUMNS = [
		{ key: 'cost', label: 'Cost' },
		{ key: 'total_tokens', label: 'Tokens' },
		{ key: 'llm_calls', label: 'Calls' },
		{ key: 'wall_seconds', label: 'Wall' },
		{ key: 'source_loc', label: 'LOC' },
	];

	const overall = $derived(data?.overall);
	const cvSummary = $derived(
		['cost', 'total_tokens', 'source_loc']
			.map((k) => `${CV_COLUMNS.find((c) => c.key === k)?.label} ${formatRatio(overall?.metrics[k]?.median_cv)}`)
			.join(' · ')
	);
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<Card.Title class="text-sm font-medium">Run-to-run variability</Card.Title>
		<Card.Description class="text-xs">
			How much runs of the same setup differ (your runs, one configuration at a time) — not the spread between apps. Descriptive.
		</Card.Description>
	</Card.Header>
	<Card.Content class="p-0">
		{#if error}
			<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
		{/if}
		{#if loading && !data}
			<LoadingState label="loading run-to-run variability" class="py-8" />
		{:else if data && data.n_groups === 0}
			<EmptyState
				compact
				icon={Repeat}
				title="No setup has been run twice yet"
				description={data.n_completed_jobs
					? `${data.n_completed_jobs} completed run${data.n_completed_jobs === 1 ? '' : 's'}, each with its own setup. Plan 2 or more repeats in an experiment to measure run-to-run variation.`
					: 'Generate apps with this model, with 2 or more repeats, to measure run-to-run variation.'}
			/>
		{:else if data && overall}
			<div class="grid grid-cols-2 gap-3 p-4 lg:grid-cols-4">
				<div class="kpi-card">
					<span class="kpi-label">Same-setup groups</span>
					<span class="font-mono text-xl font-medium tabular-nums">{data.n_groups}</span>
					<span class="text-[11px] text-muted-foreground">{data.n_runs} runs of {data.n_completed_jobs} completed</span>
				</div>
				<div class="kpi-card">
					<span class="kpi-label">Pass agreement</span>
					<span class="font-mono text-xl font-medium tabular-nums"
						>{overall.pass_agreement.groups ? `${overall.pass_agreement.agree}/${overall.pass_agreement.groups}` : DASH}</span
					>
					<span class="text-[11px] text-muted-foreground">groups whose runs all pass or all fail</span>
				</div>
				<div class="kpi-card">
					<span class="kpi-label">Median within-group CV</span>
					<span class="font-mono text-xl font-medium tabular-nums">{formatRatio(overall.metrics.cost?.median_cv)}</span>
					<span class="truncate text-[11px] text-muted-foreground" title={cvSummary}>{cvSummary}</span>
				</div>
				<div class="kpi-card">
					<span class="kpi-label">Code similarity</span>
					<span class="font-mono text-xl font-medium tabular-nums">{formatRatio(overall.median_shingle_jaccard)}</span>
					<span class="text-[11px] text-muted-foreground"
						>repeats' shingle Jaccard · replays reproduced {overall.replays.pairs ? `${overall.replays.reproduced}/${overall.replays.pairs}` : DASH}</span
					>
				</div>
			</div>
			<div class="table-frame border-t border-border" {@attach tableFrame}>
				<table class="min-w-[52rem]">
					<thead>
						<tr>
							<th scope="col" class="col-sticky">Setup</th>
							<th scope="col" class="num">Runs</th>
							<th scope="col" class="num" title="Runs that passed / graded runs">Passed</th>
							{#each CV_COLUMNS as col (col.key)}
								<th scope="col" class="num" title="Coefficient of variation (SD / mean) within the group">CV {col.label}</th>
							{/each}
							<th scope="col" class="num" title="Median 5-token shingle Jaccard between the group's runs (up to 10 pairs)">Similarity</th>
							<th scope="col" title="Seeded replays: the first call whose response differs">Replays</th>
						</tr>
					</thead>
					<tbody>
						{#each data.groups as g (`${g.key}:${g.value}`)}
							<tr>
								<td class="col-sticky max-w-72 bg-card">
									<span class="flex flex-wrap items-center gap-1.5 text-sm">
										<span class="font-medium">{g.app_slug ?? 'no app'}</span>
										{#if g.approximate}<Badge variant="secondary" title="Legacy runs without a fingerprint">approximate</Badge>{/if}
									</span>
									<span class="block truncate font-mono text-[11px] text-muted-foreground" title={g.experiments.join(', ') || g.value}
										>{g.method}{g.experiments.length ? ` · ${g.experiments.join(', ')}` : ''}</span
									>
								</td>
								<td class="num text-xs">{g.n}</td>
								<td class="num text-xs">
									{g.pass.n ? `${g.pass.passed}/${g.pass.n}` : DASH}
									{#if g.pass.agree === false}<span class="block text-muted-foreground">disagree</span>{/if}
								</td>
								{#each CV_COLUMNS as col (col.key)}
									<td class="num text-xs">{formatRatio(g.stats[col.key]?.cv)}</td>
								{/each}
								<td class="num text-xs">{formatRatio(g.similarity.median_shingle_jaccard)}</td>
								<td class="text-xs">
									{#if g.replays.length === 0}
										<span class="text-muted-foreground">{DASH}</span>
									{:else}
										{#each g.replays as r (`${r.job_a}:${r.job_b}`)}
											<span class="block">{r.first_divergent_call == null ? 'reproduced' : `differs at call ${r.first_divergent_call}`}</span>
										{/each}
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<ul class="space-y-1 border-t border-border/60 px-4 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
				<li>
					Pooled within-group SD of pass rate {overall.metrics.pass_rate?.pooled_sd != null
						? `${formatShare(overall.metrics.pass_rate.pooled_sd)} (${overall.metrics.pass_rate.df} df)`
						: DASH}; CV is shown only for ratio-scale metrics.
				</li>
				{#each data.notes as note (note)}
					<li>{note}</li>
				{/each}
			</ul>
		{/if}
	</Card.Content>
</Card.Root>
