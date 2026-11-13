<script lang="ts">
	// The generated apps (GenerationRuns) of one model: the caller's runs. Summary first — outcome mix as one
	// stacked bar whose legend filters the list, plus live app containers — then a paginated list (25 a page)
	// filtered by outcome and run kind. GET /api/generation/runs/?model=<model id>[&outcome=&kind=&page=].
	import { onMount, untrack } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import { rowLink, tableFrame } from '$lib/components/table';
	import { listSamples } from '$lib/api/v3/samples';
	import type { RunListOut } from '$lib/api/v3/types';
	import { getContainers, type ContainerInstance } from '$lib/api/runtime';
	import { formatApiError } from '$lib/api/core';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { outcomeLabels, outcomeTones, toneFillClasses, type RunOutcome } from '$lib/constants/colors';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import FilterMenu from '$lib/components/FilterMenu.svelte';
	import { formatDuration } from '$lib/utils/formatters';
	import { fmtUsd } from './helpers';

	interface Props {
		modelId: string;
		slug: string;
		onLoaded?: (total: number) => void;
	}

	let { modelId, slug, onLoaded }: Props = $props();

	const PAGE_SIZE = 25;
	const OUTCOMES = Object.keys(outcomeLabels) as RunOutcome[];
	const KIND_LABELS: Record<string, string> = {
		app_agentic: 'Agentic app',
		slot_single_shot: 'Slot variant',
		slot_agent: 'Slot variant (agent)',
		reference: 'Reference',
	};
	const KIND_OPTIONS = Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label }));

	let runs = $state<RunListOut[]>([]);
	let total = $state(0);
	let grandTotal = $state(0);
	let page = $state(1);
	let outcome = $state('');
	let kind = $state('');
	let loading = $state(true);
	let error = $state('');
	let counts = $state<Partial<Record<RunOutcome | 'none', number>>>({});
	let containers = $state<ContainerInstance[]>([]);

	const pages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));
	const running = $derived(containers.filter((c) => c.status === 'running'));
	const liveByRun = $derived(new Map(running.filter((c) => c.generation_job_id).map((c) => [c.generation_job_id as string, c])));
	const segments = $derived(
		OUTCOMES.map((o) => ({ key: o, label: outcomeLabels[o], n: counts[o] ?? 0, tone: outcomeTones[o] })).filter((s) => s.n > 0),
	);
	const pending = $derived(counts.none ?? 0);

	/** The run's outcome once it has one; before that its phase or status. */
	function runState(run: RunListOut): { status: string; label: string } {
		if (run.outcome) return { status: run.outcome, label: outcomeLabels[run.outcome as RunOutcome] ?? run.outcome };
		const status = run.phase || run.status;
		return { status, label: status.replace(/_/g, ' ') };
	}

	function fmtDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
	}

	let seq = 0;
	async function load() {
		const mine = ++seq;
		loading = true;
		error = '';
		try {
			const res = await listSamples({ model: modelId, page, page_size: PAGE_SIZE, outcome: outcome || undefined, kind: kind || undefined });
			if (mine !== seq) return;
			runs = res.items;
			total = res.total;
		} catch (e) {
			if (mine === seq) error = formatApiError(e, 'Failed to load applications.');
		} finally {
			if (mine === seq) loading = false;
		}
	}

	/** Outcome counts (one count-only request per outcome) and live containers, once. */
	async function loadSummary() {
		try {
			const [all, ...perOutcome] = await Promise.all([
				listSamples({ model: modelId, page_size: 1 }),
				...OUTCOMES.map((o) => listSamples({ model: modelId, outcome: o, page_size: 1 })),
			]);
			grandTotal = all.total;
			onLoaded?.(all.total);
			const next: Partial<Record<RunOutcome | 'none', number>> = {};
			OUTCOMES.forEach((o, i) => (next[o] = perOutcome[i].total));
			next.none = Math.max(0, all.total - OUTCOMES.reduce((a, o) => a + (next[o] ?? 0), 0));
			counts = next;
		} catch {
			/* the list still works without the summary */
		}
		try {
			containers = (await getContainers({ model_id: modelId, per_page: 100 })).containers;
		} catch {
			containers = [];
		}
	}

	function setOutcome(v: string) {
		outcome = outcome === v ? '' : v;
		page = 1;
		load();
	}
	function setKind(v: string) {
		kind = v;
		page = 1;
		load();
	}

	onMount(() => {
		untrack(() => {
			load();
			loadSummary();
		});
	});
