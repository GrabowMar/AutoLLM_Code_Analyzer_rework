<script lang="ts" module>
	import type { StepId } from './steps';

	/** How a step reads in the stepper. */
	export type StepState = 'done' | 'todo' | 'attention' | 'error' | 'idle';

	export interface StepperItem {
		id: StepId;
		label: string;
		/** What the step holds now ("3 models", "Frugal · $5 cap"); empty hides the line. */
		summary: string;
		state: StepState;
		/** Holds a required choice (shown until it is made). */
		required: boolean;
	}
</script>

<script lang="ts">
	/**
	 * BuilderStepper — the builder's progress and navigation: every step in order with its number (a
	 * check once complete, an alert while it has an error or an open required choice the user already
	 * walked past), its label and a one-line summary of what it holds. Non-linear: any step can be
	 * opened. The current step carries `aria-current="step"` and a primary underline; on narrow screens
	 * the row scrolls and keeps the current step in view.
	 *
	 * Props: `items`, `current`, `onselect(id)`.
	 */
	import { cn } from '$lib/utils';
	import Check from '@lucide/svelte/icons/check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';

	interface Props {
		items: StepperItem[];
		current: StepId;
		onselect: (id: StepId) => void;
	}

	let { items, current, onselect }: Props = $props();

	let list = $state<HTMLOListElement | null>(null);
	const doneCount = $derived(items.filter((i) => i.state === 'done').length);

	$effect(() => {
		void current;
		const el = list?.querySelector<HTMLElement>('[aria-current="step"]');
		el?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
	});

	const STATE_TEXT: Record<StepState, string> = {
		done: 'complete',
		todo: 'required, not done yet',
		attention: 'needs attention',
		error: 'has errors',
		idle: '',
	};
</script>

<nav aria-label="Experiment steps" class="bs">
	<ol bind:this={list} class="bs-list no-scrollbar">
		{#each items as item, i (item.id)}
			{@const active = item.id === current}
			<li class="bs-item" data-state={item.state} data-active={active}>
				<button
					type="button"
					class="bs-button"
					aria-current={active ? 'step' : undefined}
					title={item.summary ? `${item.label}: ${item.summary}` : item.label}
					onclick={() => onselect(item.id)}
				>
					<span class={cn('bs-dot', active && 'bs-dot-active')} aria-hidden="true">
						{#if item.state === 'error' || item.state === 'attention'}
							<CircleAlert class="size-3.5" />
						{:else if item.state === 'done' && !active}
							<Check class="size-3.5" strokeWidth={2.5} />
						{:else}
							{i + 1}
						{/if}
					</span>
					<span class="bs-text">
						<span class="bs-label">
							{item.label}
							{#if item.required && item.state !== 'done'}<span class="bs-req">required</span>{/if}
						</span>
						<span class="bs-summary">{item.summary || ' '}</span>
					</span>
					{#if STATE_TEXT[item.state]}<span class="sr-only">({STATE_TEXT[item.state]})</span>{/if}
				</button>
			</li>
		{/each}
	</ol>
	<div class="bs-progress" aria-hidden="true">
		<span style="width: {(doneCount / Math.max(1, items.length - 1)) * 100}%"></span>
	</div>
</nav>

<style>
	.bs {
		position: relative;
	}
	.bs-list {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: minmax(8.5rem, 1fr);
		overflow-x: auto;
		scroll-snap-type: x proximity;
	}
	.bs-item {
		position: relative;
		min-width: 0;
		scroll-snap-align: start;
	}
	.bs-button {
		position: relative;
		z-index: 1;
		display: flex;
		width: 100%;
		min-height: 3.5rem;
		cursor: pointer;
		align-items: flex-start;
		gap: 0.5rem;
		padding: 0.5625rem 0.75rem 0.625rem;
		text-align: left;
		border-bottom: 2px solid transparent;
		transition:
			background-color 150ms,
			border-color 150ms;
	}
	.bs-button:hover {
		background: color-mix(in oklab, var(--muted) 55%, transparent);
	}
	.bs-button:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.bs-item[data-active='true'] .bs-button {
		border-bottom-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 6%, transparent);
	}
	.bs-dot {
		display: flex;
		width: 1.625rem;
		height: 1.625rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 9999px;
		border: 1px solid var(--border);
		background: var(--card);
		font-family: var(--font-mono);
		font-size: 0.75rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		color: var(--muted-foreground);
		transition:
			background-color 200ms,
			border-color 200ms,
			color 200ms;
	}
	.bs-item[data-state='done'] .bs-dot {
		border-color: color-mix(in oklab, var(--success) 55%, transparent);
		background: color-mix(in oklab, var(--success) 14%, var(--card));
		color: var(--success-fg);
	}
	.bs-item[data-state='attention'] .bs-dot {
		border-color: color-mix(in oklab, var(--warning) 60%, transparent);
		background: color-mix(in oklab, var(--warning) 14%, var(--card));
		color: var(--warning-fg);
	}
	.bs-item[data-state='error'] .bs-dot {
		border-color: color-mix(in oklab, var(--destructive) 60%, transparent);
		background: color-mix(in oklab, var(--destructive) 14%, var(--card));
		color: var(--danger-fg);
	}
	.bs-dot-active {
		border-color: var(--primary) !important;
		background: var(--primary) !important;
		color: var(--primary-foreground) !important;
		box-shadow: 0 0 0 3px color-mix(in oklab, var(--primary) 22%, transparent);
	}
	.bs-text {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.0625rem;
		padding-top: 0.125rem;
	}
	.bs-label {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		white-space: nowrap;
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--muted-foreground);
	}
	.bs-item[data-active='true'] .bs-label,
	.bs-item[data-state='done'] .bs-label {
		color: var(--foreground);
	}
	.bs-req {
		font-family: var(--font-mono);
		font-size: 0.5625rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--warning-fg);
	}
	.bs-summary {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.bs-progress {
		height: 2px;
		background: transparent;
	}
	.bs-progress > span {
		display: block;
		height: 100%;
		max-width: 100%;
		background: color-mix(in oklab, var(--success) 70%, transparent);
		transition: width 300ms ease-out;
	}
	@media (prefers-reduced-motion: reduce) {
		.bs-progress > span,
		.bs-dot,
		.bs-button {
			transition: none;
		}
	}
</style>
