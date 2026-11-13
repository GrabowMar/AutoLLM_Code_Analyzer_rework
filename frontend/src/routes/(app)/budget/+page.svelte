<script lang="ts">
	// My spend (spec 10.1, 9.8): period totals billed and at list price against the caps that apply
	// (incl. 5 h / week quota windows), spend per study against its cap, and the spend caps themselves.
	import { onMount } from 'svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import { rowLink, tableFrame } from '$lib/components/table';
	import { formatApiError } from '$lib/api/core';
	import { getMyBudget } from '$lib/api/v3/budget';
	import { getMe } from '$lib/api/users';
	import type { BudgetMeOut } from '$lib/api/v3/types';
	import CapsCard from '$lib/components/studies/budget/CapsCard.svelte';
	import { refreshSpendChip } from '$lib/components/studies/spend';
	import { STUDY_STATUS_TONES, formatPct, formatTokenCount, formatUsd, spendFraction } from '$lib/components/studies/format';
	import Wallet from '@lucide/svelte/icons/wallet';

	let data = $state<BudgetMeOut | null>(null);
	let error = $state('');
	let isStaff = $state(false);

	const PERIOD: Record<string, string> = { '5h': 'Last 5 hours', day: 'Today', week: 'This week', month: 'This month' };

	async function load() {
		error = '';
		try {
			data = await getMyBudget();
		} catch (e) {
			error = formatApiError(e, 'Could not load your spend.');
		}
	}

	onMount(() => {
		void load();
		getMe()
			.then((me) => (isStaff = !!(me as { is_staff?: boolean }).is_staff))
			.catch(() => {});
	});

	const value = (unit: string, v: number | null | undefined) => (unit === 'tokens' ? formatTokenCount(v) : formatUsd(v));
</script>

<div class="space-y-4">
	<PageHeader title="Budget" description="What you spent, billed and at list price, against your caps; and what each experiment spent.">
		{#if data}
			{@const month = data.periods.find((p) => p.period === 'month')}
			<div class="header-stats">
				<span class="header-stat"><b>{formatUsd(month?.spent ?? null)}</b> billed this month</span>
				<span class="header-stat"><b>{formatUsd(month?.spent_list ?? null)}</b> at list price</span>
				<span class="header-stat"><b>{data.studies.length}</b> experiment{data.studies.length === 1 ? '' : 's'} spending</span>
				<span class="header-stat"><b>{data.periods.filter((p) => p.cap !== null).length}</b> caps</span>
			</div>
		{/if}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={load} />{/if}
	{#if !data && !error}
		<LoadingState label="loading your spend" />
	{:else if data}
		<section class="space-y-3">
			<SectionHeading title="Periods" size="sm" />
			<dl class="fig-strip">
				{#each data.periods as p (`${p.period}-${p.unit}-${p.cap_id ?? 'none'}`)}
					{@const used = p.unit === 'tokens' ? p.tokens : p.unit === 'list_usd' ? p.spent_list : p.spent}
					{@const fraction = spendFraction(p.used ?? used, p.cap)}
					<div class="fig-cell" data-tone={fraction === null ? undefined : fraction >= 1 ? 'bad' : fraction >= 0.8 ? 'warn' : 'good'}>
						<dt class="fig-label">{PERIOD[p.period] ?? p.period}{p.scope && p.scope !== 'user' ? ` · ${p.scope} cap` : ''}</dt>
						<dd class="fig-value">{value(p.unit, p.used ?? used)}</dd>
						{#if fraction !== null}
							<dd class="h-1 max-w-36 overflow-hidden rounded-full bg-muted" aria-hidden="true">
								<span class="block h-full rounded-full bg-primary" style="width: {Math.min(100, fraction * 100)}%"></span>
							</dd>
						{/if}
						<dd class="fig-sub">
							{#if p.cap !== null}of {value(p.unit, p.cap)} ({formatPct(fraction)}) · {p.action === 'block' ? 'blocks new runs' : 'warns'}{:else}no cap{/if}
						</dd>
						<dd class="fig-sub">billed {formatUsd(p.spent)} · list {formatUsd(p.spent_list)}</dd>
					</div>
				{/each}
			</dl>
		</section>

		<section class="space-y-3">
			<SectionHeading title="Experiments" size="sm" count={data.studies.length} />
			{#if data.studies.length === 0}
				<EmptyState compact icon={Wallet} title="No experiment has spent anything yet" />
			{:else}
				<Card.Root class="gap-0 overflow-clip py-0">
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead><tr><th>Experiment</th><th>Status</th><th class="num">Billed</th><th class="num">List price</th><th class="num">Cap</th><th class="num">Used</th></tr></thead>
							<tbody>
								{#each data.studies as s (s.id)}
									{@const f = spendFraction(s.cap_basis === 'list' ? s.spent_list : s.spent, s.cap)}
									<tr class="row-link" onclick={rowLink(`/experiments/${s.id}?tab=operations`)}>
										<td><a class="font-medium hover:underline" href="/experiments/{s.id}?tab=operations">{s.name}</a></td>
										<td><StatusBadge status={s.status} tone={STUDY_STATUS_TONES[s.status]} /></td>
										<td class="num">{formatUsd(s.spent)}</td>
										<td class="num">{formatUsd(s.spent_list)}</td>
										<td class="num">{s.cap === null ? '—' : formatUsd(s.cap)}</td>
										<td class="num" title={s.cap_basis === 'list' ? 'Of the cap, at list price' : 'Of the cap, billed'}>
											<span class="inline-flex items-center justify-end gap-2">
												{#if f !== null}<span class="block h-1 w-14 overflow-hidden rounded-full bg-muted" aria-hidden="true"><span class="block h-full rounded-full {f >= 0.8 ? 'bg-warning' : 'bg-primary'}" style="width: {Math.min(100, f * 100)}%"></span></span>{/if}
												{formatPct(f)}
											</span>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Root>
			{/if}
		</section>
	{/if}

	<CapsCard {isStaff} onchanged={() => { void load(); refreshSpendChip(); }} />
</div>
