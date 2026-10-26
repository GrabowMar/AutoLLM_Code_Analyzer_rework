<script lang="ts" module>
	export interface StepDef {
		key: string;
		label: string;
		/** Short state line under the label ("3 targets", "2 blocking"). */
		hint?: string;
		/** Reachable (clickable) from here. */
		enabled: boolean;
		/** Completed (check mark). */
		done: boolean;
		/** Needs attention (e.g. blocking preflight problems). */
		warn?: boolean;
	}
</script>

<script lang="ts">
	/**
	 * StudyStepper — the wizard's step list: numbered steps with done /
	 * current / attention states; reachable steps are buttons. Scrolls
	 * sideways on narrow screens instead of wrapping.
	 *
	 * Props: `steps`, `current` (key), `onselect(key)`.
	 */
	import { cn } from '$lib/utils';
	import Check from '@lucide/svelte/icons/check';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		steps: StepDef[];
		current: string;
		onselect?: (key: string) => void;
	}

	let { steps, current, onselect }: Props = $props();

	let nav = $state<HTMLElement | null>(null);

	// Keep the current step visible when the list scrolls sideways (phones).
	$effect(() => {
		void current;
		const el = nav?.querySelector<HTMLElement>('[aria-current="step"]');
		if (el && nav && nav.scrollWidth > nav.clientWidth) {
			nav.scrollTo({ left: el.offsetLeft - 8, behavior: 'auto' });
		}
	});
</script>

<nav
	bind:this={nav}
	aria-label="Study setup steps"
	class="no-scrollbar relative -mx-1 overflow-x-auto px-1"
>
	<ol class="flex min-w-max items-stretch gap-1.5">
		{#each steps as step, i (step.key)}
			{@const active = step.key === current}
			<li class="flex items-center gap-1.5">
				{#if i > 0}<span class="h-px w-4 bg-border sm:w-6" aria-hidden="true"></span>{/if}
				<button
					type="button"
					class={cn(
						'flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-left transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50',
						active
							? 'border-primary/60 bg-primary/10'
							: 'border-border bg-card hover:border-primary/40'
					)}
					aria-current={active ? 'step' : undefined}
					disabled={!step.enabled}
					onclick={() => onselect?.(step.key)}
				>
					<span
						class={cn(
							'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold tabular-nums',
							step.warn
								? 'border-warning/50 bg-warning/15 text-warning-fg'
								: step.done
									? 'border-success/50 bg-success/15 text-success-fg'
									: active
										? 'border-primary bg-primary text-primary-foreground'
										: 'border-border text-muted-foreground'
						)}
					>
						{#if step.warn}
							<TriangleAlert class="h-3.5 w-3.5" aria-hidden="true" />
						{:else if step.done && !active}
							<Check class="h-3.5 w-3.5" aria-hidden="true" />
						{:else}
							{i + 1}
						{/if}
					</span>
					<span class="flex flex-col leading-tight">
						<span
							class={cn(
								'text-sm font-medium',
								active ? 'text-foreground' : 'text-muted-foreground'
							)}>{step.label}</span
						>
						{#if step.hint}<span class="text-[11px] text-muted-foreground">{step.hint}</span>{/if}
					</span>
					<span class="sr-only"
						>{step.done ? '(done)' : ''}{step.warn ? '(needs attention)' : ''}</span
					>
				</button>
			</li>
		{/each}
	</ol>
</nav>
