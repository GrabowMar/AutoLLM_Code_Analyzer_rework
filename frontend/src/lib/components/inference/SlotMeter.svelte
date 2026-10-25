<script lang="ts">
	/**
	 * SlotMeter — how many of an endpoint's (or a lane's) slots are busy.
	 *
	 * Props:
	 * - `used`: busy slots, including `heldByOthers`.
	 * - `total`: slot count (0 = unknown / unlimited: only the count is shown).
	 * - `heldByOthers`: the part of `used` held by other experiments or users;
	 *   drawn hatched, never with details.
	 * - `label`: accessible name and visible prefix (e.g. "Slots"); '' hides the prefix.
	 * - `showCount` (default true): trailing "2/4".
	 * - `size`: 'sm' (8 px squares) | 'default' (10 px).
	 * - `class`.
	 * Up to 16 slots render as squares; more as a segmented bar.
	 */
	import { cn } from '$lib/utils';

	interface Props {
		used: number;
		total: number;
		heldByOthers?: number;
		label?: string;
		showCount?: boolean;
		size?: 'sm' | 'default';
		class?: string;
	}

	let { used, total, heldByOthers = 0, label = 'Slots', showCount = true, size = 'default', class: className }: Props =
		$props();

	const MAX_SQUARES = 16;

	const safeTotal = $derived(Math.max(0, Math.floor(total || 0)));
	const busy = $derived(Math.max(0, Math.floor(used || 0)));
	const others = $derived(Math.min(Math.max(0, Math.floor(heldByOthers || 0)), busy));
	const own = $derived(busy - others);
	const over = $derived(safeTotal > 0 && busy > safeTotal);
	const squares = $derived(
		safeTotal > 0 && safeTotal <= MAX_SQUARES
			? Array.from({ length: safeTotal }, (_, i) => (i < own ? 'own' : i < busy ? 'others' : 'free'))
			: []
	);
	const pct = (n: number) => (safeTotal > 0 ? Math.min(100, (n / safeTotal) * 100) : 0);
	const description = $derived(
		safeTotal > 0
			? `${busy} of ${safeTotal} busy${others > 0 ? `, ${others} by other experiments` : ''}`
			: `${busy} busy`
	);
	const square = $derived(size === 'sm' ? 'size-2' : 'size-2.5');
	const hatch =
		'bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_1.5px,transparent_1.5px_3.5px)] opacity-70';
</script>

<span
	class={cn('inline-flex min-w-0 items-center gap-1.5 text-[11px] tabular-nums text-muted-foreground', className)}
	role="meter"
	aria-label={label || 'Slots'}
	aria-valuemin={0}
	aria-valuemax={safeTotal || undefined}
	aria-valuenow={busy}
	aria-valuetext={description}
	title={description}
>
	{#if label}<span class="shrink-0">{label}</span>{/if}
	{#if squares.length > 0}
		<span class="flex flex-wrap items-center gap-0.5" aria-hidden="true">
			{#each squares as kind, i (i)}
				<span
					class={cn(
						'shrink-0 rounded-[2px] border',
						square,
						kind === 'own' && 'border-primary bg-primary',
						kind === 'others' && cn('border-muted-foreground/60', hatch),
						kind === 'free' && 'border-border bg-surface-2'
					)}
				></span>
			{/each}
		</span>
	{:else if safeTotal > 0}
		<span class="relative flex h-2 w-20 shrink-0 overflow-hidden rounded-full border border-border bg-surface-2" aria-hidden="true">
			<span class="h-full bg-primary" style="width: {pct(own)}%"></span>
			<span class={cn('h-full', hatch)} style="width: {pct(others)}%"></span>
		</span>
	{/if}
	{#if showCount}
		<span class={cn('shrink-0 font-medium', over ? 'text-warning-fg' : 'text-foreground')}>
			{busy}{#if safeTotal > 0}/{safeTotal}{/if}
		</span>
	{/if}
</span>
