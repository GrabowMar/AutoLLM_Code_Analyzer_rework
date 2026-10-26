<script lang="ts">
	/**
	 * ApplicationAnalyses — the analysis runs of one generated application
	 * (Analyses tab on `/applications/[id]`): status, completeness, static
	 * findings and AI consensus with honest NA / lower-bound cells, profile
	 * and plan. Refreshes silently while a run is queued or running.
	 *
	 * Props: `jobId` (the generation job).
	 */
	import { untrack } from 'svelte';
	import type { AnalysisRunListItem } from '$lib/api/analysis';
	import { isRunActive, listAnalysisRuns } from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { cn } from '$lib/utils';
	import Hint from '$lib/components/analysis/Hint.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import RunDataBadge from '$lib/components/analysis/RunDataBadge.svelte';
	import {
		aiConsensusCell,
		fmtAgo,
		fmtDateTime,
		fmtInt,
		phaseLabel,
		PURPOSE_LABELS,
		shortHash,
		staticFindingsCell,
		toolProgress
	} from '$lib/components/analysis/run-utils';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	interface Props {
		jobId: string;
	}

	let { jobId }: Props = $props();

	const POLL_MS = 5_000;

	let runs = $state<AnalysisRunListItem[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let refreshing = $state(false);
	let error = $state('');
	let controller: AbortController | null = null;

	async function load(silent = false) {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		if (!silent) refreshing = true;
		try {
			const res = await listAnalysisRuns(
				{
					generation_job_id: jobId,
					per_page: 50,
					purpose: ['research', 'adhoc']
				},
				{ signal: mine.signal }
			);
			runs = res.items;
			total = res.total;
			error = '';
		} catch (e) {
			if (isAbortError(e)) return;
			if (!(silent && runs.length)) error = formatApiError(e, 'Could not load the analyses.');
		} finally {
			if (controller === mine) {
				loading = false;
				refreshing = false;
			}
		}
	}

	$effect(() => {
		void jobId;
		untrack(() => void load());
		return () => controller?.abort();
	});

	const anyActive = $derived(runs.some((r) => isRunActive(r.status)));
	$effect(() => {
		if (!anyActive) return;
		const timer = setInterval(() => {
			if (!document.hidden) void load(true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	const counts = $derived({
		complete: runs.filter((r) => !isRunActive(r.status) && r.is_complete).length,
		active: runs.filter((r) => isRunActive(r.status)).length,
		incomplete: runs.filter((r) => !isRunActive(r.status) && !r.is_complete).length
	});

	const createHref = $derived(`/analysis/create?generation_job_id=${encodeURIComponent(jobId)}`);
</script>

<section id="analyses" class="space-y-4">
	<Card.Root>
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-3">
			<div class="space-y-1.5">
				<Card.Title>Analyses</Card.Title>
				<Card.Description>
					{#if loading}
						Analysis runs of this application.
					{:else}
						{total} run{total === 1 ? '' : 's'} · {counts.complete} complete · {counts.incomplete}
						incomplete · {counts.active}
						in progress
					{/if}
				</Card.Description>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<Button size="sm" variant="outline" onclick={() => load()} disabled={refreshing}>
					<RefreshCw
						class="size-3.5 {refreshing ? 'animate-spin motion-reduce:animate-none' : ''}"
					/>Refresh
				</Button>
				{#if total > runs.length}
					<Button
						size="sm"
						variant="outline"
						href="/analysis?generation_job_id={encodeURIComponent(jobId)}">All runs</Button
					>
				{/if}
				<Button size="sm" href={createHref}><Plus class="size-3.5" />Analyse</Button>
			</div>
		</Card.Header>
		<Card.Content class={cn(runs.length > 0 && !error ? 'p-0' : '')}>
			{#if loading && runs.length === 0}
				<LoadingState label="loading analyses" class="py-8" />
			{:else if error}
				<ErrorAlert message={error} onretry={() => load()} />
			{:else if runs.length === 0}
				<EmptyState
					compact
					icon={Microscope}
					title="Not analysed yet"
					description="Run the static analyzers and the AI reviewer on this application."
				>
					{#snippet action()}
						<Button size="sm" href={createHref}>Analyse this application</Button>
					{/snippet}
				</EmptyState>
			{:else}
				<div
					class={cn('table-scroll-wrapper transition-opacity', refreshing && 'opacity-60')}
					aria-busy={refreshing}
				>
					<table class="table-card-mobile">
						<thead>
							<tr>
								<th>Run</th>
								<th>Status</th>
								<th>Data</th>
								<th class="text-right">Static findings</th>
								<th class="text-right">AI consensus</th>
								<th>Created</th>
							</tr>
						</thead>
						<tbody>
							{#each runs as run (run.id)}
								{@const st = staticFindingsCell(run)}
								{@const ai = aiConsensusCell(run)}
								{@const active = isRunActive(run.status)}
								{@const progress = toolProgress(run)}
								<tr>
									<td data-label="Run" class="max-w-xs">
										<div class="min-w-0 flex-1">
											<a
												href="/analysis/{run.id}"
												class="block truncate text-sm font-medium hover:text-primary hover:underline"
												title={run.name}>{run.name}</a
											>
											<span class="block truncate text-[11px] text-muted-foreground">
												{PURPOSE_LABELS[run.purpose]}{#if run.profile}{' · '}{run.profile.name} v{run
														.profile.version}{/if}{#if run.plan_hash}{' · '}<span
														class="font-mono"
														title="plan hash {run.plan_hash}">{shortHash(run.plan_hash, 8)}</span
													>{/if}
											</span>
										</div>
									</td>
									<td data-label="Status">
										<div class="flex flex-col items-start gap-0.5">
											<StatusBadge status={run.status} />
											{#if active}
												<span class="text-[11px] text-muted-foreground">
													{phaseLabel(run.phase)} · {progress.done}/{progress.total}
													tools
												</span>
											{/if}
										</div>
									</td>
									<td data-label="Data" class="whitespace-nowrap">
										<RunDataBadge {run} />
									</td>
									<td data-label="Static findings" class="tabular-nums md:text-right">
										{#if st.value === null}
											<NaValue reason={st.note} />
										{:else if st.lowerBound}
											<Hint text={st.note}>≥ {fmtInt(st.value)}</Hint>
										{:else}
											{fmtInt(st.value)}
										{/if}
									</td>
									<td data-label="AI consensus" class="tabular-nums md:text-right">
										{#if ai.value === null}
											<NaValue reason={ai.note} />
										{:else}
											{fmtInt(ai.value)}
										{/if}
									</td>
									<td
										data-label="Created"
										class="whitespace-nowrap text-xs"
										title={fmtDateTime(run.created_at)}
									>
										{fmtAgo(run.created_at)}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
