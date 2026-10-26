<script lang="ts">
	/**
	 * Numeric score cell (`<td>`) for 0–1 scores shown as 0–100: a tabular
	 * number over a thin meter. Hue stays neutral — a mid score is not an error —
	 * and only the best value in the column (`best`) gets the primary color.
	 *
	 *   <ScoreCell value={row.mss_score} best={row.mss_score === colMax.mss} />
	 *
	 * Props: `value` (0–1 or null → "—"), `best`, `strong` (bolder headline column),
	 * `title`, `sub` (small second line, e.g. "n=13"), `class`.
	 */
	import { cn } from '$lib/utils';

	interface Props {
		value: number | null | undefined;
		best?: boolean;
		strong?: boolean;
		title?: string;
		sub?: string;
		class?: string;
	}

	let { value, best = false, strong = false, title, sub, class: className }: Props = $props();

	const pct = $derived(value == null ? null : Math.max(0, Math.min(1, value)) * 100);
</script>

<td class={cn('num', className)} {title}>
	{#if pct == null}
		<span class="text-muted-foreground">—</span>
	{:else}
		<div class="ml-auto flex w-16 flex-col items-end gap-1">
			<span
				class={cn(
					'leading-none',
					strong ? 'text-sm font-semibold' : 'text-xs',
					pct === 0 && 'text-muted-foreground',
					best && pct > 0 && 'font-semibold text-primary'
				)}>{pct.toFixed(1)}</span
			>
			<span class="block h-[3px] w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
				<span
					class={cn('block h-full rounded-full', best ? 'bg-primary' : 'bg-muted-foreground/45')}
					style:width="{pct}%"
				></span>
			</span>
			{#if sub}<span class="cell-sub text-[11px] leading-none text-muted-foreground">{sub}</span>{/if}
		</div>
	{/if}
</td>
