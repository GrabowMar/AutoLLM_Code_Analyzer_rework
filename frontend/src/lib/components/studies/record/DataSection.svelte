<script lang="ts">
	/**
	 * DataSection — section "Data" of the study record (spec 10.3 §5): the export archive (plan,
	 * manifests, runs, attrition, files, evaluations, locks, patterns), and every run of the study with
	 * filters (outcome, usable, beyond plan), its manifest, and "Use as parent" for usable agentic runs.
	 *
	 * Props: `studyId`, `number`, `refreshSignal`.
	 */
	import { untrack } from 'svelte';
	import { listSamples } from '$lib/api/v3/samples';
	import { studyExportUrl } from '$lib/api/v3/studies';
	import type { Page, RunListOut } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { TableToolbar, tableFrame } from '$lib/components/table';
	import UseAsParentAction from '$lib/components/runs/UseAsParentAction.svelte';
	import StudySection from '../study/StudySection.svelte';
	import { formatPct, formatSeconds, formatUsd, outcomeLabel, runTone } from '../format';
	import Download from '@lucide/svelte/icons/download';

	interface Props {
		studyId: string;
		number?: string;
		refreshSignal?: number;
	}

	let { studyId, number = '4', refreshSignal = 0 }: Props = $props();

	const PAGE_SIZE = 50;
	let outcome = $state('');
	let usable = $state('');
	let beyond = $state('');
	let pageNum = $state(1);
	let data = $state<Page<RunListOut> | null>(null);
	let error = $state('');
	let seq = 0;

	async function load() {
		const mine = ++seq;
		try {
			const out = await listSamples({
				study: studyId,
				outcome: outcome || undefined,
				usable: usable === '' ? undefined : usable === 'true',
				beyond_plan: beyond === '' ? undefined : beyond === 'true',
				page: pageNum,
				page_size: PAGE_SIZE,
			});
			if (mine === seq) {
				data = out;
				error = '';
			}
		} catch (e) {
			if (mine === seq) error = formatApiError(e, 'Could not load the runs.');
		}
	}

	$effect(() => {
		void studyId;
		void outcome;
		void usable;
		void beyond;
		void pageNum;
		void refreshSignal;
		untrack(() => load());
	});

	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);
	const reset = (fn: () => void) => {
		fn();
		pageNum = 1;
	};
</script>

<StudySection id="data" {number} title="Data" lead="Every run with its manifest; the archive holds what is needed to re-analyse the experiment offline.">
	{#snippet aside()}
		<Button variant="outline" size="sm" href={studyExportUrl(studyId)} download><Download />Export (zip)</Button>
	{/snippet}
	<Card.Root class="gap-0 overflow-clip py-0">
		<TableToolbar>
			{#snippet summary()}{data?.total ?? 0} runs{/snippet}
			{#snippet controls()}
				<Select.Simple
					id="runs-outcome"
					size="sm"
					value={outcome}
					items={[{ value: '', label: 'Any outcome' }, ...['tested', 'output_invalid', 'build_fail', 'start_fail', 'infra_error', 'excluded'].map((o) => ({ value: o, label: outcomeLabel(o) }))]}
					onValueChange={(v) => reset(() => (outcome = v))}
				/>
				<Select.Simple
					id="runs-usable"
					size="sm"
					value={usable}
					items={[
						{ value: '', label: 'Usable or not' },
						{ value: 'true', label: 'Usable' },
						{ value: 'false', label: 'Not usable' },
					]}
					onValueChange={(v) => reset(() => (usable = v))}
				/>
				<Select.Simple
					id="runs-beyond"
					size="sm"
					value={beyond}
					items={[
						{ value: '', label: 'Planned and beyond' },
						{ value: 'false', label: 'Planned only' },
						{ value: 'true', label: 'Beyond plan only' },
					]}
					onValueChange={(v) => reset(() => (beyond = v))}
				/>
			{/snippet}
		</TableToolbar>
		{#if error}
			<div class="p-3"><ErrorAlert message={error} onretry={load} /></div>
		{:else if !data}
			<LoadingState label="loading runs" />
		{:else if data.items.length === 0}
			<p class="px-4 py-6 text-center text-xs text-muted-foreground">No run matches.</p>
		{:else}
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead><tr><th class="col-sticky">Run</th><th>Condition</th><th>Status</th><th>Outcome</th><th class="num">Pass rate</th><th class="num">Cost</th><th class="num">Wall</th><th></th></tr></thead>
					<tbody>
						{#each data.items as r (r.id)}
							<tr>
								<td class="col-sticky"><a class="font-mono text-xs text-primary hover:underline" href="/samples/{r.id}">{r.id.slice(0, 8)}</a><span class="cell-sub block text-xs text-muted-foreground">{r.model ?? ''}</span></td>
								<td class="max-w-56 truncate text-xs" title={Object.values(r.labels).join(' · ')}>{Object.values(r.labels).join(' · ') || '—'}</td>
								<td><StatusBadge status={r.status} tone={runTone(r.status)} /></td>
								<td>{#if r.outcome}<StatusBadge status={r.outcome} tone={runTone(r.outcome)} label={outcomeLabel(r.outcome)} />{:else}<span class="text-muted-foreground">—</span>{/if}</td>
								<td class="num">{formatPct(r.agent_pass_rate, 1)}</td>
								<td class="num">{formatUsd(r.cost_usd)}</td>
								<td class="num">{formatSeconds(r.wall_s)}</td>
								<td class="row-actions-more whitespace-nowrap text-right">
									<a class="text-xs text-primary hover:underline" href="/api/generation/runs/{r.id}/manifest/" target="_blank" rel="noopener">manifest</a>
									<UseAsParentAction runId={r.id} run={{ kind: String(r.kind), status: String(r.status), usable: r.usable, outcome: r.outcome }} size="sm" variant="ghost" hideWhenIneligible />
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if pages > 1}<PaginationBar class="border-t border-border" page={pageNum} {pages} onGoToPage={(p) => (pageNum = p)} />{/if}
		{/if}
	</Card.Root>
</StudySection>
