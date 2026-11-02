<script lang="ts" module>
	export interface StepItem {
		id: string;
		title: string;
		complete: boolean;
		/** The server refused this block on the last submit. */
		hasErrors: boolean;
	}
</script>

<script lang="ts">
	/**
	 * StepNav — the block stepper's index: one numbered chip per block with a
	 * check mark once the block is complete and an alert mark when the last
	 * submit found problems. The current block is `aria-current="step"`.
	 *
	 * Props: `steps`, `active` (index), `onselect(index)`.
	 */
	import Check from '@lucide/svelte/icons/check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { cn } from '$lib/utils';

	interface Props {
		steps: StepItem[];
		active: number;
		onselect: (index: number) => void;
	}

	let { steps, active, onselect }: Props = $props();
	const doneCount = $derived(steps.filter((s) => s.complete).length);
</script>

<nav aria-label="Evaluation sections" class="min-w-0">
	<div class="mb-1.5 flex items-baseline justify-between gap-2">
		<span class="section-label">Sections</span>
		<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{doneCount}/{steps.length} complete</span>
	</div>
	<ol class="no-scrollbar flex gap-1 overflow-x-auto pb-0.5">
		{#each steps as step, i (step.id)}
			<li class="shrink-0">
				<button
					type="button"
					class={cn(
						'flex h-8 max-w-[13rem] cursor-pointer items-center gap-1.5 rounded-md border px-2 text-xs transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
						i === active
							? 'border-primary bg-primary/10 font-semibold text-foreground'
							: 'border-border bg-card text-muted-foreground hover:border-primary/50 hover:text-foreground',
						step.hasErrors && i !== active && 'border-destructive/50'
					)}
					aria-current={i === active ? 'step' : undefined}
					onclick={() => onselect(i)}
				>
					<span
						class={cn(
							'flex size-4 shrink-0 items-center justify-center rounded-full font-mono text-[11px] tabular-nums',
							step.hasErrors
								? 'bg-destructive/15 text-danger-fg'
								: step.complete
									? 'bg-success/15 text-success-fg'
									: 'bg-muted text-muted-foreground'
						)}
						aria-hidden="true"
					>
						{#if step.hasErrors}
							<CircleAlert class="size-3" />
						{:else if step.complete}
							<Check class="size-3" strokeWidth={3} />
						{:else}
							{i + 1}
						{/if}
					</span>
					<span class="truncate">{step.title}</span>
					<span class="sr-only">
						{step.hasErrors ? '(needs attention)' : step.complete ? '(complete)' : '(not complete)'}
					</span>
				</button>
			</li>
		{/each}
	</ol>
</nav>
