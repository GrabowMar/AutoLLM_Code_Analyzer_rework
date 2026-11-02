<script lang="ts">
	/**
	 * ProgressBar — a thin neutral progress bar (`role="progressbar"`) for a
	 * participant's progress through a task. Ink on a muted track; the number is
	 * always printed next to it by the caller.
	 *
	 * Props: `fraction` (0..1; null = unknown, nothing drawn), `label`
	 * (accessible name), `valueText` (e.g. "3 of 10 apps"), `class`.
	 */
	import { cn } from '$lib/utils';

	interface Props {
		fraction: number | null;
		label: string;
		valueText?: string;
		class?: string;
	}

	let { fraction, label, valueText, class: className }: Props = $props();

	const pct = $derived(fraction == null ? null : Math.round(Math.min(1, Math.max(0, fraction)) * 100));
</script>

{#if pct != null}
	<div
		class={cn('h-1.5 w-full overflow-hidden rounded-full bg-muted', className)}
		role="progressbar"
		aria-label={label}
		aria-valuemin={0}
		aria-valuemax={100}
		aria-valuenow={pct}
		aria-valuetext={valueText}
	>
		<div class="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none" style:width="{pct}%"></div>
	</div>
{/if}
