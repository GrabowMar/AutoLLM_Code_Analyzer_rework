<script lang="ts">
	// SlotSet page (spec 10.1): one frozen parent — its record (tree hash, symbol index, baseline evaluation,
	// how it was chosen) and its targets with validation and leak status and the original's slot pass rate,
	// plus "Choose parts" and "New slot study".
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { rowLink, tableFrame } from '$lib/components/table';
	import Scissors from '@lucide/svelte/icons/scissors';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSlotSet, getSlotTarget } from '$lib/api/v3/slots';
	import type { SlotSetDetailOut, SlotTargetDetailOut } from '$lib/api/v3/types';
	import { fmtRate, shortSha } from '$lib/components/runs/format';
	import { slotStudyHref, VALIDATION_TONES, validationLabel } from '$lib/components/slots';

	const setId = $derived(page.params.set ?? '');
	let set = $state<SlotSetDetailOut | null>(null);
	let details = $state<Record<string, SlotTargetDetailOut>>({});
	let loading = $state(true);
	let error = $state('');

	async function load(id: string, signal?: AbortSignal) {
		loading = untrack(() => set === null);
		error = '';
		try {
			set = await getSlotSet(id, { signal });
			const rows = await Promise.allSettled(set.targets.map((t) => getSlotTarget(t.id, { signal })));
			const next: Record<string, SlotTargetDetailOut> = {};
			for (const r of rows) if (r.status === 'fulfilled') next[r.value.id] = r.value;
			details = next;
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the slot set.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const controller = new AbortController();
		void load(setId, controller.signal);
		return () => controller.abort();
	});

	const pending = $derived((set?.targets ?? []).some((t) => t.validation?.status === 'pending'));
	$effect(() => {
		if (!pending) return;
		const timer = setInterval(() => void load(setId), 10_000);
		return () => clearInterval(timer);
	});

	const selection = $derived(set?.selection ?? {});
</script>

