<script lang="ts">
	/**
	 * ResultsTab — the statistics (one server computation, `GET /results/`):
	 * leaderboard with cluster-bootstrap CIs, the condition × app matrix,
	 * paired comparisons against the baseline and the server's notes.
	 * Controls live in the URL (`baseline`, `common`, `invalid`, `truncated`,
	 * `fingerprint`, `transpose`). Refetched on state changes, and on ticks at
	 * most every 30 s while running.
	 *
	 * Props: `experiment`, `refreshSignal` (bumped by the page on `state`),
	 * `tickSignal` (bumped on `tick`).
	 */
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ResultsMatrix from '$lib/components/generator/batches/ResultsMatrix.svelte';
	import { fromResultsMatrix } from '$lib/components/generator/batches/matrix';
	import { getResults, type ExperimentDetail, type ExperimentResults, type ResultsQuery } from '$lib/api/experiments';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import FileText from '@lucide/svelte/icons/file-text';
	import Info from '@lucide/svelte/icons/info';
	import Trophy from '@lucide/svelte/icons/trophy';
	import Leaderboard from '../Leaderboard.svelte';
	import PairedDeltas from '../PairedDeltas.svelte';
	import { isTerminal } from '../format';

	interface Props {
		experiment: ExperimentDetail;
		refreshSignal?: number;
		tickSignal?: number;
	}

	let { experiment, refreshSignal = 0, tickSignal = 0 }: Props = $props();

	const TICK_REFRESH_MS = 30_000;

	const params = $derived(page.url.searchParams);
	const flag = (key: string, fallback: boolean) => {
		const v = params.get(key);
		return v == null ? fallback : v === '1';
	};
	const baselineParam = $derived(params.get('baseline'));
	const query = $derived<ResultsQuery>({
		baseline: baselineParam ? Number(baselineParam) : undefined,
		common_cells: flag('common', true),
		exclude_invalid: flag('invalid', false),
		exclude_truncated: flag('truncated', true),
		fingerprint: params.get('fingerprint') === 'majority' ? 'majority' : 'all',
	});
	const transpose = $derived(flag('transpose', experiment.conditions.length > 15));

	let results = $state.raw<ExperimentResults | null>(null);
	let loading = $state(true);
	let error = $state('');
	let lastFetch = 0;
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		lastFetch = Date.now();
		try {
			const next = await getResults(experiment.id, $state.snapshot(query), { signal: mine.signal });
			if (mine.signal.aborted) return;
			results = next;
			error = '';
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			error = formatApiError(e, 'Could not compute the results.');
		} finally {
			if (!mine.signal.aborted) loading = false;
		}
	}

	// Keyed on the query's value: other URL changes (tab, transpose) do not refetch.
	const queryKey = $derived(JSON.stringify(query));

	$effect(() => {
		void queryKey;
		void refreshSignal;
		untrack(() => void load());
		return () => controller?.abort();
	});

	$effect(() => {
		void tickSignal;
		untrack(() => {
			if (!results || isTerminal(experiment.status)) return;
			if (Date.now() - lastFetch >= TICK_REFRESH_MS) void load();
		});
	});

	function setParam(key: string, value: string | null) {
		const url = new URL(page.url);
		if (value == null) url.searchParams.delete(key);
		else url.searchParams.set(key, value);
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const conditionLabel = $derived(new Map(experiment.conditions.map((c) => [c.id, c.label])));
	const conditionInfo = $derived(
		experiment.conditions.map((c) => ({
			id: c.id,
			label: c.label,
			model: c.model_name ?? `model #${c.model}`,
			variant: c.harness_preset_ref ? `${c.harness_preset_ref.name} v${c.harness_preset_ref.version}` : c.harness || '',
		}))
	);
	const matrix = $derived(results ? fromResultsMatrix(results, conditionInfo) : null);
	const graded = $derived((results?.leaderboard ?? []).some((r) => r.n_graded > 0));
	const baselineItems = $derived(
		experiment.conditions.map((c) => ({
			value: String(c.id),
			label: c.label,
			description: c.id === experiment.baseline_condition_id ? 'pre-registered' : undefined,
		}))
	);
	const currentBaseline = $derived(
		String(results?.baseline_condition_id ?? baselineParam ?? experiment.baseline_condition_id ?? '')
	);
	const toggles = $derived([
		{ key: 'common', label: 'Common cells only', on: query.common_cells !== false, def: true, title: 'Compare every condition on the same graded (app, repeat) cells' },
		{ key: 'invalid', label: 'Exclude invalid', on: !!query.exclude_invalid, def: false, title: 'Drop runs whose validity is “exclude” (for every condition)' },
		{ key: 'truncated', label: 'Exclude truncated', on: query.exclude_truncated !== false, def: true, title: 'Drop runs that hit a budget or wall-clock limit (for every condition)' },
		{ key: 'fingerprint', label: 'Majority fingerprint only', on: query.fingerprint === 'majority', def: false, title: 'Keep only runs with the condition’s most common configuration fingerprint' },
	]);

	function toggle(key: string, on: boolean, def: boolean) {
		if (key === 'fingerprint') setParam('fingerprint', on ? 'majority' : null);
		else setParam(key, on === def ? null : on ? '1' : '0');
	}
</script>

<div class="space-y-6">
	<Card.Root>
		<Card.Content class="flex flex-col gap-4 py-4 lg:flex-row lg:items-end">
			<div class="min-w-0 space-y-1 lg:w-80">
				<Label class="text-xs" for="results-baseline">Baseline</Label>
				<Select.Simple
					id="results-baseline"
					size="sm"
					value={currentBaseline}
					items={baselineItems}
					onValueChange={(v) => setParam('baseline', v === String(experiment.baseline_condition_id) ? null : v)}
				/>
				{#if results?.baseline_source === 'post_hoc'}
					<p class="text-[11px] text-warning-fg">Post hoc — exploratory. The pre-registered baseline is the default.</p>
				{/if}
			</div>
			<div class="flex flex-wrap gap-x-5 gap-y-2">
				{#each toggles as t (t.key)}
					<label class="flex items-center gap-2 text-xs" title={t.title}>
						<Switch size="sm" checked={t.on} label={t.label} onCheckedChange={(v) => toggle(t.key, v, t.def)} />
						{t.label}
					</label>
				{/each}
			</div>
			<Button
				variant="outline"
				size="sm"
				class="lg:ml-auto"
				href="/reports/create?type=experiment_report&experiment_id={experiment.id}"
			>
				<FileText />Generate report
			</Button>
		</Card.Content>
	</Card.Root>

	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{/if}

	{#if loading && !results}
		<LoadingState label="computing results" />
	{:else if results}
		{#if results.interim}
			<Alert>
				<Info />
				<AlertDescription>Interim: conditions are at unequal progress — p-values are hidden until the experiment finishes.</AlertDescription>
			</Alert>
		{/if}
		{#if !graded}
			<EmptyState
				icon={Trophy}
				title="No graded cells yet"
				description={experiment.mode === 'scaffolding' && !experiment.post_run?.evaluate
					? 'Turn on Evaluate in Automation to grade runs.'
					: 'Results appear as runs are evaluated.'}
			/>
		{:else}
			<section class="space-y-2" aria-labelledby="leaderboard-title">
				<h2 id="leaderboard-title" class="section-label">Leaderboard</h2>
				<Leaderboard {results} />
			</section>

			<section class="space-y-2" aria-labelledby="paired-title">
				<h2 id="paired-title" class="section-label">Against the baseline</h2>
				<PairedDeltas {results} labelOf={(id) => conditionLabel.get(id) ?? `Condition #${id}`} />
			</section>

			{#if matrix}
				<section class="space-y-2" aria-labelledby="matrix-title">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<h2 id="matrix-title" class="section-label">Per app</h2>
						<label class="flex items-center gap-2 text-xs">
							<Switch size="sm" checked={transpose} label="Conditions as rows" onCheckedChange={(v) => setParam('transpose', v ? '1' : '0')} />
							Conditions as rows
						</label>
					</div>
					<ResultsMatrix {matrix} mode={experiment.mode} graded {transpose} />
				</section>
			{/if}
		{/if}

		{#if results.notes.length > 0}
			<ul class="space-y-1 text-[11px] text-muted-foreground" aria-label="Notes">
				{#each results.notes as note (note)}
					<li class="flex items-start gap-1.5"><Info class="mt-px size-3 shrink-0" aria-hidden="true" />{note}</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>