</script>

<section id="applications" class="md-sec" aria-labelledby="apps-title">
	<div class="md-sec-head">
		<h2 id="apps-title" class="md-sec-title">Apps</h2>
		<span class="md-sec-desc">Your generations with this model, newest first</span>
		<span class="md-sec-aside">
			<Button size="sm" href="/experiments/new?mode=app" title="Generate apps with this model in an app experiment (pick {slug} as a model level)">
				<Sparkles class="size-3.5" />New
			</Button>
		</span>
	</div>

	{#if grandTotal > 0}
		<!-- Outcome mix: one stacked bar; the legend filters the list below. -->
		<div class="apps-summary">
			<div class="flex flex-wrap items-baseline gap-x-6 gap-y-1">
				<span><b class="font-mono text-xl font-medium tabular-nums">{grandTotal.toLocaleString()}</b> <span class="text-xs text-muted-foreground">apps</span></span>
				{#if running.length}
					<span class="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
						<span class="status-dot status-dot-live h-1.5 w-1.5 text-success"></span>
						<b class="font-mono text-foreground">{running.length}</b> running now
					</span>
				{/if}
				{#if pending}<span class="text-xs text-muted-foreground"><b class="font-mono text-foreground">{pending}</b> without an outcome yet</span>{/if}
			</div>
			{#if segments.length}
				<div class="apps-bar" aria-hidden="true">
					{#each segments as seg (seg.key)}
						<span class="{toneFillClasses[seg.tone]} {outcome && outcome !== seg.key ? 'opacity-30' : ''}" style:flex-grow={seg.n} title="{seg.label}: {seg.n}"></span>
					{/each}
					{#if pending}<span class="bg-muted" style:flex-grow={pending}></span>{/if}
				</div>
				<div class="flex flex-wrap gap-1.5" role="group" aria-label="Filter by outcome">
					{#each segments as seg (seg.key)}
						<button type="button" class="apps-legend" aria-pressed={outcome === seg.key} onclick={() => setOutcome(seg.key)}>
							<span class="size-2 rounded-sm {toneFillClasses[seg.tone]}"></span>{seg.label}
							<span class="font-mono tabular-nums text-muted-foreground">{seg.n}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	{#if grandTotal > 0 || outcome || kind}
	<div class="flex flex-wrap items-center gap-2 border-t border-border px-5 py-2">
		<FilterMenu label="Kind" value={kind} options={KIND_OPTIONS} onChange={setKind} anyLabel="All kinds" />
		{#if outcome}
			<button type="button" class="apps-legend" aria-pressed="true" onclick={() => setOutcome(outcome)}>
				{outcomeLabels[outcome as RunOutcome]} ×
			</button>
		{/if}
		<span class="ml-auto text-xs text-muted-foreground">{total.toLocaleString()} shown{outcome || kind ? ' (filtered)' : ''}</span>
	</div>
	{/if}

	{#if error}
		<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
	{:else if !loading && grandTotal === 0 && total === 0}
		<div class="p-4">
			<EmptyState compact icon={AppWindow} title="No apps yet" description="No applications have been generated with this model.">
				{#snippet action()}
					<Button size="sm" variant="outline" href="/experiments/new?mode=app"><Sparkles class="size-3.5" />Generate the first one</Button>
				{/snippet}
			</EmptyState>
		</div>
	{:else}
		<div class="table-frame border-t border-border" {@attach tableFrame} aria-busy={loading}>
			<table class={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
				<thead>
					<tr>
						<th>App</th>
						<th>Task · setup</th>
						<th>Outcome</th>
						<th class="num" title="Latest evaluation's agent pass rate; — when not measured (never 0)">Pass rate</th>
						<th class="num">Cost</th>
						<th class="num">Wall</th>
						<th class="num">Created</th>
					</tr>
				</thead>
				<tbody>
					{#if loading && runs.length === 0}
						{#each Array(8) as _, i (i)}
							<tr>{#each Array(7) as __, j (j)}<td><span class="md-skel h-3 {j === 0 ? 'w-20' : 'w-12'}"></span></td>{/each}</tr>
						{/each}
					{:else}
						{#each runs as run (run.id)}
							{@const href = `/samples/${run.id}`}
							{@const state = runState(run)}
							{@const live = liveByRun.get(run.id)}
							{@const setup = [run.labels?.stack_profile, run.labels?.harness_preset].filter(Boolean).join(' · ')}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the id link is the keyboard path) -->
							<tr class="row-link" onclick={rowLink(href)}>
								<td class="whitespace-nowrap">
									<a {href} class="font-mono text-xs font-medium hover:text-primary" title="Open sample {run.id}">{run.id.substring(0, 8)}</a>
									<span class="cell-sub block text-[11px] text-muted-foreground">{KIND_LABELS[run.kind] ?? run.kind}</span>
								</td>
								<td class="max-w-[22rem]">
									<span class="block truncate text-sm" title={run.task ?? undefined}>{run.task_title || run.task || '—'}</span>
									<span class="cell-sub block truncate font-mono text-[11px] text-muted-foreground" title={setup || undefined}>{setup || '—'}</span>
								</td>
								<td class="whitespace-nowrap">
									<StatusBadge status={state.status} label={state.label} />
									{#if run.usable === false}<span class="cell-sub block text-[11px] text-muted-foreground" title="Not usable: attrition, never counted as a failure">not usable</span>{/if}
									{#if live?.app_url}
										<a href={live.app_url} target="_blank" rel="noopener noreferrer" class="mt-0.5 inline-flex items-center gap-1 text-[11px] text-success-fg hover:underline" onclick={(e) => e.stopPropagation()}>
											<span class="status-dot status-dot-live h-1.5 w-1.5 text-success"></span>live<ExternalLink class="size-3" aria-hidden="true" />
										</a>
									{/if}
								</td>
								<td class="num">
									{#if run.agent_pass_rate != null}
										<span class="inline-flex items-center gap-2">
											<span class="md-bar w-12" aria-hidden="true"><span style:width="{run.agent_pass_rate * 100}%"></span></span>
											<span class="w-9 text-right font-mono text-xs tabular-nums">{Math.round(run.agent_pass_rate * 100)}%</span>
										</span>
									{:else}<span class="text-muted-foreground" title="Not measured">—</span>{/if}
								</td>
								<td class="num font-mono text-xs">{run.cost_usd == null ? '—' : fmtUsd(run.cost_usd)}</td>
								<td class="num font-mono text-xs">{run.wall_s == null ? '—' : formatDuration(run.wall_s, 0)}</td>
								<td class="num whitespace-nowrap text-xs text-muted-foreground">{fmtDate(run.created_at)}</td>
							</tr>
						{:else}
							<tr><td colspan="7" class="py-6 text-center text-sm text-muted-foreground">No apps match these filters.</td></tr>
						{/each}
					{/if}
				</tbody>
			</table>
		</div>
		{#if pages > 1}
			<PaginationBar
				resultsText={`${(page - 1) * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE, total)} of ${total.toLocaleString()}`}
				{page}
				{pages}
				onGoToPage={(p) => {
					page = p;
					load();
				}}
				class="border-t border-border"
				scrollToCard
			/>
		{/if}
	{/if}
</section>

<style>
	.apps-summary {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 1rem 1.25rem;
	}
	.apps-bar {
		display: flex;
		height: 0.625rem;
		gap: 2px;
		overflow: hidden;
		border-radius: 9999px;
	}
	.apps-bar > span {
		min-width: 3px;
		transition: opacity 150ms;
	}
	.apps-legend {
		display: inline-flex;
		cursor: pointer;
		align-items: center;
		gap: 0.375rem;
		border-radius: 9999px;
		border: 1px solid var(--border);
		padding: 0 0.625rem;
		font-size: 0.75rem;
		line-height: 1.5rem;
		transition:
			background-color 150ms,
			border-color 150ms;
	}
	.apps-legend:hover {
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
	}
	.apps-legend[aria-pressed='true'] {
		background: var(--accent);
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
		color: var(--accent-foreground);
	}
	.apps-legend:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
</style>
