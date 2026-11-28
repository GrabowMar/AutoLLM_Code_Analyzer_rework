<script lang="ts">
	/**
	 * SelectionMark — the glyph of one selection-matrix cell (and of its legend entry). Shape carries the
	 * meaning as well as hue, so the grid reads in greyscale and for colour-blind readers:
	 * solid square = selected and tested · half-filled square = selected, failed (output/build/start) ·
	 * square with a centre dot = selected, another outcome · square with a bar = selected but left out of the
	 * sample (task dropped, or truncated) · square with a cross = attrition · ring = still running ·
	 * dashed outline = no attempt. A dot in the top-right corner: older attempts were superseded.
	 * Decorative (`aria-hidden`): the cell or legend row carries the words.
	 *
	 * Props: `kind` (CellKind), `superseded` (dot), `class` (size; default 14px).
	 */
	import { cn } from '$lib/utils';
	import type { CellKind } from './selection';

	interface Props {
		kind: CellKind;
		superseded?: boolean;
		class?: string;
	}

	let { kind, superseded = false, class: className }: Props = $props();

	const tone: Record<CellKind, string> = {
		tested: 'text-success',
		failed: 'text-destructive',
		selected: 'text-muted-foreground',
		excluded: 'text-muted-foreground',
		attrition: 'text-warning',
		in_flight: 'text-info',
		missing: 'text-muted-foreground/70'
	};
</script>

<svg viewBox="0 0 16 16" class={cn('size-3.5 shrink-0 overflow-visible', tone[kind], className)} aria-hidden="true" focusable="false">
	{#if kind === 'tested'}
		<rect x="1.5" y="1.5" width="13" height="13" rx="2" fill="currentColor" />
	{:else if kind === 'selected'}
		<rect x="2" y="2" width="12" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5" />
		<rect x="5.5" y="5.5" width="5" height="5" rx="1" fill="currentColor" />
	{:else if kind === 'excluded'}
		<rect x="2" y="2" width="12" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5" />
		<path d="M4.5 8 H11.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
	{:else if kind === 'failed'}
		<rect x="2" y="2" width="12" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5" />
		<path d="M2.6 13.4 L13.4 2.6 L13.4 13.4 Z" fill="currentColor" />
	{:else if kind === 'attrition'}
		<rect x="2" y="2" width="12" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5" />
		<path d="M5 5 L11 11 M11 5 L5 11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
	{:else if kind === 'in_flight'}
		<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.75" />
	{:else}
		<rect x="2" y="2" width="12" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.25" stroke-dasharray="2.4 2" />
	{/if}
	{#if superseded}
		<circle cx="14.6" cy="1.4" r="2.6" class="sm-dot" />
	{/if}
</svg>

<style>
	.sm-dot {
		fill: var(--foreground);
		stroke: var(--card);
		stroke-width: 1.2;
	}
</style>
