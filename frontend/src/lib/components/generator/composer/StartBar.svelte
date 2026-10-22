<script lang="ts">
	/**
	 * StartBar — sticky bottom bar while the composer is single-column (its
	 * `@container/composer` is narrower than 56rem, so the Run summary sits under
	 * the form): run count, cost bound, error count, Start.
	 *
	 * Props: `runs` (null = not known yet), `maxCost` (USD upper bound, optional),
	 * `errors`, `label` (Start button text), `canStart`, `busy`, `reason` (why Start
	 * is disabled), `onstart()`, `summaryHref` (anchor of the full summary).
	 */
	import { Button } from '$lib/components/ui/button';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';

	interface Props {
		runs: number | null;
		maxCost?: number | null;
		errors: number;
		label: string;
		canStart: boolean;
		busy?: boolean;
		reason?: string;
		summaryHref?: string;
		onstart: () => void;
	}

	let { runs, maxCost = null, errors, label, canStart, busy = false, reason = '', summaryHref, onstart }: Props =
		$props();
</script>

<div
	class="sticky bottom-0 z-20 -mx-3 border-t border-border bg-card px-3 py-2 sm:-mx-4 sm:px-4 md:-mx-5 md:px-5 @4xl/composer:hidden"
	data-slot="start-bar"
>
	<div class="flex items-center gap-3">
		<div class="min-w-0 flex-1 text-xs" aria-live="polite">
			<p class="flex flex-wrap items-baseline gap-x-2">
				<span class="font-semibold text-foreground tabular-nums">{runs == null ? '—' : runs} run{runs === 1 ? '' : 's'}</span>
				{#if maxCost != null}
					<span class="text-muted-foreground tabular-nums">≤ ${maxCost.toFixed(2)}</span>
				{/if}
				{#if errors > 0}
					<span class="font-medium text-danger-fg tabular-nums">{errors} error{errors === 1 ? '' : 's'}</span>
				{/if}
			</p>
			{#if reason && !canStart}
				<p class="truncate text-muted-foreground">
					{#if summaryHref}<a href={summaryHref} class="underline-offset-2 hover:underline">{reason}</a>{:else}{reason}{/if}
				</p>
			{/if}
		</div>
		<Button size="sm" class="shrink-0" disabled={!canStart || busy} onclick={onstart}>
			{#if busy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}
			{label}
		</Button>
	</div>
</div>
