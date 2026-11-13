<script lang="ts">
	/**
	 * ModelVariabilityPanel — the model's run-to-run variability
	 * (`GET /generation/models/{id}/variability/`): spread within each group of
	 * the caller's completed runs that share one setup (one configuration
	 * fingerprint, i.e. one condition on one task), never across setups.
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
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getModelVariability, type ModelVariability, type ModelVariabilityGroup } from '$lib/api/variability';
	import { DASH, formatRatio, formatShare } from '$lib/components/studies/variability/variability';
	import { kindLabel } from '$lib/components/runs/format';
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

	/** The group's setup beyond its task: run kind, stack, harness and the studies its runs belong to. */
	function setupLine(g: ModelVariabilityGroup): string {
		const parts = [kindLabel(g.kind)];
		if (g.stack) parts.push(g.stack);
		if (g.harness) parts.push(`${g.harness[0]} ${g.harness[1]}`);
		if (g.studies.length) parts.push(g.studies.join(', '));
		return parts.join(' · ');
	}
	// Groups whose runs disagree on pass/fail first (the interesting ones), then the largest groups.
	const PREVIEW = 8;
	let showAll = $state(false);
	const sortedGroups = $derived(
		[...(data?.groups ?? [])].sort((a, b) => Number(a.pass.agree !== false) - Number(b.pass.agree !== false) || b.n - a.n),
	);
	const shownGroups = $derived(showAll ? sortedGroups : sortedGroups.slice(0, PREVIEW));
	const disagreeing = $derived(sortedGroups.filter((g) => g.pass.agree === false).length);

	/** Headline CV: cost when billed, else tokens (local endpoints bill $0, so cost has no spread). */
	const headlineCv = $derived.by(() => {
		for (const key of ['cost', 'total_tokens', 'wall_seconds']) {
			const v = overall?.metrics[key]?.median_cv;
			if (v != null) return { label: CV_COLUMNS.find((c) => c.key === key)?.label.toLowerCase() ?? key, value: v };
		}
		return { label: 'cost', value: null };
	});

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
				description={data.n_completed_runs
					? `${data.n_completed_runs} completed run${data.n_completed_runs === 1 ? '' : 's'}, each with its own setup. Plan 2 or more repeats in an experiment to measure run-to-run variation.`
					: 'Generate apps with this model, with 2 or more repeats, to measure run-to-run variation.'}
			/>
		{:else if data && overall}
			<dl class="md-figs">
				<div class="md-fig">
					<dt class="md-label">Same-setup groups</dt>
					<dd class="md-val">{data.n_groups}</dd>
					<dd class="md-sub">{data.n_runs} runs of {data.n_completed_runs} completed</dd>
				</div>
				<div class="md-fig">
					<dt class="md-label">Pass agreement</dt>
					<dd class="md-val">{overall.pass_agreement.groups ? `${overall.pass_agreement.agree}/${overall.pass_agreement.groups}` : DASH}</dd>
					<dd class="md-sub">groups whose runs all pass or all fail</dd>
				</div>
				<div class="md-fig">
					<dt class="md-label">Median CV · {headlineCv.label}</dt>
					<dd class="md-val">{formatRatio(headlineCv.value)}</dd>
					<dd class="md-sub" title={cvSummary}>{cvSummary}</dd>
				</div>
				<div class="md-fig">
					<dt class="md-label">Code similarity</dt>
					<dd class="md-val">{formatRatio(overall.median_shingle_jaccard)}</dd>
					<dd class="md-sub">repeats' shingle Jaccard · replays reproduced {overall.replays.pairs ? `${overall.replays.reproduced}/${overall.replays.pairs}` : DASH}</dd>
				</div>
			</dl>
			<p class="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
				{sortedGroups.length} groups{disagreeing ? `, ${disagreeing} whose runs disagree on pass/fail listed first` : ''}{sortedGroups.length > PREVIEW && !showAll ? ` · showing ${PREVIEW}` : ''}.
			</p>
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
						{#each shownGroups as g (`${g.key}:${g.value}`)}
							<tr>
								<td class="col-sticky max-w-72 bg-card">
									<span class="block text-sm font-medium" title={g.unit_key && g.unit_key !== g.task ? g.unit_key : undefined}>{g.task ?? 'no task'}</span>
									<span class="block truncate font-mono text-[11px] text-muted-foreground" title={`${setupLine(g)} · ${g.value}`}
										>{setupLine(g)}</span
									>
								</td>
								<td class="num text-xs">{g.n}</td>
								<td class="num text-xs">
									{g.pass.n ? `${g.pass.passed}/${g.pass.n}` : DASH}
									{#if g.pass.agree === false}<span class="md-chip mt-0.5 text-warning-fg">disagree</span>{/if}
								</td>
								{#each CV_COLUMNS as col (col.key)}
									<td class="num text-xs">{formatRatio(g.stats[col.key]?.cv)}</td>
								{/each}
								<td class="num text-xs">{formatRatio(g.similarity.median_shingle_jaccard)}</td>
								<td class="text-xs">
									{#if g.replays.length === 0}
										<span class="text-muted-foreground">{DASH}</span>
									{:else}
										{#each g.replays as r (`${r.run_a}:${r.run_b}`)}
											<span class="block">{r.first_divergent_call == null ? 'reproduced' : `differs at call ${r.first_divergent_call}`}</span>
										{/each}
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if sortedGroups.length > PREVIEW}
				<button type="button" class="md-more" aria-expanded={showAll} onclick={() => (showAll = !showAll)}>
					{showAll ? 'Show fewer' : `Show all ${sortedGroups.length} groups`}
				</button>
			{/if}
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
