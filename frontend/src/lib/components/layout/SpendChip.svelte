<script lang="ts">
	/**
	 * SpendChip — the app header's spend indicator (spec 10.1, 4.7; `GET /budget/header/`): spend in the
	 * tightest capped period against its cap ("$3.20 / $20 · month"), linking to `/budget`. When any study
	 * is paused for money (`budget`, `quota` or `spend_cap`) it turns into a warning badge naming how many.
	 * Refreshes every 60 s while the tab is visible, on window focus, and after `refreshSpendChip()` (`$lib/components/studies/spend`).
	 * Silent on errors (it is informational; the Budget page shows the details).
	 */
	import { onMount } from 'svelte';
	import { getBudgetHeader } from '$lib/api/v3/budget';
	import type { BudgetHeaderOut } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import { BUDGET_PAUSE_REASONS, formatTokenCount, formatUsd, pauseLabel, spendFraction } from '$lib/components/studies/format';
	import Wallet from '@lucide/svelte/icons/wallet';
	import CirclePause from '@lucide/svelte/icons/circle-pause';

	const REFRESH_MS = 60_000;
	const PERIOD: Record<string, string> = { '5h': '5 h', day: 'today', week: 'week', month: 'month' };

	let data = $state<BudgetHeaderOut | null>(null);
	let inFlight = false;

	async function load() {
		if (inFlight) return;
		inFlight = true;
		try {
			data = await getBudgetHeader();
		} catch {
			// informational only
		} finally {
			inFlight = false;
		}
	}

	onMount(() => {
		void load();
		const timer = setInterval(() => {
			if (!document.hidden) void load();
		}, REFRESH_MS);
		const onFocus = () => void load();
		const onRefresh = () => void load();
		window.addEventListener('focus', onFocus);
		window.addEventListener('llmlab:spend-changed', onRefresh);
		return () => {
			clearInterval(timer);
			window.removeEventListener('focus', onFocus);
			window.removeEventListener('llmlab:spend-changed', onRefresh);
		};
	});

	const paused = $derived((data?.paused_studies ?? []).filter((s) => BUDGET_PAUSE_REASONS.has(s.reason)));
	const fraction = $derived(spendFraction(data?.period_spend, data?.period_cap));
	const money = (v: number | null | undefined) => (data?.unit === 'tokens' ? formatTokenCount(v) : formatUsd(v));
	const text = $derived(
		data ? `${money(data.period_spend)}${data.period_cap !== null ? ` / ${money(data.period_cap)}` : ''} · ${PERIOD[data.period] ?? data.period}` : '',
	);
	const title = $derived(
		paused.length
			? `${paused.length} stud${paused.length === 1 ? 'y' : 'ies'} paused: ${[...new Set(paused.map((p) => pauseLabel(p.reason)))].join(', ')}`
			: data
				? `Spend this ${PERIOD[data.period] ?? data.period}${data.unit === 'list_usd' ? ' (list price)' : ''}${data.period_cap === null ? ' (no cap set)' : ''}`
				: '',
	);
</script>

{#if data}
	<a
		href="/budget"
		{title}
		class={cn(
			'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium whitespace-nowrap transition-colors',
			paused.length
				? toneClasses.warning
				: fraction !== null && fraction >= 0.8
					? toneClasses.warning
					: 'border-border bg-card text-muted-foreground hover:border-primary/50 hover:text-foreground',
		)}
		aria-label={paused.length ? title : `Spend: ${text}`}
	>
		{#if paused.length}
			<CirclePause class="size-3.5" aria-hidden="true" />
			{paused.length} paused · budget
		{:else}
			<Wallet class="size-3.5" aria-hidden="true" />
			<span class="font-mono tabular-nums">{text}</span>
		{/if}
	</a>
{/if}
