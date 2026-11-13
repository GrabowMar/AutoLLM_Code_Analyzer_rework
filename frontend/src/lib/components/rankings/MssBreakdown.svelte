<script lang="ts">
	/**
	 * One rankings row's MSS inputs, opened under the row: popularity (token and
	 * spend share, days listed / censored, window), price (list price and its
	 * source, $/run, π for 30 runs, tier), capability (WebDev rating ± CI and the
	 * arena name), recency (release date), routes and eligibility. Each block names
	 * its source's as_of; a missing input says "no data", never 0.
	 *
	 * Props: `row` (RankingRow).
	 */
	import type { RankingRow, RouteKind } from '$lib/api/rankings';
	import { Badge } from '$lib/components/ui/badge';
	import { exclusionLabel, flagLabel, fmtDate, fmtScore, fmtShare, fmtUsd, routeLabel, tierLabel, NO_DATA } from './mss';

	interface Props {
		row: RankingRow;
	}

	let { row }: Props = $props();

	const pop = $derived(row.popularity);
	const price = $derived(row.list_price);
	const web = $derived(row.webdev_rating);
	const ex = $derived(row.exclusion ? exclusionLabel(row.exclusion.code) : null);
	const ROUTE_ORDER: RouteKind[] = ['go', 'anthropic', 'openrouter'];
	const routes = $derived(ROUTE_ORDER.filter((k) => row.routes?.[k]).map((k) => ({ kind: k, r: row.routes[k]! })));
	const ci = $derived(
		web?.rating_lower != null && web?.rating_upper != null ? (web.rating_upper - web.rating_lower) / 2 : null,
	);
</script>

