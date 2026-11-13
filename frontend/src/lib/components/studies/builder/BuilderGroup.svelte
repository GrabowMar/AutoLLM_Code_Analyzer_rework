<script lang="ts">
	/**
	 * BuilderGroup — one titled group inside a builder step ("Budget", "Seeds & ordering"): a small
	 * heading with an optional one-line description and right-aligned `aside`, then the body. With
	 * `collapsible` it is a native `<details>` (keyboard and screen readers work without script) folded
	 * to its `summary` line until opened; `open` starts it unfolded (e.g. when it already holds a
	 * non-default value).
	 *
	 * Props: `title`, `description`, `summary`, `collapsible`, `open`, `aside`, `children`, `id`.
	 */
	import type { Snippet } from 'svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';

	interface Props {
		title: string;
		description?: string;
		/** Collapsed one-liner: what applies right now. */
		summary?: string;
		collapsible?: boolean;
		open?: boolean;
		aside?: Snippet;
		children: Snippet;
		id?: string;
	}

	let { title, description, summary, collapsible = false, open = false, aside, children, id }: Props = $props();
</script>

{#if collapsible}
	<details class="bg-group bg-collapsible" {id} {open}>
		<summary class="bg-summary">
			<ChevronRight class="bg-chevron size-4 shrink-0" aria-hidden="true" />
			<span class="min-w-0 flex-1">
				<span class="bg-title">{title}</span>
				{#if summary}<span class="bg-line" title={summary}>{summary}</span>{/if}
			</span>
		</summary>
		<div class="bg-body">
			{#if description}<p class="bg-desc">{description}</p>{/if}
			{@render children()}
		</div>
	</details>
{:else}
	<div class="bg-group" {id}>
		<div class="bg-head">
			<div class="min-w-0 flex-1">
				<h3 class="bg-title">{title}</h3>
				{#if description}<p class="bg-desc">{description}</p>{/if}
			</div>
			{#if aside}<div class="flex shrink-0 flex-wrap items-center gap-2">{@render aside()}</div>{/if}
		</div>
		{@render children()}
	</div>
{/if}

<style>
	.bg-group {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.75rem;
	}
	.bg-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 0.5rem 1rem;
		padding-bottom: 0.5rem;
		border-bottom: 1px solid color-mix(in oklab, var(--foreground) 14%, transparent);
	}
	.bg-title {
		display: block;
		font-size: 0.875rem;
		font-weight: 600;
		color: var(--foreground);
	}
	.bg-desc {
		margin-top: 0.125rem;
		font-size: 0.75rem;
		line-height: 1.5;
		color: var(--muted-foreground);
	}
	.bg-collapsible {
		gap: 0;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--muted) 18%, transparent);
	}
	.bg-summary {
		display: flex;
		cursor: pointer;
		list-style: none;
		align-items: flex-start;
		gap: 0.5rem;
		padding: 0.625rem 0.75rem;
		border-radius: var(--radius);
		transition: background-color 150ms;
	}
	.bg-summary::-webkit-details-marker {
		display: none;
	}
	.bg-summary:hover {
		background: color-mix(in oklab, var(--muted) 45%, transparent);
	}
	.bg-summary:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.bg-summary :global(.bg-chevron) {
		margin-top: 0.0625rem;
		color: var(--muted-foreground);
		transition: transform 150ms;
	}
	.bg-collapsible[open] > .bg-summary :global(.bg-chevron) {
		transform: rotate(90deg);
	}
	.bg-line {
		display: block;
		margin-top: 0.125rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.bg-collapsible[open] .bg-line {
		display: none;
	}
	.bg-body {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 0.25rem 0.75rem 0.875rem 2.25rem;
	}
	@media (prefers-reduced-motion: reduce) {
		.bg-summary :global(.bg-chevron) {
			transition: none;
		}
	}
</style>
