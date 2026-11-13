<script lang="ts">
	/**
	 * Benchmark scores published in the model's Hugging Face repo (evalResults /
	 * model-index): self-reported by the authors or third parties, shown exactly
	 * as published with their source and harness notes. Not verified here, not
	 * normalized (scales differ) and never folded into any platform score.
	 *
	 * Grouped by dataset: one line per benchmark with its headline score (the
	 * published "mean"/"overall" row when there is one, else the first row) and
	 * the rest — sub-scores, harness notes — behind a disclosure. No "best of"
	 * across rows: their metrics, scales and harnesses differ.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { isoDay, type HfEvalResult } from './helpers';

	interface Props {
		results: HfEvalResult[];
		/** Published count before the storage cap; more than `results.length` when rows were cut. */
		total?: number | null;
		hfId: string;
		fetchedAt?: string | null;
		inheritedFrom?: string | null;
		inheritedSlug?: string | null;
	}

	let { results, total = null, hfId, fetchedAt = null }: Props = $props();

	const truncated = $derived(total != null && total > results.length);
	const verified = $derived(results.filter((r) => r.verified).length);

	interface Group {
		dataset: string;
		org: string;
		name: string;
		rows: HfEvalResult[];
		head: HfEvalResult;
	}

	const HEADLINE = /^(mean|overall|average|avg|score|overall-score)$/i;

	const groups = $derived.by<Group[]>(() => {
		const by = new Map<string, HfEvalResult[]>();
		for (const r of results) {
			const key = r.dataset ?? '—';
			by.set(key, [...(by.get(key) ?? []), r]);
		}
		return [...by.entries()]
			.map(([dataset, rows]) => {
				const slash = dataset.indexOf('/');
				const head = rows.find((r) => HEADLINE.test(r.metric ?? '') || HEADLINE.test(r.task ?? '')) ?? rows[0];
				return { dataset, org: slash > 0 ? dataset.slice(0, slash) : '', name: slash > 0 ? dataset.slice(slash + 1) : dataset, rows, head };
			})
			.sort((a, b) => a.name.localeCompare(b.name));
	});

	const label = (r: HfEvalResult) => [r.task, r.metric && r.metric !== r.task ? r.metric : null].filter(Boolean).join(' · ') || '—';
</script>