{#snippet score(v: number | null | undefined)}
	{@const s = fmtScore(v)}
	<span class="font-mono text-sm font-medium tabular-nums {s == null ? 'text-muted-foreground' : ''}">{s ?? NO_DATA}</span>
{/snippet}

<div class="bd" role="group" aria-label="MSS inputs of {row.model_name}">
	<section class="bd-block">
		<header class="bd-head"><span class="section-label">Popularity</span>{@render score(row.popularity_score)}</header>
		{#if pop && pop.volume_share != null}
			<dl class="bd-dl">
				<dt>Token share</dt><dd>{fmtShare(pop.volume_share)}</dd>
				<dt>Spend share</dt><dd>{fmtShare(pop.value_share)}</dd>
				<dt>Days listed</dt><dd>{pop.days_listed ?? '—'} / {pop.days_available ?? '—'}{#if pop.days_censored}<span class="text-muted-foreground"> · {pop.days_censored} censored</span>{/if}</dd>
				<dt>Window</dt><dd>{pop.window ? `${pop.window.start} – ${pop.window.end}` : '—'}</dd>
			</dl>
			<p class="bd-src">{pop.source === 'lmarena_proxy' ? 'LMArena interactions (proxy)' : 'OpenRouter rankings-daily'} · as of {fmtDate(pop.as_of)}{pop.short_exposure ? ' · short exposure' : ''}</p>
		{:else if pop?.source === 'lmarena_proxy'}
			<p class="bd-none">Proxied from LMArena interactions (no OpenRouter usage)</p>
		{:else}
			<p class="bd-none">{pop?.source ? `${pop.source}: no share recorded` : 'No OpenRouter usage recorded'}</p>
		{/if}
	</section>

	<section class="bd-block">
		<header class="bd-head"><span class="section-label">Price</span>{@render score(row.price_score)}</header>
		{#if price || row.run_cost_usd != null}
			<dl class="bd-dl">
				<dt>$/run</dt><dd>{fmtUsd(row.run_cost_usd)}</dd>
				<dt title="30 runs × retry factor at the planning basket">π (30 runs)</dt><dd>{fmtUsd(row.planning_cost_usd, 2)}</dd>
				<dt>Tier</dt><dd>{tierLabel(row.price_tier)}</dd>
				{#if price}
					<dt>In / out per M</dt><dd>{fmtUsd(price.input, 2)} / {fmtUsd(price.output, 2)}</dd>
					<dt>Cache r / w</dt><dd>{fmtUsd(price.cache_read, 3)} / {fmtUsd(price.cache_write, 3)}</dd>
				{/if}
			</dl>
			{#if price}<p class="bd-src" title={price.source ?? undefined}>{price.source ?? 'list price'} · fetched {fmtDate(price.fetched_at)}</p>{/if}
		{:else}
			<p class="bd-none">No list price recorded</p>
		{/if}
	</section>

	<section class="bd-block">
		<header class="bd-head"><span class="section-label">Capability</span>{@render score(row.capability_score)}</header>
		{#if web && web.rating != null}
			<dl class="bd-dl">
				<dt>WebDev rating</dt><dd>{web.rating.toFixed(0)}{#if ci != null}<span class="text-muted-foreground"> ± {ci.toFixed(0)}</span>{/if}</dd>
				<dt>95% CI</dt><dd>{web.rating_lower?.toFixed(0) ?? '—'} – {web.rating_upper?.toFixed(0) ?? '—'}</dd>
				<dt>Arena name</dt><dd class="truncate" title={web.arena_name ?? undefined}>{web.arena_name ?? '—'}</dd>
				{#if web.match}<dt>Match</dt><dd>{web.match}{web.from_model && web.from_model !== row.canonical_id ? ` · via ${web.from_model}` : ''}</dd>{/if}
			</dl>
			<p class="bd-src">LMArena WebDev · published {fmtDate(web.publish_date)}</p>
		{:else}
			<p class="bd-none">No LMArena WebDev rating: left out, weights renormalised</p>
		{/if}
	</section>

	<section class="bd-block">
		<header class="bd-head"><span class="section-label">Recency</span>{@render score(row.recency_score)}</header>
		<dl class="bd-dl">
			<dt>Released</dt><dd>{fmtDate(row.release_date)}</dd>
			<dt>Coverage</dt><dd>{row.mss_coverage != null ? `${Math.round(row.mss_coverage * 100)}% of weight` : '—'}</dd>
			{#if row.mss_missing.length}<dt>Missing</dt><dd>{row.mss_missing.join(', ')}</dd>{/if}
		</dl>
	</section>

	<section class="bd-block">
		<header class="bd-head"><span class="section-label">Routes</span>{#if row.preferred_route}<span class="text-[11px] text-muted-foreground">prefers {routeLabel(row.preferred_route)}</span>{/if}</header>
		{#if routes.length}
			<ul class="space-y-1 text-xs">
				{#each routes as { kind, r } (kind)}
					<li class="flex items-baseline justify-between gap-2">
						<span class="shrink-0">{routeLabel(kind)}</span>
						<span class="truncate font-mono text-[11px] text-muted-foreground" title={r.permaslug ?? r.served_name ?? r.model_id ?? undefined}>
							{r.served_name ?? r.permaslug ?? r.model_id ?? '—'}{r.available === false ? ' · unavailable' : ''}{r.paid === false ? ' · free only' : ''}
						</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="bd-none">No study route</p>
		{/if}
	</section>

	<section class="bd-block">
		<header class="bd-head"><span class="section-label">Eligibility</span></header>
		{#if row.eligible}
			<p class="text-xs">Eligible{row.canonical_id && row.canonical_id !== row.model_id ? ` as ${row.canonical_id}` : ''}</p>
		{:else if row.exclusion && ex}
			<p class="text-xs"><span class="font-mono text-muted-foreground">{row.exclusion.step}</span> {ex.label}{ex.target ? ` ${ex.target}` : ''}</p>
			{#if row.exclusion.detail}<p class="bd-src">{row.exclusion.detail}</p>{/if}
		{/if}
		{#if row.mss_inherited_from}<p class="bd-src">Scores of {row.mss_inherited_from} (route copy)</p>{/if}
		{#if row.mss_flags.length}
			<div class="mt-1.5 flex flex-wrap gap-1">
				{#each row.mss_flags as f (f)}
					{@const fl = flagLabel(f)}
					<Badge variant="outline" class="text-[11px] font-normal" title={fl.help}>{fl.label}</Badge>
				{/each}
			</div>
		{/if}
	</section>
</div>

<style>
	.bd {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 14rem), 1fr));
		gap: 0.75rem 1.25rem;
		padding: 0.75rem 1rem 1rem;
		white-space: normal;
	}
	.bd-block {
		min-width: 0;
	}
	.bd-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem;
		margin-bottom: 0.375rem;
	}
	.bd-dl {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		gap: 0.125rem 0.75rem;
		font-size: 0.75rem;
	}
	.bd-dl dt {
		color: var(--muted-foreground);
	}
	.bd-dl dd {
		text-align: right;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		min-width: 0;
	}
	.bd-src {
		margin-top: 0.375rem;
		font-size: 11px;
		color: var(--muted-foreground);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.bd-none {
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
</style>
