<script lang="ts">
	/**
	 * LiveSpendStrip — the study's live spend (spec 10.3 §6, 4.7): spent / reserved / cap with a meter,
	 * billed and list price side by side, burn rate, ETA, blocks, and cost per usable and per tested
	 * sample. Updated in place from SSE `spend` events (`StudyLive`); a value the server does not know is
	 * "—", never 0.
	 *
	 * Props: `budget` (`StudyBudgetOut`), `liveBurnRate` (USD/h from recent events, preferred when known),
	 * `alertFraction`, `compact` (one line, for the masthead).
	 */
	import type { StudyBudgetOut } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import { formatPct, formatSeconds, formatUsd, pauseLabel, spendFraction } from '../format';

	interface Props {
		budget: StudyBudgetOut | null;
		liveBurnRate?: number | null;
		alertFraction?: number | null;
		compact?: boolean;
	}

	let { budget, liveBurnRate = null, alertFraction = null, compact = false }: Props = $props();

	const spentFrac = $derived(spendFraction(budget?.spent, budget?.cap) ?? 0);
	const reservedFrac = $derived(spendFraction(budget?.reserved, budget?.cap) ?? 0);
	const burn = $derived(liveBurnRate ?? budget?.burn_rate_usd_per_h ?? null);
	const usable = $derived(budget?.by_condition.reduce((n, c) => n + c.usable, 0) ?? 0);
	const tested = $derived(budget?.by_condition.reduce((n, c) => n + c.tested, 0) ?? 0);
	const perUsable = $derived(usable > 0 && budget?.spent != null ? budget.spent / usable : null);
	const perTested = $derived(tested > 0 && budget?.spent != null ? budget.spent / tested : null);
	const crossed = $derived(alertFraction ?? Math.max(0, ...(budget?.alerts ?? []).filter((a) => a.crossed).map((a) => a.fraction)));
</script>

{#if !budget}
	<p class="text-xs text-muted-foreground">Spend not loaded.</p>
{:else if compact}
	<p class="flex flex-wrap items-baseline gap-x-3 text-xs" aria-live="polite">
		<span><b class="font-mono tabular-nums">{formatUsd(budget.spent)}</b> spent</span>
		<span class="text-muted-foreground">{formatUsd(budget.reserved)} reserved</span>
		<span class="text-muted-foreground">cap {budget.cap === null ? '—' : formatUsd(budget.cap)}</span>
		{#if burn !== null}<span class="text-muted-foreground">{formatUsd(burn)}/h</span>{/if}
	</p>
{:else}
	<div class="space-y-2" aria-live="polite">
		<div class="relative h-2.5 overflow-hidden rounded-sm bg-muted" role="img" aria-label="Spent {formatPct(spentFrac)} and reserved {formatPct(reservedFrac)} of the cap">
			<span class="absolute inset-y-0 left-0 bg-[var(--chart-1)]" style="width: {Math.min(100, spentFrac * 100)}%"></span>
			<span
				class="absolute inset-y-0 bg-[var(--chart-1)] opacity-35"
				style="left: {Math.min(100, spentFrac * 100)}%; width: {Math.max(0, Math.min(100 - spentFrac * 100, reservedFrac * 100))}%"
			></span>
		</div>
		<dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
			<div><dt class="text-muted-foreground">Spent (billed)</dt><dd class="font-mono tabular-nums">{formatUsd(budget.spent_billed ?? budget.spent)}</dd></div>
			<div><dt class="text-muted-foreground">At list price</dt><dd class="font-mono tabular-nums">{formatUsd(budget.spent_list)}</dd></div>
			<div><dt class="text-muted-foreground">Reserved</dt><dd class="font-mono tabular-nums">{formatUsd(budget.reserved)}</dd></div>
			<div>
				<dt class="text-muted-foreground">Cap ({budget.cap_basis})</dt>
				<dd class="font-mono tabular-nums">{budget.cap === null ? '—' : formatUsd(budget.cap)}</dd>
			</div>
			<div><dt class="text-muted-foreground">Remaining</dt><dd class="font-mono tabular-nums">{formatUsd(budget.remaining)}</dd></div>
			<div><dt class="text-muted-foreground">Expected to finish</dt><dd class="font-mono tabular-nums">{formatUsd(budget.expected_remaining)}</dd></div>
			<div><dt class="text-muted-foreground">Burn rate</dt><dd class="font-mono tabular-nums">{burn === null ? '—' : `${formatUsd(burn)}/h`}</dd></div>
			<div><dt class="text-muted-foreground">ETA</dt><dd class="font-mono tabular-nums">{formatSeconds(budget.eta_s)}</dd></div>
			<div><dt class="text-muted-foreground">Per usable sample</dt><dd class="font-mono tabular-nums">{formatUsd(perUsable)}</dd></div>
			<div><dt class="text-muted-foreground">Per tested sample</dt><dd class="font-mono tabular-nums">{formatUsd(perTested)}</dd></div>
			<div>
				<dt class="text-muted-foreground">Blocks</dt>
				<dd class="font-mono tabular-nums">{budget.blocks.complete} / {budget.blocks.total} complete · {budget.blocks.started} started</dd>
			</div>
			<div>
				<dt class="text-muted-foreground">Tokens</dt>
				<dd class="font-mono tabular-nums">{budget.tokens.spent.toLocaleString()}{budget.tokens.cap ? ` / ${budget.tokens.cap.toLocaleString()}` : ''}</dd>
			</div>
		</dl>
		{#if crossed > 0}
			<p class={cn('text-xs', crossed >= 1 ? 'text-danger-fg' : 'text-warning-fg')}>{formatPct(crossed)} of the cap reached.</p>
		{/if}
		{#if budget.status === 'paused' && budget.pause_reason}
			<p class="text-xs text-warning-fg">Paused: {pauseLabel(budget.pause_reason)}.</p>
		{/if}
		<p class="text-[11px] text-muted-foreground">
			When the cap is reached: {budget.on_budget_exhausted.replace(/_/g, ' ')} (budget stops are balanced across units).
		</p>
	</div>
{/if}
