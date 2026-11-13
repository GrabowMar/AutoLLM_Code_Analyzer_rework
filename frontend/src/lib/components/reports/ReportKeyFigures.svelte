<script lang="ts">
	/**
	 * ReportKeyFigures — the report's summary as one panel of key figures (`keyFigures`): the
	 * leader across two cells, rates as big mono percentages over a neutral meter, counts, and
	 * `by_*` breakdowns as a ranked list with share bars. Values are never colored by size;
	 * only a non-zero warning count is marked (warning tone + text, not color alone).
	 *
	 * Compare mode (`previous`: the compared report's figures): numbers and rates get a signed
	 * delta underneath, the leader says what the other report's leader was.
	 *
	 * Props: `figures`, `previous`, `previousLabel`.
	 */
	import Trophy from '@lucide/svelte/icons/trophy';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { cn } from '$lib/utils';
	import type { KeyFigure } from './format';
	import { cellDelta } from './compare';

	interface Props {
		figures: KeyFigure[];
		previous?: KeyFigure[] | null;
		previousLabel?: string;
	}

	let { figures, previous = null, previousLabel = 'compared report' }: Props = $props();
	const prev = $derived(new Map((previous ?? []).map((f) => [f.label, f])));

	const tiles = $derived(figures.filter((f) => f.kind !== 'breakdown'));
	const breakdowns = $derived(figures.filter((f) => f.kind === 'breakdown'));
</script>

<section aria-label="Key figures" class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
	{#if tiles.length > 0}
		<dl class="rk-grid">
			{#each tiles as f, i (f.label)}
				<div class={cn('rk-cell rise-in', f.kind === 'leader' && 'rk-wide')} style:--stagger-i={Math.min(i, 8)}>
					<dt class="kpi-label flex items-center gap-1.5">
						{#if f.kind === 'leader'}<Trophy class="size-3.5 text-accent-2" aria-hidden="true" />{/if}
						{#if f.attention}<TriangleAlert class="size-3.5 text-warning-fg" aria-hidden="true" />{/if}
						{f.label}
					</dt>
					<dd class="mt-1.5 min-w-0">
						{#if f.kind === 'leader'}
							<span class="block break-words font-display text-lg font-semibold leading-snug">{f.value}</span>
						{:else if f.kind === 'rate'}
							<span class="block font-mono text-2xl font-medium leading-none tabular-nums">{f.value}</span>
							<span class="mt-2 block h-1 w-full max-w-36 overflow-hidden rounded-full bg-muted" aria-hidden="true">
								<span class="block h-full rounded-full bg-primary/70" style:width="{(f.share ?? 0) * 100}%"></span>
							</span>
						{:else if f.kind === 'number'}
							<span class={cn('block font-mono text-2xl font-medium leading-none tabular-nums', f.attention && 'text-warning-fg')}>{f.value}</span>
							{#if f.attention}<span class="mt-1 block text-xs text-warning-fg">see threats to validity</span>{/if}
						{:else if f.href}
							<a href={f.href} class="settings-link font-mono text-sm">{f.value}</a>
						{:else}
							<span class="block break-words text-sm font-medium">{f.value}</span>
						{/if}
						{#if previous}
							{@const p = prev.get(f.label)}
							{@const d = p ? cellDelta(f.value, p.value) : null}
							<span class="mt-1.5 block truncate font-mono text-[11px] text-muted-foreground" title="{previousLabel}: {p?.value ?? 'not in that report'}">
								{#if !p}not in {previousLabel}{:else if d}{d.sign === 0 ? 'same as' : `${d.text} vs`} {previousLabel}{:else if p.value === f.value}same as {previousLabel}{:else}{previousLabel}: {p.value}{/if}
							</span>
						{/if}
					</dd>
				</div>
			{/each}
		</dl>
	{/if}
	{#if breakdowns.length > 0}
		<div class={cn('grid gap-x-8 gap-y-4 px-4 py-3.5 sm:px-5 md:grid-cols-2', tiles.length > 0 && 'border-t border-border/60')}>
			{#each breakdowns as b (b.label)}
				<div class="min-w-0">
					<p class="kpi-label flex items-baseline justify-between gap-2">
						<span>{b.label}</span>
						<span class="font-mono normal-case tracking-normal text-muted-foreground">n = {b.total?.toLocaleString()}</span>
					</p>
					<ul class="mt-2 space-y-1.5">
						{#each b.parts ?? [] as p (p.label)}
							{@const share = b.total ? p.n / b.total : 0}
							<li class="grid grid-cols-[minmax(6rem,9rem)_minmax(0,1fr)_auto] items-center gap-3 text-sm">
								<span class="truncate" title={p.label}>{p.label}</span>
								<span class="block h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
									<span class="block h-full rounded-full bg-muted-foreground/50" style:width="{share * 100}%"></span>
								</span>
								<span class="text-right font-mono text-xs tabular-nums">
									{p.n.toLocaleString()} <span class="text-muted-foreground">· {(share * 100).toFixed(0)}%</span>
								</span>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</div>
	{/if}
</section>

<style>
	.rk-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		/* Hairlines between cells: each cell draws its right and bottom edge; the panel clips the outer ones. */
		margin: 0 -1px -1px 0;
	}
	.rk-cell {
		min-width: 0;
		padding: 0.875rem 1.25rem;
		border-right: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		border-bottom: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
	}
	@media (min-width: 40rem) {
		.rk-wide {
			grid-column: span 2;
		}
	}
</style>