<div class="space-y-4">
	<PageHeader
		title={set?.name || (set ? `Slot set ${shortSha(set.id, 8)}` : 'Slot set')}
		description="A frozen parent app: parts of it are regenerated while the rest stays byte-identical."
		back={{ href: '/slots/new', label: 'Slot picker' }}
	>
		{#snippet actions()}
			{#if set}
				<Button variant="outline" size="sm" href={`/slots/new?sets=${set.id}`}><Scissors class="mr-1.5 h-3.5 w-3.5" />Choose parts</Button>
				<Button size="sm" href={slotStudyHref([set.id], set.targets)} disabled={!set.targets.some((t) => t.usable)}>
					<FlaskConical class="mr-1.5 h-3.5 w-3.5" />New slot study
				</Button>
			{/if}
		{/snippet}
		{#if set}
			<div class="header-stats">
				<span class="header-stat"><b class="font-mono">{set.task ?? '—'}</b> task</span>
				<span class="header-stat"><b>{set.parent_model}</b> parent model</span>
				<span class="header-stat"><b>{set.parent_harness}</b> harness</span>
				<span class="header-stat"><b>{fmtRate(set.parent_eval?.agent_pass_rate ?? null)}</b> parent pass rate</span>
				<span class="header-stat"><b>{set.targets.length}</b> targets</span>
			</div>
		{/if}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => load(setId)} />{/if}
	{#if loading}
		<LoadingState label="loading slot set" />
	{:else if set}
		<div class="grid gap-4 lg:grid-cols-2">
			<Card.Root>
				<Card.Header><Card.Title class="font-display">Frozen parent</Card.Title></Card.Header>
				<Card.Content class="text-xs">
					<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
						<dt class="text-muted-foreground">parent run</dt>
						<dd><a class="font-mono text-primary hover:underline" href={`/samples/${set.parent_run}`}>{set.parent_run}</a></dd>
						<dt class="text-muted-foreground">tree hash</dt>
						<dd class="truncate font-mono" title={set.frozen_tree_hash}>{shortSha(set.frozen_tree_hash, 20)}</dd>
						<dt class="text-muted-foreground">manifest hash</dt>
						<dd class="truncate font-mono" title={set.parent_manifest_hash}>{shortSha(set.parent_manifest_hash, 20)}</dd>
						<dt class="text-muted-foreground">baseline evaluation</dt>
						<dd>
							{#if set.parent_eval}#{set.parent_eval.id} · {set.parent_eval.status} · <span class="font-mono">{set.parent_eval.eval_suite}</span>{:else}<span class="text-muted-foreground">none</span>{/if}
						</dd>
						<dt class="text-muted-foreground">index</dt>
						<dd>
							{set.index_stats.files} files · {set.index_stats.symbols} symbols · {set.index_stats.endpoints} endpoints · {set.index_stats.components} components
							{#if set.index_stats.unparsed.length}<div class="text-muted-foreground">unparsed: <span class="font-mono">{set.index_stats.unparsed.join(', ')}</span></div>{/if}
						</dd>
					</dl>
				</Card.Content>
			</Card.Root>
			<Card.Root>
				<Card.Header>
					<Card.Title class="font-display">How it was chosen</Card.Title>
					<Card.Description>Recorded on the slot set and in every variant's manifest.</Card.Description>
				</Card.Header>
				<Card.Content class="text-xs">
					<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
						<dt class="text-muted-foreground">rule</dt><dd class="font-mono">{selection.rule ?? 'manual'}</dd>
						<dt class="text-muted-foreground">seed</dt><dd class="font-mono">{selection.seed ?? '—'}</dd>
						<dt class="text-muted-foreground">pool size</dt><dd>{selection.pool_size ?? '—'}</dd>
						<dt class="text-muted-foreground">pool query</dt><dd class="break-all font-mono">{selection.pool_query ? JSON.stringify(selection.pool_query) : '—'}</dd>
						<dt class="text-muted-foreground">picked by</dt><dd>{selection.picked_by ?? '—'} {selection.at ? `· ${selection.at}` : ''}</dd>
					</dl>
				</Card.Content>
			</Card.Root>
		</div>

		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="font-display">Targets</Card.Title>
				<Card.Description>
					Validation evaluates the original 3× and the holed tree 1×; only checks that pass 3/3 on the original and fail with the hole are kept.
				</Card.Description>
			</Card.Header>
			{#if set.targets.length === 0}
				<EmptyState compact icon={Scissors} title="No target yet" description="Choose a part of this parent to regenerate.">
					{#snippet action()}<Button size="sm" href={`/slots/new?sets=${set?.id}`}>Choose parts</Button>{/snippet}
				</EmptyState>
			{:else}
				<div class="table-frame" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th>Target</th>
								<th>Kind</th>
								<th>Path</th>
								<th>Validation</th>
								<th class="num" title="Discriminating checks kept after validation">Checks</th>
								<th class="num">Leak</th>
								<th class="num" title="The original's pass rate over the discriminating checks (variant 0, a reference point)">Original</th>
								<th class="num">Variants</th>
							</tr>
						</thead>
						<tbody>
							{#each set.targets as target (target.id)}
								{@const d = details[target.id]}
								<tr class="row-link" onclick={rowLink(`/slots/targets/${target.id}`)}>
									<td>
										<a class="font-medium text-primary hover:underline" href={`/slots/targets/${target.id}`}>{target.label || shortSha(target.id, 8)}</a>
										{#if target.match_key}<div class="cell-sub font-mono">{target.match_key}</div>{/if}
									</td>
									<td><Badge variant="outline">{target.kind}</Badge></td>
									<td class="max-w-64 truncate font-mono text-xs" title={target.address?.path}>{target.address?.path}</td>
									<td>
										<StatusBadge status={target.validation?.status ?? 'pending'} tones={VALIDATION_TONES} label={validationLabel(target)} />
										{#if target.validation?.dropped_checks?.length}<div class="cell-sub">dropped {target.validation.dropped_checks.length}</div>{/if}
									</td>
									<td class="num">{target.validation?.discriminating_checks?.length ?? '—'}</td>
									<td class="num">
										{target.leak ? target.leak.score.toFixed(2) : '—'}
										{#if target.leak?.matches?.some((m) => m.verbatim)}<div class="cell-sub text-danger-fg">verbatim</div>{/if}
									</td>
									<td class="num">
										{#if target.validation?.status === 'uninformative'}<span class="text-muted-foreground" title="No discriminating check">n/a</span>{:else}{fmtRate(d?.original?.slot_pass_rate ?? null, '—')}{/if}
									</td>
									<td class="num">{d?.variants_summary?.n ?? '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card.Root>
	{/if}
</div>