{#snippet source(r: HfEvalResult)}
	{#if r.source_url}
		<a href={r.source_url} target="_blank" rel="noopener noreferrer" class="text-primary hover:underline" onclick={(e) => e.stopPropagation()}>{r.source_name ?? 'source'}</a>
	{:else}
		<span>{r.source_name ?? r.source}</span>
	{/if}
	{#if r.verified}<Badge variant="secondary" class="ml-1">verified</Badge>{/if}
{/snippet}

<section class="md-sec" aria-labelledby="self-reported-title">
	<div class="md-sec-head">
		<h2 id="self-reported-title" class="md-sec-title">Self-reported evaluations</h2>
		<Badge variant="outline">self-reported</Badge>
		<span class="md-sec-desc">
			{groups.length} benchmarks · {#if truncated}first {results.length} of {total}{:else}{results.length}{/if} scores · {verified} verified by Hugging Face
		</span>
		<a href="https://huggingface.co/{hfId}" target="_blank" rel="noopener noreferrer" class="md-sec-aside inline-flex items-center gap-1 font-mono hover:text-foreground">
			{hfId}<ExternalLink class="size-3" aria-hidden="true" />
		</a>
	</div>
	<p class="border-b border-border px-5 py-2 text-[11px] text-muted-foreground">
		As published{fetchedAt ? ` (fetched ${isoDay(fetchedAt)})` : ''}: not checked by this platform, scales differ between rows (0–1 vs 0–100), harnesses differ.
	</p>
	<!-- The tab shows the inherited-from note once, above every external source. -->

	<ul class="divide-y divide-border">
		{#each groups as g (g.dataset)}
			<li>
				{#if g.rows.length === 1 && !g.head.notes}
					<div class="ev-row">
						<span class="ev-caret" aria-hidden="true"></span>
						<span class="min-w-0">
							<span class="block truncate text-sm font-medium">{g.name}{#if g.org}<span class="font-normal text-muted-foreground">{` · ${g.org}`}</span>{/if}</span>
							<span class="block truncate text-[11px] text-muted-foreground">{label(g.head)}</span>
						</span>
						<span class="ev-score">{g.head.value}</span>
						<span class="ev-src">{@render source(g.head)}</span>
						<span class="ev-date">{g.head.date ? g.head.date.slice(0, 10) : '—'}</span>
					</div>
				{:else}
					<details class="ev-group">
						<summary class="ev-row">
							<ChevronRight class="ev-caret size-3.5" aria-hidden="true" />
							<span class="min-w-0">
								<span class="block truncate text-sm font-medium">{g.name}{#if g.org}<span class="font-normal text-muted-foreground">{` · ${g.org}`}</span>{/if}</span>
								<span class="block truncate text-[11px] text-muted-foreground">{label(g.head)}{g.rows.length > 1 ? ` · ${g.rows.length - 1} more score${g.rows.length === 2 ? '' : 's'}` : ' · notes'}</span>
							</span>
							<span class="ev-score">{g.head.value}</span>
							<span class="ev-src">{@render source(g.head)}</span>
							<span class="ev-date">{g.head.date ? g.head.date.slice(0, 10) : '—'}</span>
						</summary>
						<ul class="ev-detail">
							{#each g.rows as r, i (i)}
								<li class="ev-sub">
									<span class="min-w-0">
										<span class="block text-xs">{label(r)}</span>
										{#if r.notes}<span class="block text-[11px] leading-snug text-muted-foreground">{r.notes}</span>{/if}
									</span>
									<span class="ev-score text-xs!">{r.value}</span>
									<span class="ev-src">{@render source(r)}</span>
									<span class="ev-date">{r.date ? r.date.slice(0, 10) : '—'}</span>
								</li>
							{/each}
						</ul>
					</details>
				{/if}
			</li>
		{/each}
	</ul>
</section>

<style>
	.ev-row,
	.ev-sub {
		display: grid;
		grid-template-columns: 1rem minmax(0, 1fr) 5rem minmax(7rem, 14rem) 5.5rem;
		align-items: center;
		gap: 0.75rem;
		padding: 0.625rem 1.25rem;
	}
	.ev-sub {
		grid-template-columns: minmax(0, 1fr) 5rem minmax(7rem, 14rem) 5.5rem;
		padding: 0.5rem 1.25rem 0.5rem 3rem;
		align-items: start;
	}
	summary.ev-row {
		cursor: pointer;
		list-style: none;
		transition: background-color 150ms;
	}
	summary.ev-row::-webkit-details-marker {
		display: none;
	}
	summary.ev-row:hover {
		background: var(--surface-2, var(--muted));
	}
	summary.ev-row:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	:global(.ev-caret) {
		color: var(--muted-foreground);
		transition: transform 150ms;
	}
	.ev-group[open] :global(.ev-caret) {
		transform: rotate(90deg);
	}
	.ev-detail {
		border-top: 1px dashed var(--border);
		background: color-mix(in oklab, var(--muted) 30%, transparent);
	}
	.ev-score {
		text-align: right;
		font-family: var(--font-mono);
		font-size: 0.875rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
	}
	.ev-src {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.75rem;
	}
	.ev-date {
		text-align: right;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	@media (max-width: 640px) {
		.ev-row {
			grid-template-columns: 1rem minmax(0, 1fr) 4rem;
			padding-inline: 1rem;
		}
		.ev-sub {
			grid-template-columns: minmax(0, 1fr) 4rem;
			padding-left: 2rem;
		}
		.ev-src,
		.ev-date {
			display: none;
		}
	}
</style>
