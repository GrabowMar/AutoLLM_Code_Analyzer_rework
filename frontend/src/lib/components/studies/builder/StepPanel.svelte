<script lang="ts">
	/**
	 * StepPanel — the frame of the open builder step: "Step 3 of 7" eyebrow, the step's question as the
	 * heading (focused when the step opens, so keyboard and screen-reader users land on it), its lead
	 * sentence, an optional `aside` (counts, bulk actions) and the body; then the footer with Back and
	 * Continue. `blockers` (open required choices of this step) show above the footer once the user
	 * tried to continue past them — never before.
	 *
	 * Props: `index` (0-based), `total`, `title`, `lead`, `headingId`, `aside`, `children`, `footer`
	 * (replaces Continue, e.g. on Review), `backLabel` + `onback`, `nextLabel` + `onnext`, `blockers`,
	 * `showBlockers`.
	 */
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';

	interface Props {
		index: number;
		total: number;
		title: string;
		lead: string;
		headingId: string;
		aside?: Snippet;
		children: Snippet;
		footer?: Snippet;
		backLabel?: string;
		onback?: () => void;
		nextLabel?: string;
		onnext?: () => void;
		blockers?: string[];
		showBlockers?: boolean;
	}

	let {
		index,
		total,
		title,
		lead,
		headingId,
		aside,
		children,
		footer,
		backLabel,
		onback,
		nextLabel,
		onnext,
		blockers = [],
		showBlockers = false,
	}: Props = $props();
</script>

<section class="sp" aria-labelledby={headingId}>
	<div class="sp-head">
		<div class="min-w-0 flex-1">
			<p class="sp-eyebrow">Step {index + 1} <span aria-hidden="true">/</span><span class="sr-only">of</span> {total}</p>
			<h2 id={headingId} tabindex="-1" class="sp-title">{title}</h2>
			<p class="sp-lead">{lead}</p>
		</div>
		{#if aside}<div class="sp-aside">{@render aside()}</div>{/if}
	</div>

	<div class="sp-body @container">
		{@render children()}
	</div>

	{#if showBlockers && blockers.length > 0}
		<div class="sp-blockers" role="alert">
			<CircleAlert class="mt-px size-4 shrink-0" aria-hidden="true" />
			<p>
				Still required here: {blockers.join('; ')}. You can continue and come back, but the experiment can’t launch until
				{blockers.length === 1 ? 'it is' : 'they are'} done.
			</p>
		</div>
	{/if}

	<div class="sp-foot">
		{#if onback && backLabel}
			<Button variant="ghost" size="sm" onclick={onback}><ArrowLeft />{backLabel}</Button>
		{:else}
			<span></span>
		{/if}
		{#if footer}
			{@render footer()}
		{:else if onnext && nextLabel}
			<Button size="sm" onclick={onnext}>{nextLabel}<ArrowRight /></Button>
		{/if}
	</div>
</section>

<style>
	.sp {
		display: flex;
		flex-direction: column;
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) + 2px);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.sp-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 0.75rem 1rem;
		padding: 1.125rem 1.25rem 1rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 75%, transparent);
	}
	.sp-eyebrow {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		letter-spacing: 0.14em;
		text-transform: uppercase;
		color: var(--primary);
	}
	.sp-title {
		margin-top: 0.25rem;
		font-family: var(--font-display);
		font-size: 1.375rem;
		line-height: 1.2;
		font-weight: 600;
		text-wrap: balance;
	}
	.sp-title:focus {
		outline: none;
	}
	.sp-title:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 3px;
		border-radius: 2px;
	}
	.sp-lead {
		margin-top: 0.375rem;
		max-width: 70ch;
		font-size: 0.8125rem;
		line-height: 1.55;
		color: var(--muted-foreground);
	}
	.sp-aside {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}
	.sp-body {
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
		padding: 1.25rem;
	}
	/* Labels that toggle a checkbox are part of its target: pointer cursor and at least 24px tall. */
	.sp-body :global(label:has(> [role='checkbox'], > [data-slot='checkbox'])) {
		cursor: pointer;
		min-height: 1.5rem;
	}
	.sp-blockers {
		display: flex;
		gap: 0.5rem;
		margin: 0 1.25rem 1rem;
		padding: 0.625rem 0.75rem;
		border: 1px solid color-mix(in oklab, var(--warning) 40%, transparent);
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--warning) 9%, transparent);
		font-size: 0.75rem;
		line-height: 1.5;
		color: var(--warning-fg);
	}
	.sp-foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.75rem 1.25rem;
		border-top: 1px solid color-mix(in oklab, var(--border) 75%, transparent);
		background: color-mix(in oklab, var(--muted) 30%, transparent);
		border-radius: 0 0 calc(var(--radius) + 2px) calc(var(--radius) + 2px);
	}
	@media (max-width: 640px) {
		.sp-head,
		.sp-body {
			padding-inline: 1rem;
		}
		.sp-foot {
			padding-inline: 1rem;
		}
	}
</style>
