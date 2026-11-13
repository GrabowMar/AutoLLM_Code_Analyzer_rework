<script lang="ts">
	/**
	 * One study selection (a snapshot's payload or a live preview), read-only:
	 * headline figures and per-route budget use, the infeasibility diagnosis when
	 * no set meets the constraints, the picks grouped by price tier (MSS_tier,
	 * global MSS, components, capability percentile in the tier, route, π for 30
	 * runs, Go cap use, selection frequency), constraint status, the higher-ranked
	 * models that were not picked and why, alternates (next in tier, per-pick swap),
	 * the sensitivity summary, and the inputs (tiers, quality floor, sources, flow).
	 *
	 * Props: `payload` (SelectionPayload), `class`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { ScoreCell, tableFrame } from '$lib/components/table';
	import { modelPathSegment } from '$lib/api/models';
	import { PRICE_TIERS, type PriceTier, type SelectionPayload, type SelectionPick } from '$lib/api/rankings';
	import { cn } from '$lib/utils';
	import FlowList from './FlowList.svelte';
	import SourcesTable from './SourcesTable.svelte';
	import {
		COMPONENTS,
		NO_DATA,
		admittedLabel,
		budgetFraction,
		candidateIndex,
		constraintFigure,
		constraintLabel,
		describeBlocked,
		flagLabel,
		fmtDate,
		fmtNum,
		fmtPct,
		fmtUsd,
		frequencyTable,
		modelPageId,
		payloadWeights,
		picksByTier,
		robustnessTone,
		routeLabel,
		shortName,
		tierBand,
		tierLabel,
		weightLabel,
		weightsLabel,
	} from './mss';

	interface Props {
		payload: SelectionPayload;
		class?: string;
	}

	let { payload, class: className }: Props = $props();

	const cands = $derived(candidateIndex(payload));
	const sel = $derived(payload.selection);
	const picks = $derived(sel?.picks ?? []);
	const groups = $derived(picksByTier(picks));
	const weights = $derived(payloadWeights(payload));
	const use = $derived(sel?.budget_use ?? null);
	const freq = $derived(frequencyTable(payload.sensitivity));
	const pickIds = $derived(new Set(picks.map((p) => p.canonical_id)));
	const replacements = $derived(new Map((payload.alternates?.replacements ?? []).map((r) => [r.replace, r])));
	const robustCounts = $derived.by(() => {
		const out = { robust: 0, borderline: 0, fragile: 0 };
		for (const p of picks) if (p.robustness) out[p.robustness] += 1;
		return out;
	});
	const sensRan = $derived(picks.some((p) => p.selection_frequency != null));
	const maxGoShare = $derived(Math.max(0, ...(use?.go ?? []).map((g) => g.share_of_monthly_limit)));
	// B-go allows (1 − contingency) × the monthly limit × the study months.
	const studyMonths = $derived(Number(payload.params?.study_months ?? 1) || 1);
	const tierCounts = $derived(payload.tiers?.counts ?? {});

	const name = (id: string) => shortName(id, cands);
	const href = (id: string) => `/models/${modelPathSegment(modelPageId(id, cands))}`;

	function budgetTone(f: number | null): 'good' | 'warn' | 'bad' | undefined {
		if (f == null) return undefined;
		return f > 1 ? 'bad' : f >= 0.9 ? 'warn' : 'good';
	}

	function goLine(p: SelectionPick): string {
		const g = p.go_usage;
		if (!g) return '—';
		return `${fmtPct(g.share_of_monthly_limit)} of ${fmtUsd(g.monthly_limit_usd, 0)}`;
	}

	/** "admitted without capability" already says "no capability". */
	function shownFlags(flags: string[]): string[] {
		return flags.includes('capability_missing_admitted') ? flags.filter((f) => f !== 'capability_missing') : flags;
	}

	/** "P 99 · C 62 · Q no data · R 87" (C = the within-tier price score). */
	function componentLine(p: SelectionPick): string {
		return COMPONENTS.map((c) => {
			const v = c.key === 'price' ? p.tier_price_score : p.scores[c.key];
			return `${c.letter} ${v != null ? (v * 100).toFixed(0) : NO_DATA}`;
		}).join(' · ');
	}

	function pinned(p: SelectionPick): string {
		const v = p.pinned_version ?? {};
		const parts = [v.permaslug, v.served_name, v.model_id, v.note].filter((x): x is string => typeof x === 'string' && x !== '');
		return [...new Set(parts)].join(' · ');
	}

	function mixLine(mix: Record<string, number> | undefined): string {
		return Object.entries(mix ?? {})
			.sort((a, b) => b[1] - a[1])
			.map(([k, v]) => `${k} ${v}`)
			.join(' · ');
	}
	const openMix = $derived(sel?.open_weight_mix ?? {});
