<script lang="ts">
	// Applications: the agentic app samples (`GET /runs/?kind=app_agentic`, spec 9.5, 10.1). Each row opens
	// `/samples/[id]`; tested, usable rows can be used as slot parents. A pass rate that was not measured is
	// shown as "not measured", never 0.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Button } from '$lib/components/ui/button';
	import { rowLink, tableFrame, TableToolbar } from '$lib/components/table';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Scissors from '@lucide/svelte/icons/scissors';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listSamples } from '$lib/api/v3/samples';
	import { RUN_OUTCOMES, type Page, type RunListOut } from '$lib/api/v3/types';
	import {
		fmtRate,
		fmtSeconds,
		fmtUsd,
		OUTCOME_TONES,
		outcomeLabel,
		shortSha,
		UseAsParentAction,
	} from '$lib/components/runs';
	import { formatDate } from '$lib/utils/formatters';

	const PAGE_SIZE = 50;

	const outcome = $derived(page.url.searchParams.get('outcome') ?? '');
	const usable = $derived(page.url.searchParams.get('usable') ?? '');
	const model = $derived(page.url.searchParams.get('model') ?? '');
	const currentPage = $derived(Math.max(1, Number(page.url.searchParams.get('page') ?? '1') || 1));

	let data = $state<Page<RunListOut> | null>(null);
	let loading = $state(true);
	let error = $state('');
	let modelInput = $state('');
	let reloadKey = $state(0);

	$effect(() => {
		modelInput = model;
	});

	function setParam(key: string, value: string) {
		const url = new URL(page.url);
		if (value) url.searchParams.set(key, value);
		else url.searchParams.delete(key);
		if (key !== 'page') url.searchParams.delete('page');
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	$effect(() => {
		void reloadKey;
		const controller = new AbortController();
		const query = {
			kind: 'app_agentic',
			outcome: outcome || undefined,
			usable: usable === '' ? undefined : usable === 'true',
			model: model || undefined,
			page: currentPage,
			page_size: PAGE_SIZE,
		};
		loading = untrack(() => data === null);
		listSamples(query, { signal: controller.signal })
			.then((out) => {
				data = out;
				error = '';
			})
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the applications.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);
	const labelKeys = $derived.by(() => {
		const keys = new Set<string>();
		for (const row of data?.items ?? [])
			for (const key of Object.keys(row.labels ?? {})) if (key !== 'model' && key !== 'task') keys.add(key);
		return [...keys].sort().slice(0, 4);
	});
	const filtered = $derived(!!(outcome || usable || model));
</script>

<div class="space-y-4">
	<PageHeader
		title="Applications"
		description="Agentic app samples: open one to see its manifest, files, trajectory and evaluation, or use it as a slot parent."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (reloadKey += 1)}>
				<RefreshCw class="mr-1.5 h-3.5 w-3.5" />Refresh
			</Button>
			<Button size="sm" href="/slots/new">
				<Scissors class="mr-1.5 h-3.5 w-3.5" />Regenerate a part…
			</Button>
		{/snippet}
		{#if data}
			<div class="header-stats">
				<span class="header-stat"><b>{data.total.toLocaleString()}</b> {filtered ? 'matching' : 'total'}</span>
			</div>
		{/if}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => (reloadKey += 1)} />{/if}

	<Card.Root class="gap-0 overflow-clip py-0">
		<TableToolbar>
			{#snippet summary()}
				<div class="flex flex-wrap items-center gap-2">
					<Select.Simple
						size="sm"
						aria-label="Outcome"
						value={outcome}
						onValueChange={(v: string) => setParam('outcome', v)}
						placeholder="Any outcome"
						clearLabel="Any outcome"
						items={RUN_OUTCOMES.map((o) => ({ value: o, label: outcomeLabel(o) }))}
					/>
					<Select.Simple
						size="sm"
						aria-label="Usable"
						value={usable}
						onValueChange={(v: string) => setParam('usable', v)}
						placeholder="Usable or not"
						clearLabel="Usable or not"
						items={[
							{ value: 'true', label: 'Usable', description: 'Model-attributable outcomes (analysed as data)' },
							{ value: 'false', label: 'Not usable', description: 'Infra errors and exclusions' },
						]}
					/>
					<form
						class="flex items-center gap-1"
						onsubmit={(e) => {
							e.preventDefault();
							setParam('model', modelInput.trim());
						}}
					>
						<Input class="h-8 w-56" placeholder="Model id (exact), Enter" aria-label="Model" bind:value={modelInput} />
					</form>
				</div>
			{/snippet}
		</TableToolbar>

		{#if loading}
			<LoadingState label="loading applications" />
		{:else if !data || data.items.length === 0}
			<EmptyState
				compact
				icon={AppWindow}
				title={filtered ? 'No application matches' : 'No applications yet'}
				description={filtered
					? 'Change or clear the filters.'
					: 'Agentic app runs appear here when a study (or a “Generate a parent app” run) creates them.'}
			>
				{#snippet action()}
					{#if !filtered}<Button size="sm" href="/studies/new?mode=app">New study</Button>{/if}
				{/snippet}
			</EmptyState>
		{:else}
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th>Sample</th>
							<th>Model</th>
							{#each labelKeys as key (key)}<th>{key.replace(/_/g, ' ')}</th>{/each}
							<th>Outcome</th>
							<th class="num" title="Agent checks only; infra and flaky checks excluded">Pass rate</th>
							<th class="num">Cost</th>
							<th class="num">Wall time</th>
							<th>Created</th>
							<th><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each data.items as row (row.id)}
							<tr class="row-link" onclick={rowLink(`/samples/${row.id}`)}>
								<td>
									<a class="font-mono text-xs text-primary hover:underline" href={`/samples/${row.id}`}>{shortSha(row.id, 8)}</a>
									{#if row.task_title || row.labels?.task || row.task}<div class="cell-sub" title={row.task ?? ''}>{row.task_title ?? row.labels?.task ?? row.task}</div>{/if}
								</td>
								<td class="font-mono text-xs">{row.model ?? '—'}</td>
								{#each labelKeys as key (key)}<td class="text-xs">{row.labels?.[key] ?? '—'}</td>{/each}
								<td>
									{#if row.outcome}
										<StatusBadge status={row.outcome} tones={OUTCOME_TONES} label={outcomeLabel(row.outcome)} />
									{:else}
										<StatusBadge status={row.status} />
									{/if}
									{#if row.phase && row.status === 'running'}<div class="cell-sub">{row.phase}</div>{/if}
								</td>
								<td class="num">
									{#if row.agent_pass_rate === null}<span class="text-muted-foreground">not measured</span>{:else}{fmtRate(row.agent_pass_rate)}{/if}
								</td>
								<td class="num">{fmtUsd(row.cost_usd)}</td>
								<td class="num">{fmtSeconds(row.wall_s)}</td>
								<td class="text-xs text-muted-foreground">{formatDate(row.created_at)}</td>
								<td class="text-right">
									<UseAsParentAction runId={row.id} run={row} hideWhenIneligible />
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar
				class="border-t border-border"
				resultsText={`${data.items.length} of ${data.total.toLocaleString()}`}
				page={currentPage}
				{pages}
				onGoToPage={(p) => setParam('page', String(p))}
			/>
		{/if}
	</Card.Root>
</div>
