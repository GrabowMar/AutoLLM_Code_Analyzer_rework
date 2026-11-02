<script lang="ts">
	/**
	 * SubmitBar — the stepper's sticky footer: previous / next section, the
	 * autosave state ("Saved 12:03", "Saving…", "Not saved — retry"), Skip and
	 * Submit. Hidden actions in read-only mode.
	 *
	 * Props: `index`, `count`, `saveState`, `savedAt`, `completeCount`,
	 * `readOnly`, `allowSkip`, `submitting`, `onprev`, `onnext`, `onretry`,
	 * `onskip`, `onsubmit`.
	 */
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { Button } from '$lib/components/ui/button';
	import type { SaveState } from './autosave.svelte';
	import { clockTime } from './workbench';

	interface Props {
		index: number;
		count: number;
		saveState: SaveState;
		savedAt: number | string | null;
		completeCount: number;
		readOnly?: boolean;
		allowSkip?: boolean;
		submitting?: boolean;
		onprev: () => void;
		onnext: () => void;
		onretry: () => void;
		onskip: () => void;
		onsubmit: () => void;
	}

	let {
		index,
		count,
		saveState,
		savedAt,
		completeCount,
		readOnly = false,
		allowSkip = false,
		submitting = false,
		onprev,
		onnext,
		onretry,
		onskip,
		onsubmit,
	}: Props = $props();

	const last = $derived(index >= count - 1);
	const allDone = $derived(completeCount >= count);
	const time = $derived(clockTime(savedAt));
</script>

<div class="flex flex-wrap items-center gap-2 border-t border-border bg-card px-3 py-2">
	<div class="flex items-center gap-1">
		<Button variant="ghost" size="sm" disabled={index <= 0} onclick={onprev} aria-label="Previous section">
			<ChevronLeft />
			<span class="hidden sm:inline">Back</span>
		</Button>
		{#if !last}
			<Button variant="outline" size="sm" onclick={onnext}>
				Next
				<ChevronRight />
			</Button>
		{/if}
	</div>

	<p class="min-w-0 flex-1 truncate text-xs text-muted-foreground" aria-live="polite">
		{#if readOnly}
			Read-only
		{:else if saveState === 'saving' || saveState === 'pending'}
			<span class="inline-flex items-center gap-1"><LoaderCircle class="size-3 animate-spin" aria-hidden="true" />Saving…</span>
		{:else if saveState === 'error'}
			<span class="inline-flex items-center gap-1 text-danger-fg">
				<CircleAlert class="size-3.5" aria-hidden="true" />Not saved
				<button type="button" class="cursor-pointer font-medium underline underline-offset-2" onclick={onretry}>Retry</button>
			</span>
		{:else if time}
			<span class="inline-flex items-center gap-1"><CircleCheck class="size-3.5 text-success-fg" aria-hidden="true" />Saved {time}</span>
		{:else}
			Answers save automatically
		{/if}
	</p>

	{#if !readOnly}
		<div class="flex items-center gap-2">
			{#if allowSkip}
				<Button variant="ghost" size="sm" disabled={submitting} onclick={onskip}>Skip…</Button>
			{/if}
			<Button
				size="sm"
				variant={allDone || last ? 'default' : 'outline'}
				disabled={submitting}
				aria-busy={submitting}
				onclick={onsubmit}
				title={allDone ? 'Submit your answers' : `${completeCount} of ${count} sections complete`}
			>
				{#if submitting}<LoaderCircle class="animate-spin" />{/if}
				Submit
			</Button>
		</div>
	{/if}
</div>