</script>

{#snippet modelCell(id: string, sub?: string)}
	<a href={href(id)} class="block truncate text-sm font-medium hover:text-primary" title={id}>{name(id)}</a>
	{#if sub}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{sub}</span>{/if}
{/snippet}

{#snippet components(p: SelectionPick)}
	<div class="comp-grid" aria-label="Components">
		{#each COMPONENTS as c (c.key)}
			{@const v = c.key === 'price' ? p.tier_price_score : p.scores[c.key]}
			<span
				class="comp"
				title="{c.label}{c.key === 'price' ? ` within the tier (global price score ${fmtNum(p.scores.price != null ? p.scores.price * 100 : null, 1)})` : ''}: {v != null ? (v * 100).toFixed(1) : NO_DATA}"
			>
				<span class="comp-letter">{c.letter}</span>
				{#if v == null}
					<span class="comp-none">{NO_DATA}</span>
				{:else}
					<span class="comp-val">{(v * 100).toFixed(0)}</span>
					<span class="comp-bar" aria-hidden="true"><span style:width="{Math.max(0, Math.min(1, v)) * 100}%"></span></span>
				{/if}
			</span>
		{/each}
	</div>
	<span class="cell-sub mt-0.5 block text-[11px] text-muted-foreground">
		cov {fmtPct(p.coverage)} · Q {p.capability_pct_in_tier != null ? `p${Math.round(p.capability_pct_in_tier * 100)} in tier` : NO_DATA}
	</span>
{/snippet}

{#snippet floorMark(p: SelectionPick)}
	{#if p.floor_sensitive}
		<span class="ml-1 text-[11px] text-warning-fg" title="Leaves the set under a quality-floor variant (S6)">▲ floor</span>
	{/if}
{/snippet}

{#snippet freqBadge(cls: string | null, f: number | null)}
	{#if f == null}
		<span class="text-[11px] text-muted-foreground">not run</span>
	{:else}
		<StatusBadge status={cls ?? 'n/a'} tone={robustnessTone(cls)} label="{cls ?? '—'} {fmtPct(f)}" />
	{/if}
{/snippet}

<div class={cn('space-y-4', className)}>
	{#if payload.warnings?.length}
		<ul class="space-y-1 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-fg">
			{#each payload.warnings as w (w)}<li>{w}</li>{/each}
		</ul>
	{/if}

	<!-- Headline figures -->
	<dl class="fig-strip">
		<div class="fig-cell" data-tone={sel ? 'good' : 'bad'}>
			<dt class="fig-label">Selection</dt>
			<dd class="fig-value">{sel ? picks.length : 'Infeasible'}</dd>
			<dd class="fig-sub">
				{#if sel}{groups.map((g) => `${g.picks.length} ${g.tier}`).join(' · ')}{:else}no set meets every constraint{/if}
			</dd>
		</div>
		{#if use}
			{@const fa = budgetFraction(use.anthropic.planned_usd, use.anthropic.effective_usd)}
			{@const fo = budgetFraction(use.openrouter.planned_usd, use.openrouter.effective_usd)}
			<div class="fig-cell" data-tone={budgetTone(fa)}>
				<dt class="fig-label">Anthropic key</dt>
				<dd class="fig-value">{fmtUsd(use.anthropic.planned_usd, 2)}</dd>
				<dd class="fig-sub" title="Available {fmtUsd(use.anthropic.available_usd, 2)} less the {fmtPct(use.contingency)} contingency">of {fmtUsd(use.anthropic.effective_usd, 2)} · {fmtPct(fa)}</dd>
				{#if use.anthropic.break_even_cache_hit != null && use.anthropic.break_even_cache_hit > 0}
					<dd class="fig-sub" title="Planned at a {fmtPct(use.anthropic.cache_hit_assumed)} prompt-cache hit share; {fmtUsd(use.anthropic.planned_h0_usd, 2)} with no cache hits">holds at ≥ {fmtPct(use.anthropic.break_even_cache_hit)} cache hits</dd>
				{/if}
			</div>
			<div class="fig-cell" data-tone={budgetTone(fo)}>
				<dt class="fig-label">OpenRouter</dt>
				<dd class="fig-value">{fmtUsd(use.openrouter.planned_usd, 2)}</dd>
				<dd class="fig-sub" title="Available {fmtUsd(use.openrouter.available_usd, 2)} ({use.openrouter.source ?? 'source unknown'}) less the {fmtPct(use.contingency)} contingency">of {fmtUsd(use.openrouter.effective_usd, 2)} · {fmtPct(fo)}</dd>
			</div>
			<div class="fig-cell" data-tone={use.go.length ? budgetTone(maxGoShare / ((1 - use.contingency) * studyMonths)) : undefined}>
				<dt class="fig-label">OpenCode Go</dt>
				<dd class="fig-value">{use.go.length}</dd>
				<dd class="fig-sub">{use.go.length ? `models · max ${fmtPct(maxGoShare)} of a monthly limit` : 'no Go-routed pick'}</dd>
			</div>
		{:else}
			<div class="fig-cell">
				<dt class="fig-label">Budgets</dt>
				<dd class="fig-value">{fmtUsd(payload.budgets?.anthropic_effective, 0)} · {fmtUsd(payload.budgets?.openrouter_effective, 0)}</dd>
				<dd class="fig-sub" title={payload.budgets?.openrouter_source ?? undefined}>Anthropic · OpenRouter, effective</dd>
			</div>
		{/if}
		<div class="fig-cell">
			<dt class="fig-label" title="Robustness = the weaker of the S1 (weights) and S3 (specification) selection frequencies">Stability</dt>
			<dd class="fig-value">{sensRan ? `${robustCounts.robust}/${picks.length}` : '—'}</dd>
			<dd class="fig-sub">{sensRan ? `robust · ${robustCounts.borderline} borderline · ${robustCounts.fragile} fragile` : 'sensitivity not run'}</dd>
		</div>
		{#if sel}
			<div class="fig-cell">
				<dt class="fig-label">Mix</dt>
				<dd class="fig-value">{mixLine(sel.origin_mix) || '—'}</dd>
				<dd class="fig-sub">{openMix.True ?? 0} open · {openMix.False ?? 0} closed weights</dd>
			</div>
		{/if}
	</dl>

	{#if payload.infeasible}
		{@const inf = payload.infeasible}
		<Card.Root class="gap-0 border-destructive/40 py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="text-sm font-medium">No feasible selection</Card.Title>
				<Card.Description class="text-xs">{inf.note}</Card.Description>
			</Card.Header>
			<Card.Content class="grid grid-cols-[minmax(0,1fr)] gap-4 p-4 text-xs md:grid-cols-2">
				<div>
					<p class="section-label mb-1.5">Relax one constraint</p>
					<ul class="space-y-1">
						{#each inf.relaxations as r (r.constraint)}
							<li class="flex items-center justify-between gap-2">
								<span title={constraintLabel(r.constraint).help}>{constraintLabel(r.constraint).label} <span class="font-mono text-muted-foreground">{r.constraint}</span></span>
								<StatusBadge status={r.feasible_if_relaxed ? 'feasible' : 'still infeasible'} tone={r.feasible_if_relaxed ? 'success' : 'neutral'} />
							</li>
						{/each}
					</ul>
				</div>
				<div class="space-y-2">
					<p class="section-label">What would make it feasible</p>
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
						<dt class="text-muted-foreground">Min. OpenRouter budget</dt>
						<dd class="font-mono tabular-nums">
							{#if inf.min_openrouter_budget_effective_usd != null}
								{fmtUsd(inf.min_openrouter_budget_effective_usd, 2)} effective
								<span class="text-muted-foreground">(≈ {fmtUsd(inf.min_openrouter_budget_effective_usd / (1 - (payload.budgets?.contingency ?? 0.15)), 2)} credit)</span>
							{:else}—{/if}
						</dd>
						<dt class="text-muted-foreground">Min. Anthropic budget</dt>
						<dd class="font-mono tabular-nums">{inf.min_anthropic_budget_effective_usd != null ? `${fmtUsd(inf.min_anthropic_budget_effective_usd, 2)} effective` : '—'}</dd>
						<dt class="text-muted-foreground">Study months</dt>
						<dd class="font-mono tabular-nums">{inf.feasible_with_study_months != null ? `feasible with ${inf.feasible_with_study_months}` : '—'}</dd>
						<dt class="text-muted-foreground">Without quality floor</dt>
						<dd>{inf.feasible_without_quality_floor ? 'feasible' : 'still infeasible'}</dd>
						{#if inf.diagnosed_at_floor_percentiles}
							<dt class="text-muted-foreground">Diagnosed at floor</dt>
							<dd class="font-mono tabular-nums">
								{#each Object.entries(inf.diagnosed_at_floor_percentiles) as [t, lvl] (t)}<span class="mr-2">{tierLabel(t)} {lvl != null ? `p${lvl}` : 'none'}</span>{/each}
							</dd>
						{/if}
					</dl>
					<p class="section-label pt-1">Selectable per tier</p>
					<p class="font-mono tabular-nums">
						{#each Object.entries(inf.quota_per_tier) as [t, q] (t)}
							{@const s = inf.selectable_per_tier[t as PriceTier] ?? 0}
							<span class="mr-3 {s < (q ?? 0) ? 'text-danger-fg' : ''}">{tierLabel(t)} {s}/{q}</span>
						{/each}
					</p>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

	<!-- Picks, by tier -->
	{#if sel}
		{#each groups as g (g.tier)}
			<section class="space-y-2" aria-labelledby="sel-tier-{g.tier}">
				<SectionHeading
					id="sel-tier-{g.tier}"
					size="sm"
					title="{tierLabel(g.tier)} · {g.picks.length} of {tierCounts[g.tier] ?? '—'}"
					count={tierBand(payload.tiers, g.tier)}
				/>
				<Card.Root class="gap-0 overflow-clip py-0">
					<div class="table-frame hidden md:block" {@attach tableFrame}>
						<table>
							<thead>
								<tr>
									<th class="num w-8" title="Rank by MSS_tier within the tier">#</th>
									<th class="col-sticky">Model</th>
									<th class="num" title="Within-tier score that drives the selection: {COMPONENTS.map((c) => `${c.label} ${weightLabel(weights.tier[c.key])}`).join(', ')}; price re-normalised inside the tier band">MSS<sub>tier</sub></th>
									<th class="num" title="Global MSS (rankings page) and its rank among eligible models">MSS</th>
									<th title="Components of MSS_tier, 0–100: {COMPONENTS.map((c) => `${c.letter} ${c.label} ${weightLabel(weights.tier[c.key])}`).join(', ')}. C is the price re-normalised inside the tier band; under Q, its percentile within the tier (the quality floor is the tier median). Coverage = share of the weight with data.">Components</th>
									<th>Route</th>
									<th class="num" title="30 runs × retry factor at the planning basket (USD)">π 30</th>
									<th class="num" title="OpenCode Go: share of the model's monthly limit · weeks needed · runs per 5-hour window">Go cap</th>
									<th title="Selection frequency: the weaker of S1 (within-tier weights) and S3 (specification incl. rating noise); ▲ = leaves under a quality-floor variant (S6)">Stability</th>
								</tr>
							</thead>
							<tbody>
								{#each g.picks as p (p.canonical_id)}
									<tr>
										<td class="num text-xs text-muted-foreground">{p.tier_rank}</td>
										<td class="col-sticky max-w-[15rem] whitespace-normal">
											{@render modelCell(p.canonical_id, `${p.vendor} · ${p.origin} · ${weightsLabel(p.open_weights)} · ${admittedLabel(p.admitted_by)}`)}
											{#if p.flags.length}
												<span class="mt-1 flex flex-wrap gap-1">
													{#each shownFlags(p.flags) as f (f)}
														<Badge variant="outline" class="h-4 px-1 text-[11px] font-normal" title={flagLabel(f).help}>{flagLabel(f).label}</Badge>
													{/each}
												</span>
											{/if}
										</td>
										<ScoreCell value={p.mss_tier} strong />
										<ScoreCell value={p.mss} sub={p.mss_rank != null ? `#${p.mss_rank}` : undefined} title="Global MSS{p.mss_rank != null ? `, rank ${p.mss_rank} among eligible models` : ''}" />
										<td>{@render components(p)}</td>
										<td class="text-xs" title="{p.route_reason}{pinned(p) ? ` — ${pinned(p)}` : ''}">
											{routeLabel(p.route)}
											<span class="cell-sub block max-w-[10rem] truncate font-mono text-[11px] text-muted-foreground">{pinned(p) || '—'}</span>
										</td>
										<td class="num text-xs">
											{fmtUsd(p.planning_cost_usd, 2)}
											<span class="cell-sub block text-[11px] text-muted-foreground">{fmtUsd(p.run_cost_usd)}/run</span>
										</td>
										<td class="num text-xs {p.go_usage ? '' : 'text-muted-foreground'}">
											{goLine(p)}
											{#if p.go_usage}<span class="cell-sub block text-[11px] text-muted-foreground">{p.go_usage.weeks_needed} wk · {p.go_usage.runs_per_5h ?? '—'}/5h</span>{/if}
										</td>
										<td>{@render freqBadge(p.robustness, p.selection_frequency)}{@render floorMark(p)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<ul class="md:hidden">
						{#each g.picks as p (p.canonical_id)}
							<li class="data-list-row space-y-1.5 px-3">
								<div class="flex items-start justify-between gap-2">
									<div class="min-w-0">{@render modelCell(p.canonical_id, `#${p.tier_rank} · ${p.vendor} · ${routeLabel(p.route)}`)}</div>
									<div class="shrink-0 text-right">
										<div class="font-mono text-base font-medium tabular-nums">{p.mss_tier != null ? (p.mss_tier * 100).toFixed(1) : NO_DATA}</div>
										<div class="text-[11px] text-muted-foreground">MSS<sub>tier</sub></div>
									</div>
								</div>
								<p class="font-mono text-[11px] text-muted-foreground">{componentLine(p)} · π {fmtUsd(p.planning_cost_usd, 2)}{p.go_usage ? ` · Go ${goLine(p)}` : ''}</p>
								{@render freqBadge(p.robustness, p.selection_frequency)}{@render floorMark(p)}
							</li>
						{/each}
					</ul>
				</Card.Root>
			</section>
		{/each}
	{/if}

	<div class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
		<!-- Constraints -->
		{#if payload.constraint_status.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-sm font-medium">Constraints</Card.Title>
					<Card.Description class="text-xs">Binding: relaxing it alone would change the selection</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<ul class="divide-y divide-border">
						{#each payload.constraint_status as c (c.code)}
							{@const meta = constraintLabel(c.code)}
							<li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-xs" title={meta.help}>
								<span class="w-24 shrink-0 font-mono text-[11px] text-muted-foreground">{c.code}</span>
								<span class="min-w-0 flex-1 truncate">{meta.label}</span>
								<span class="font-mono tabular-nums">{constraintFigure(c)}</span>
								<span class="ml-auto flex shrink-0 justify-end gap-1 sm:w-36">
									<StatusBadge status={c.satisfied ? 'satisfied' : 'violated'} tone={c.satisfied ? 'success' : 'danger'} />
									{#if c.binding}<StatusBadge status="binding" tone="warning" />{/if}
								</span>
							</li>
						{/each}
					</ul>
				</Card.Content>
			</Card.Root>
		{/if}

		<!-- Budgets per route -->
		{#if use}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-sm font-medium">Budget use per route</Card.Title>
					<Card.Description class="text-xs">
						{use.runs_per_model} runs × {use.retry_factor} retry factor per model; hard limits after a {fmtPct(use.contingency)} contingency
					</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3 p-4">
					{#each [{ key: 'anthropic', label: 'Anthropic key', b: use.anthropic }, { key: 'openrouter', label: 'OpenRouter (shared credit)', b: use.openrouter }] as r (r.key)}
						{@const f = budgetFraction(r.b.planned_usd, r.b.effective_usd)}
						<div class="space-y-1">
							<div class="flex items-baseline justify-between gap-2 text-xs">
								<span>{r.label}</span>
								<span class="font-mono tabular-nums">{fmtUsd(r.b.planned_usd, 2)} <span class="text-muted-foreground">/ {fmtUsd(r.b.effective_usd, 2)}</span></span>
							</div>
							<span class="block h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
								<span class="block h-full rounded-full {f != null && f > 1 ? 'bg-destructive' : 'bg-primary'}" style:width="{Math.min(100, (f ?? 0) * 100)}%"></span>
							</span>
							<p class="text-[11px] text-muted-foreground">
								available {fmtUsd(r.b.available_usd, 2)}{r.b.source ? ` · ${r.b.source}` : ''}
							</p>
						</div>
					{/each}
					{#if use.go.length}
						<div class="overflow-x-auto">
							<table class="w-full">
								<thead>
									<tr>
										<th>OpenCode Go</th>
										<th class="num" title="Monthly limit for this model on the user's Go plan">Limit</th>
										<th class="num">π 30</th>
										<th class="num" title="Share of the monthly limit">Use</th>
										<th class="num" title="Weeks needed under the weekly cap (50% of the monthly limit)">Weeks</th>
										<th class="num" title="Runs per 5-hour window (20% of the monthly limit)">Runs/5h</th>
									</tr>
								</thead>
								<tbody>
									{#each use.go as gu (gu.canonical_id)}
										<tr>
											<td class="text-xs"><a href={href(gu.canonical_id)} class="hover:text-primary">{name(gu.canonical_id)}</a></td>
											<td class="num text-xs">{fmtUsd(gu.monthly_limit_usd, 0)}</td>
											<td class="num text-xs">{fmtUsd(gu.planning_cost_usd, 2)}</td>
											<td class="num text-xs">{fmtPct(gu.share_of_monthly_limit)}</td>
											<td class="num text-xs">{gu.weeks_needed}</td>
											<td class="num text-xs">{gu.runs_per_5h ?? '—'}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}
	</div>

	<!-- Higher-ranked but not picked -->
	{#if payload.blocked.length}
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="text-sm font-medium">Higher-ranked but not picked</Card.Title>
				<Card.Description class="text-xs">Eligible models ranked above the last pick of their tier, with the rule that blocked them</Card.Description>
			</Card.Header>
			<div class="overflow-x-auto">
				<table class="w-full">
					<thead>
						<tr><th>Tier</th><th class="num">#</th><th>Model</th><th class="num">MSS<sub>tier</sub></th><th class="num">MSS</th><th>Blocked by</th></tr>
					</thead>
					<tbody>
						{#each payload.blocked as b (b.canonical_id)}
							{@const why = describeBlocked(b.blocked_by)}
							<tr>
								<td class="text-xs">{tierLabel(b.tier)}</td>
								<td class="num text-xs text-muted-foreground">{b.tier_rank}</td>
								<td class="max-w-[16rem]">{@render modelCell(b.canonical_id)}</td>
								<ScoreCell value={b.mss_tier} />
								<ScoreCell value={b.mss} />
								<td class="text-xs" title={b.blocked_by}>
									{why.label}
									{#if why.detail}<span class="cell-sub block text-[11px] text-muted-foreground">{why.detail}</span>{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Root>
	{/if}

	<!-- Alternates -->
	{#if sel && (payload.alternates?.next_in_tier || payload.alternates?.replacements?.length)}
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="text-sm font-medium">Alternates</Card.Title>
				<Card.Description class="text-xs">The swap menu for confirmation: who enters when a pick is left out, and the next models in each tier</Card.Description>
			</Card.Header>
			<Card.Content class="grid grid-cols-[minmax(0,1fr)] gap-0 p-0 lg:grid-cols-2 lg:divide-x lg:divide-border">
				<div class="overflow-x-auto">
					<table class="w-full">
						<thead><tr><th>Pick</th><th>Replaced by</th></tr></thead>
						<tbody>
							{#each picks as p (p.canonical_id)}
								{@const r = replacements.get(p.canonical_id)}
								<tr>
									<td class="text-xs"><span class="text-muted-foreground">{tierLabel(p.tier)} ·</span> {name(p.canonical_id)}</td>
									<td class="text-xs">
										{#if !r}
											<span class="text-muted-foreground">—</span>
										{:else if !r.feasible || !r.with}
											<span class="text-muted-foreground">no feasible set without it</span>
										{:else}
											{r.with.map(name).join(', ')}
											{#if r.also_leaves?.length}<span class="cell-sub block text-[11px] text-muted-foreground">also leaves: {r.also_leaves.map(name).join(', ')}</span>{/if}
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<div class="overflow-x-auto border-t border-border lg:border-t-0">
					<table class="w-full">
						<thead><tr><th>Next in tier</th><th class="num">MSS<sub>tier</sub></th><th>Route</th><th>Selectable</th></tr></thead>
						<tbody>
							{#each Object.entries(payload.alternates?.next_in_tier ?? {}) as [tier, rows] (tier)}
								{#each rows ?? [] as a (a.canonical_id)}
									{@const why = a.note ? describeBlocked(a.note) : null}
									<tr>
										<td class="text-xs"><span class="text-muted-foreground">{tierLabel(tier)} #{a.tier_rank} ·</span> {name(a.canonical_id)}</td>
										<td class="num text-xs">{a.mss_tier != null ? (a.mss_tier * 100).toFixed(1) : NO_DATA}</td>
										<td class="text-xs">{routeLabel(a.route)}</td>
										<td class="text-xs" title={a.note ?? undefined}>
											{#if a.selectable}<span class="text-success-fg">yes</span>{:else}<span class="text-muted-foreground">no{why ? ` · ${why.label}` : ''}</span>{/if}
										</td>
									</tr>
								{/each}
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

	<!-- Sensitivity -->
	{#if payload.sensitivity}
		{@const s = payload.sensitivity}
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="text-sm font-medium">Sensitivity</Card.Title>
				<Card.Description class="text-xs">
					The stratified procedure rerun under perturbed weights and specifications. Robust ≥ 70% of draws, borderline 40–70%, fragile below; non-picks at ≥ 40% are contenders.
					{#if payload.preview?.draws_capped}<b class="font-medium text-foreground">Preview: draws capped</b> (S1 {payload.preview.draws.S1 ?? 0}, S2 {payload.preview.draws.S2 ?? 0}, S3 {payload.preview.draws.S3 ?? 0}).{/if}
				</Card.Description>
			</Card.Header>
			<div class="overflow-x-auto">
				<table class="w-full">
					<thead>
						<tr>
							<th>Run</th>
							<th class="num">Draws</th>
							<th class="num" title="Draws with no feasible set">Infeasible</th>
							<th class="num" title="Jaccard overlap with the nominal set: mean / 5th percentile">Jaccard</th>
							<th class="num" title="Kendall τ_b of MSS_tier within each price tier, averaged over the tiers: mean / 5th percentile">τ<sub>b</sub></th>
							<th class="num" title="Rank-biased overlap (p = 0.9) within each price tier, averaged over the tiers: mean / 5th percentile">RBO</th>
						</tr>
					</thead>
					<tbody>
						{#each [s.S1, s.S2, s.S3] as run, i (i)}
							{#if run}
								<tr>
									<td class="text-xs"><span class="font-mono">{run.run}</span> <span class="text-muted-foreground">{run.description}</span></td>
									<td class="num text-xs">{run.draws.toLocaleString()}</td>
									<td class="num text-xs">{run.infeasible_draws}</td>
									<td class="num text-xs">{fmtNum(run.jaccard_mean)} / {fmtNum(run.jaccard_p5)}</td>
									<td class="num text-xs">{fmtNum(run.kendall_tau_b_mean)} / {fmtNum(run.kendall_tau_b_p5)}</td>
									<td class="num text-xs">{fmtNum(run.rbo_mean)} / {fmtNum(run.rbo_p5)}</td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
			{#if freq.length}
				<div class="overflow-x-auto border-t border-border">
					<table class="w-full">
						<thead>
							<tr><th>Selection frequency</th><th>Class (S1)</th><th class="num">S1</th><th class="num">S2</th><th class="num">S3</th></tr>
						</thead>
						<tbody>
							{#each freq as f (f.canonical_id)}
								<tr>
									<td class="text-xs">{name(f.canonical_id)}{#if !pickIds.has(f.canonical_id)}<span class="text-muted-foreground"> · not picked</span>{/if}</td>
									<td>{#if f.cls}<StatusBadge status={f.cls} tone={robustnessTone(f.cls)} />{:else}<span class="text-[11px] text-muted-foreground">—</span>{/if}</td>
									<td class="num text-xs">{fmtPct(f.s1)}</td>
									<td class="num text-xs">{fmtPct(f.s2)}</td>
									<td class="num text-xs">{fmtPct(f.s3)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
			{#if s.S4 || s.S5 || s.S6}
				<div class="grid grid-cols-[minmax(0,1fr)] gap-4 border-t border-border p-4 text-xs md:grid-cols-3">
					{#each [s.S4, s.S5, s.S6] as run, i (i)}
						{#if run}
							<div class="min-w-0">
								<p class="section-label mb-1.5"><span class="font-mono">{run.run}</span> · {run.description}</p>
								<ul class="space-y-1">
									{#each run.changes as ch (ch.change)}
										<li>
											<span>{ch.change}</span>
											{#if !ch.feasible}
												<span class="text-danger-fg"> · infeasible</span>
											{:else if ch.entered.length === 0 && ch.left.length === 0}
												<span class="text-muted-foreground"> · no change</span>
											{:else}
												<span class="block text-[11px] text-muted-foreground">+ {ch.entered.map(name).join(', ')} · − {ch.left.map(name).join(', ')}</span>
											{/if}
										</li>
									{/each}
								</ul>
							</div>
						{/if}
					{/each}
				</div>
			{/if}
		</Card.Root>
	{/if}

	<!-- Inputs and rules -->
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="border-b border-border py-3">
			<Card.Title class="text-sm font-medium">Inputs and rules</Card.Title>
			<Card.Description class="text-xs">{payload.version} · as of {fmtDate(payload.as_of)}</Card.Description>
		</Card.Header>
		<Card.Content class="grid grid-cols-[minmax(0,1fr)] gap-4 p-4 text-xs lg:grid-cols-3">
			<div class="space-y-1.5">
				<p class="section-label">Weights</p>
				<dl class="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 gap-y-0.5">
					<dt class="text-muted-foreground"></dt><dd class="text-right text-muted-foreground">MSS</dd><dd class="text-right text-muted-foreground">MSS<sub>tier</sub></dd>
					{#each COMPONENTS as c (c.key)}
						<dt>{c.label}</dt>
						<dd class="text-right font-mono tabular-nums">{weightLabel(weights.global[c.key])}</dd>
						<dd class="text-right font-mono tabular-nums">{weightLabel(weights.tier[c.key])}</dd>
					{/each}
				</dl>
			</div>
			<div class="space-y-1.5">
				<p class="section-label">Price tiers</p>
				<p class="text-muted-foreground">{payload.tiers?.rule ?? '—'}</p>
				<ul class="font-mono tabular-nums">
					{#each PRICE_TIERS as t (t)}
						<li>{tierLabel(t)} {tierBand(payload.tiers, t) || '—'} · {tierCounts[t] ?? 0} models</li>
					{/each}
				</ul>
			</div>
			<div class="space-y-1.5">
				<p class="section-label">Quality floor</p>
				{#if payload.floor}
					<p>
						Capability ≥ tier percentile
						{#each Object.entries(payload.floor.levels) as [t, lvl] (t)}<span class="mr-2 font-mono">{tierLabel(t)} p{lvl}</span>{/each}
					</p>
					<p class="text-muted-foreground">
						Missing capability admitted with popularity ≥ {payload.floor.popularity_cut_for_missing_capability != null ? (payload.floor.popularity_cut_for_missing_capability * 100).toFixed(1) : '—'} (top quartile).
					</p>
					{#if payload.floor.relaxations.length}
						<ul class="text-warning-fg">
							{#each payload.floor.relaxations as r, i (i)}<li>{tierLabel(r.tier)}: p{r.from_percentile} → p{r.to_percentile} ({r.reason})</li>{/each}
						</ul>
					{:else}
						<p class="text-muted-foreground">No relaxation needed.</p>
					{/if}
				{:else}
					<p class="text-muted-foreground">Off.</p>
				{/if}
			</div>
			{#if payload.validity}
				{@const v = payload.validity}
				<div class="space-y-1.5 lg:col-span-3">
					<p class="section-label">Popularity validity</p>
					<p>
						Spearman ρ with LMArena interactions
						<b class="font-mono font-medium">{fmtNum(v.spearman_popularity_vs_lmarena_interactions?.rho)}</b>
						<span class="text-muted-foreground">(n = {v.spearman_popularity_vs_lmarena_interactions?.n ?? 0})</span>;
						with Hugging Face downloads of open-weight models
						<b class="font-mono font-medium">{fmtNum(v.spearman_popularity_vs_hf_downloads_open_weight?.rho)}</b>
						<span class="text-muted-foreground">(n = {v.spearman_popularity_vs_hf_downloads_open_weight?.n ?? 0})</span>.
					</p>
				</div>
			{/if}
			{#if payload.sum_optimal}
				<p class="lg:col-span-3 {payload.sum_optimal.differs_from_greedy ? 'text-warning-fg' : 'text-muted-foreground'}">
					{payload.sum_optimal.differs_from_greedy
						? `The set maximising Σ MSS_tier (${payload.sum_optimal.sum_mss_tier.toFixed(3)}) differs from the greedy set (${payload.sum_optimal.sum_mss_tier_greedy.toFixed(3)}): ${payload.sum_optimal.set.filter((id) => !pickIds.has(id)).map(name).join(', ')}.`
						: `The greedy set is also the Σ MSS_tier optimum (${payload.sum_optimal.sum_mss_tier.toFixed(3)}${payload.sum_optimal.exhaustive ? ', exhaustive search' : ''}).`}
					{#if sel?.ties_at_4dp.length}Ties at 4 dp: {sel.ties_at_4dp.map((t) => t.map(name).join(' = ')).join('; ')}.{/if}
				</p>
			{/if}
		</Card.Content>
		<div class="border-t border-border">
			<p class="section-label px-4 pt-3">Sources</p>
			<SourcesTable sources={payload.sources} />
		</div>
		<details class="border-t border-border px-4 py-3">
			<summary class="cursor-pointer text-xs text-muted-foreground hover:text-foreground">Frame flow ({payload.flow.length} steps, {payload.flow.at(-1)?.remaining ?? 0} eligible)</summary>
			<FlowList flow={payload.flow} class="mt-3 max-w-2xl" />
		</details>
	</Card.Root>
</div>

<style>
	.comp-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(3.25rem, auto));
		gap: 0.75rem;
	}
	.comp {
		display: grid;
		grid-template-columns: auto 1fr;
		align-items: baseline;
		column-gap: 0.25rem;
		font-family: var(--font-mono);
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.comp-letter {
		font-size: 11px;
		color: var(--muted-foreground);
	}
	.comp-val {
		text-align: right;
	}
	.comp-none {
		font-size: 11px;
		color: var(--muted-foreground);
	}
	.comp-bar {
		grid-column: 2;
		display: block;
		height: 3px;
		overflow: hidden;
		border-radius: 9999px;
		background: var(--muted);
	}
	.comp-bar > span {
		display: block;
		height: 100%;
		border-radius: 9999px;
		background: color-mix(in oklab, var(--muted-foreground) 45%, transparent);
	}
</style>
