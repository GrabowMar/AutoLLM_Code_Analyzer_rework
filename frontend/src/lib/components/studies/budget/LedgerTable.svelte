<script lang="ts">
	/**
	 * LedgerTable — a study's spend ledger (`GET /budget/studies/{id}/ledger/`): every reserve, usage,
	 * adjust and release entry with billed and list-price amounts, tokens, source and its dedupe key
	 * (entries are idempotent). Filter by kind; paginated.
	 *
	 * Props: `studyId`, `refreshSignal` (bump to refetch, e.g. on a `spend` event).
	 */
	import { untrack } from 'svelte';
	import { getStudyLedger } from '$lib/api/v3/budget';
	import type { Page, SpendLedgerEntryOut } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { tableFrame } from '$lib/components/table';
	import { formatTokenCount, formatUsd } from '../format';

	interface Props {
		studyId: string;
		refreshSignal?: number;
	}

	let { studyId, refreshSignal = 0 }: Props = $props();

	const PAGE_SIZE = 50;
	let kind = $state('');
	let pageNum = $state(1);
	let data = $state<Page<SpendLedgerEntryOut> | null>(null);
	let error = $state('');
	let seq = 0;

	async function load() {
		const mine = ++seq;
		try {
			const out = await getStudyLedger(studyId, { kind: kind || undefined, page: pageNum, page_size: PAGE_SIZE });
			if (mine === seq) {
				data = out;
				error = '';
			}
		} catch (e) {
			if (mine === seq) error = formatApiError(e, 'Could not load the ledger.');
		}
	}

	$effect(() => {
		void studyId;
		void kind;
		void pageNum;
		void refreshSignal;
		untrack(() => load());
	});

	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);
</script>

<div class="space-y-2">
	<div class="flex items-center gap-2">
		<Select.Simple
			id="ledger-kind"
			size="sm"
			value={kind}
			items={[
				{ value: '', label: 'Every entry' },
				{ value: 'reserve', label: 'Reserve' },
				{ value: 'usage', label: 'Usage' },
				{ value: 'adjust', label: 'Adjust' },
				{ value: 'release', label: 'Release' },
			]}
			onValueChange={(v) => {
				kind = v;
				pageNum = 1;
			}}
		/>
		{#if data}<span class="text-xs text-muted-foreground">{data.total} entries</span>{/if}
	</div>
	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if !data}
		<LoadingState label="loading the ledger" />
	{:else if data.items.length === 0}
		<p class="text-xs text-muted-foreground">No ledger entry yet.</p>
	{:else}
		<div class="table-frame rounded-md border border-border" {@attach tableFrame}>
			<table>
				<thead>
					<tr>
						<th>When</th>
						<th>Kind</th>
						<th>Run</th>
						<th>Model</th>
						<th class="num">Billed</th>
						<th class="num">List</th>
						<th class="num">Tokens in / out</th>
						<th>Source</th>
					</tr>
				</thead>
				<tbody>
					{#each data.items as e (e.id)}
						<tr>
							<td class="font-mono text-xs">{new Date(e.created_at).toLocaleString()}</td>
							<td>{e.kind}</td>
							<td class="font-mono text-xs">{#if e.run}<a class="text-primary hover:underline" href="/samples/{e.run}">{e.run.slice(0, 8)}</a>{#if e.attempt}/a{e.attempt}{/if}{:else}—{/if}</td>
							<td class="max-w-40 truncate text-xs">{e.model_slug || '—'}</td>
							<td class="num">{formatUsd(e.amount_usd)}</td>
							<td class="num">{formatUsd(e.list_cost_usd)}</td>
							<td class="num">{formatTokenCount(e.tokens_in)} / {formatTokenCount(e.tokens_out)}</td>
							<td class="text-xs text-muted-foreground" title={e.dedupe_key}>{e.source}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if pages > 1}<PaginationBar page={pageNum} {pages} onGoToPage={(p) => (pageNum = p)} />{/if}
	{/if}
</div>
