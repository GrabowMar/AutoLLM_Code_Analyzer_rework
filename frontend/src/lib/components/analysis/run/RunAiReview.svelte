<script lang="ts">
	/**
	 * RunAiReview — the AI reviewer of one run: frozen configuration
	 * (engine, harness, model, prompt and schema hashes), status, consensus,
	 * grounding, agreement between samples, overlap with static issues, cost
	 * and tokens, every review sample (coverage, providers, artifacts) and
	 * the consensus clusters. AI findings are opinions: shown apart from the
	 * static measurement.
	 *
	 * Props: `run`, `revision`, `onfindings(kind, issueKey)`,
	 * `onsample(index)` (findings of one sample).
	 */
	import { untrack } from 'svelte';
	import type { AnalysisRunDetail, IssuePage, ReviewSample } from '$lib/api/analysis';
	import {
		artifactUrl,
		GROUNDINGS,
		isMeasurement,
		isRunActive,
		isToolOk,
		listRunIssues
	} from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { groundingTones, severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import CopyValue from '../CopyValue.svelte';
	import Hint from '../Hint.svelte';
	import NaValue from '../NaValue.svelte';
	import {
		categoryLabel,
		failureReasonLabel,
		fmtBytes,
		fmtInt,
		fmtNum,
		fmtRate,
		fmtSeconds,
		fmtUsd,
		outcomeNaReason
	} from '../run-utils';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Download from '@lucide/svelte/icons/download';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import Sparkles from '@lucide/svelte/icons/sparkles';

	interface Props {
		run: AnalysisRunDetail;
		revision: string;
		onfindings?: (kind: 'ai', issueKey: string) => void;
		onsample?: (index: number) => void;
	}

	let { run, revision, onfindings, onsample }: Props = $props();

	const result = $derived(run.results.find((r) => r.tool_kind === 'ai') ?? null);
	const reviewer = $derived(run.plan?.reviewer ?? null);
	const m = $derived(isMeasurement(run.measurement) ? run.measurement : null);
	const ai = $derived(m?.ai ?? null);
	const agreement = $derived(result?.metrics?.agreement ?? null);
	const usage = $derived(result?.metrics?.usage ?? null);
	const samples = $derived([...run.review_samples].sort((a, b) => a.index - b.index));
	const active = $derived(isRunActive(run.status));
	const ok = $derived(!!result && isToolOk(result.status));

	const grounding = $derived(ai?.grounding ?? agreement?.grounding ?? null);
	const groundingTotal = $derived(
		grounding ? GROUNDINGS.reduce((n, g) => n + (grounding[g] ?? 0), 0) : 0
	);

	// Consensus clusters (the counted AI issues).
	let clusters = $state<IssuePage | null>(null);
	let clustersError = $state('');
	let clusterPage = $state(1);
	let controller: AbortController | null = null;

	async function loadClusters() {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		try {
			clusters = await listRunIssues(
				run.id,
				{ kind: 'ai', page: clusterPage, per_page: 25 },
				{ signal: mine.signal }
			);
			clustersError = '';
		} catch (e) {
			if (isAbortError(e)) return;
			if (!clusters) clustersError = formatApiError(e, 'Could not load the consensus clusters.');
		}
	}

	$effect(() => {
		void revision;
		void clusterPage;
		if (!result || !ok) return;
		untrack(() => void loadClusters());
		return () => controller?.abort();
	});

	let expanded = $state<Set<string>>(new Set());
	function toggle(id: string) {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}

	function filesSent(s: ReviewSample): string {
		const sent = s.coverage?.files_sent?.length;
		const scope = s.coverage?.files_in_scope;
		if (sent == null && scope == null) return '—';
		return `${sent ?? '—'}/${scope ?? '—'}`;
	}

	function entries(record: Record<string, number> | undefined | null): [string, number][] {
		return Object.entries(record ?? {}).sort((a, b) => b[1] - a[1]);
	}

	const overlap = $derived(ai?.overlap_with_static ?? null);
</script>

{#if !result && !reviewer}
	<EmptyState
		icon={Sparkles}
		title="No AI reviewer in this plan"
		description="The profile this run used has the AI reviewer turned off; only static tools ran."
	/>
{:else}
	<div class="space-y-6">
		<!-- Status + headline numbers -->
		<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
			<div class="kpi-card">
				<span class="kpi-label">Status</span>
				<div class="flex flex-wrap items-center gap-1.5 pt-1">
					{#if result}<StatusBadge status={result.status} />{/if}
					{#if result?.failure_reason}
						<span class="text-xs text-danger-fg">{failureReasonLabel(result.failure_reason)}</span>
					{/if}
				</div>
				<p class="text-[11px] text-muted-foreground sm:text-xs">
					{agreement?.successful_samples ?? ai?.samples_ok ?? '—'} of {reviewer?.samples ??
						ai?.samples ??
						'—'} samples ok
				</p>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Consensus</span>
				<span class="kpi-value text-2xl sm:text-3xl">
					{#if ai?.available}{ai.consensus.total}{:else}<NaValue
							reason={m
								? outcomeNaReason('ai_consensus_per_kloc', m)
								: active
									? 'The review has not finished'
									: 'The review did not succeed'}
						/>{/if}
				</span>
				<p class="text-[11px] text-muted-foreground sm:text-xs">
					{#if ai?.available}{fmtNum(ai.per_kloc, 1)} / KLOC · threshold {ai.threshold ??
							'—'}{:else}counted clusters{/if}
				</p>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Grounding</span>
				<span class="kpi-value text-2xl sm:text-3xl">
					{#if (ai?.grounding_rate ?? agreement?.grounding_rate) != null}
						{fmtRate(ai?.grounding_rate ?? agreement?.grounding_rate)}
					{:else}
						<NaValue reason="No findings to ground" />
					{/if}
				</span>
				<p class="text-[11px] text-muted-foreground sm:text-xs">exact + relocated evidence</p>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Agreement</span>
				<span class="kpi-value text-2xl sm:text-3xl">
					{#if (ai?.agreement.mean_pairwise_jaccard ?? agreement?.mean_pairwise_jaccard) != null}
						{fmtNum(ai?.agreement.mean_pairwise_jaccard ?? agreement?.mean_pairwise_jaccard, 2)}
					{:else}
						<NaValue reason="Agreement needs at least two successful samples" />
					{/if}
				</span>
				<p class="text-[11px] text-muted-foreground sm:text-xs">
					mean pairwise Jaccard{#if agreement?.mean_pairwise_jaccard_grounded != null}{' · '}grounded
						{fmtNum(agreement.mean_pairwise_jaccard_grounded, 2)}{/if}
				</p>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Cost</span>
				<span class="kpi-value text-2xl sm:text-3xl">
					{#if (ai?.cost_usd ?? usage?.cost_usd) != null}{fmtUsd(
							ai?.cost_usd ?? usage?.cost_usd
						)}{:else}<NaValue reason="No replicate reported a cost" />{/if}
				</span>
				<p class="text-[11px] text-muted-foreground sm:text-xs">
					{#if usage?.cost_estimated}estimated from catalog prices{:else}{fmtInt(usage?.llm_calls)} LLM
						calls{/if}
				</p>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Tokens</span>
				<span class="kpi-value text-2xl sm:text-3xl"
					>{fmtInt(ai?.tokens.total ?? usage?.total_tokens)}</span
				>
				<p class="text-[11px] text-muted-foreground sm:text-xs">
					{fmtInt(ai?.tokens.prompt ?? usage?.prompt_tokens)} in · {fmtInt(
						ai?.tokens.completion ?? usage?.completion_tokens
					)} out · {fmtInt(ai?.tokens.reasoning ?? usage?.reasoning_tokens)} reasoning
				</p>
			</div>
		</div>

		{#if result?.error_message}
			<p class={cn('text-sm', ok ? 'text-muted-foreground' : 'text-danger-fg')}>
				{result.error_message}
			</p>
		{/if}
		{#if agreement?.stopped_early}
			<p class="text-sm text-warning-fg">
				Replicates stopped early: {agreement.stopped_early.replace(/_/g, ' ')}.
			</p>
		{/if}

		<div class="grid gap-4 lg:grid-cols-2">
			<!-- Frozen configuration -->
			<Card.Root>
				<Card.Header>
					<Card.Title>Reviewer</Card.Title>
					<Card.Description
						>Frozen into the plan at submission; identical plans are comparable.</Card.Description
					>
				</Card.Header>
				<Card.Content>
					{#if reviewer}
						<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
							<dt class="text-muted-foreground">Engine</dt>
							<dd>
								{reviewer.engine.replace(/_/g, '-')}
								<span class="text-xs text-muted-foreground">({reviewer.engine_version})</span>
							</dd>
							{#if reviewer.harness}
								<dt class="text-muted-foreground">Harness</dt>
								<dd>
									{reviewer.harness}
									<span class="text-xs text-muted-foreground">{reviewer.harness_version}</span>
								</dd>
							{/if}
							<dt class="text-muted-foreground">Model</dt>
							<dd class="min-w-0">
								<span class="break-all font-mono text-xs">{reviewer.model.slug}</span>
								<span class="block text-[11px] text-muted-foreground">
									{reviewer.model.canonical_slug} · {reviewer.model.provider} · context
									{fmtInt(reviewer.model.context_window)}
								</span>
							</dd>
							<dt class="text-muted-foreground">Pricing</dt>
							<dd class="text-xs">
								${fmtNum(reviewer.model.pricing.input_per_token * 1e6, 3)} / ${fmtNum(
									reviewer.model.pricing.output_per_token * 1e6,
									3
								)}
								per 1M tokens in / out
								<span class="block text-[11px] text-muted-foreground"
									>snapshot {new Date(reviewer.model.pricing.snapshot_at).toLocaleString()}</span
								>
							</dd>
							<dt class="text-muted-foreground">Samples</dt>
							<dd>
								k = {reviewer.samples} · quorum {reviewer.quorum} · consensus ≥ {reviewer.consensus
									.threshold}
							</dd>
							<dt class="text-muted-foreground">Sampling</dt>
							<dd class="text-xs">
								temperature {reviewer.params.temperature ?? 'provider default'} · max tokens {fmtInt(
									reviewer.params.max_tokens
								)}
								· reasoning {reviewer.params.reasoning?.effort ?? 'model default'} · seed {reviewer
									.params.seed_base}{reviewer.params.seed_forwarded ? '' : ' (not forwarded)'}
								{#if Object.keys(reviewer.params.dropped ?? {}).length > 0}
									<span class="block text-warning-fg">
										Dropped (unsupported by the model): {Object.entries(reviewer.params.dropped)
											.map(([k, v]) => `${k} (${v})`)
											.join(', ')}
									</span>
								{/if}
							</dd>
							<dt class="text-muted-foreground">Output</dt>
							<dd class="text-xs">{reviewer.output_mode.replace(/_/g, ' ')}</dd>
							<dt class="text-muted-foreground">Prompt</dt>
							<dd class="min-w-0">
								<span class="text-xs">{reviewer.prompt_version}</span>
								<CopyValue
									value={reviewer.prompt_sha256}
									display={reviewer.prompt_sha256.slice(0, 12)}
									label="prompt sha256"
								/>
							</dd>
							<dt class="text-muted-foreground">Schema</dt>
							<dd class="min-w-0">
								<span class="text-xs">{reviewer.schema_version}</span>
								<CopyValue
									value={reviewer.schema_sha256}
									display={reviewer.schema_sha256.slice(0, 12)}
									label="schema sha256"
								/>
							</dd>
							<dt class="text-muted-foreground">Reviewed code</dt>
							<dd class="text-xs">
								origins {reviewer.include_origins.join(', ')} · kinds {reviewer.include_kinds.join(
									', '
								)} · chunks ≤
								{fmtInt(reviewer.chunk_char_budget)} chars
							</dd>
							<dt class="text-muted-foreground">Budget / sample</dt>
							<dd class="text-xs">
								{fmtUsd(reviewer.budget.max_cost_usd)} · {reviewer.budget.max_llm_requests} requests ·
								{fmtSeconds(reviewer.budget.wall_clock_seconds)}
							</dd>
							<dt class="text-muted-foreground">Routing</dt>
							<dd class="text-xs">
								{reviewer.provider_routing_source} ·
								{reviewer.provider?.require_parameters
									? 'require parameters'
									: 'parameters optional'} ·
								{reviewer.provider?.allow_fallbacks === false
									? 'no fallbacks'
									: 'fallbacks allowed'}
							</dd>
						</dl>
						{#if reviewer.notes?.length}
							<ul class="mt-3 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
								{#each reviewer.notes as note, i (i)}<li>{note}</li>{/each}
							</ul>
						{/if}
					{:else}
						<p class="text-sm text-muted-foreground">The plan has no reviewer section.</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<div class="space-y-4">
				<!-- Grounding -->
				<Card.Root>
					<Card.Header>
						<Card.Title>Grounding</Card.Title>
						<Card.Description>
							Evidence quoted by the model, checked against the code. Only exact and relocated
							findings can count.
						</Card.Description>
					</Card.Header>
					<Card.Content>
						{#if grounding && groundingTotal > 0}
							<table class="text-sm">
								<thead>
									<tr
										><th>Verdict</th><th class="text-right">Findings</th><th class="text-right"
											>Share</th
										></tr
									>
								</thead>
								<tbody>
									{#each GROUNDINGS as g (g)}
										<tr>
											<td><StatusBadge status={g} tones={groundingTones} /></td>
											<td class="text-right tabular-nums">{grounding[g] ?? 0}</td>
											<td class="text-right tabular-nums"
												>{fmtRate((grounding[g] ?? 0) / groundingTotal)}</td
											>
										</tr>
									{/each}
								</tbody>
							</table>
						{:else}
							<p class="text-sm text-muted-foreground">
								{active
									? 'Available when the review finishes.'
									: 'No sample findings were grounded.'}
							</p>
						{/if}
					</Card.Content>
				</Card.Root>

				<!-- Overlap with static -->
				<Card.Root>
					<Card.Header>
						<Card.Title>Overlap with static issues</Card.Title>
						<Card.Description
							>AI consensus vs counted static issues on the same lines (±{overlap?.window ??
								3}).</Card.Description
						>
					</Card.Header>
					<Card.Content>
						{#if overlap && ai?.available}
							<dl class="grid grid-cols-3 gap-3 text-center">
								<div>
									<dt class="section-label">Jaccard</dt>
									<dd class="text-lg font-semibold tabular-nums">
										{fmtNum(overlap.jaccard, 2)}
									</dd>
								</div>
								<div>
									<dt class="section-label">AI confirmed</dt>
									<dd class="text-lg font-semibold tabular-nums">
										{fmtRate(overlap.ai_confirmed_share)}
									</dd>
								</div>
								<div>
									<dt class="section-label">Static found by AI</dt>
									<dd class="text-lg font-semibold tabular-nums">
										{fmtRate(overlap.static_found_by_ai_share)}
									</dd>
								</div>
							</dl>
							{#if Object.keys(overlap.by_category ?? {}).length > 0}
								<table class="mt-3 text-sm">
									<thead>
										<tr>
											<th>Category</th>
											<th class="text-right">Static</th>
											<th class="text-right">AI</th>
											<th class="text-right">Matched</th>
										</tr>
									</thead>
									<tbody>
										{#each Object.entries(overlap.by_category) as [cat, row] (cat)}
											<tr>
												<td>{categoryLabel(cat)}</td>
												<td class="text-right tabular-nums">{row.static}</td>
												<td class="text-right tabular-nums">{row.ai}</td>
												<td class="text-right tabular-nums">{row.matched}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							{/if}
						{:else}
							<p class="text-sm text-muted-foreground">
								{active
									? 'Available when the run finishes.'
									: 'Not available: the AI review did not succeed.'}
							</p>
						{/if}
					</Card.Content>
				</Card.Root>
			</div>
		</div>

		<!-- Samples -->
		<Card.Root>
			<Card.Header>
				<Card.Title>Review samples</Card.Title>
				<Card.Description>
					Independent replicates (shuffled file order per seed). Expand a sample for coverage and
					raw responses.
				</Card.Description>
			</Card.Header>
			<Card.Content class="p-0">
				{#if samples.length === 0}
					<div class="px-4 pb-2">
						<EmptyState
							compact
							icon={Hourglass}
							title={active ? 'No sample has finished yet' : 'No review sample was recorded'}
						/>
					</div>
				{:else}
					<div class="table-scroll-wrapper">
						<table class="min-w-full">
							<thead>
								<tr>
									<th class="w-8 pr-0"><span class="sr-only">Expand</span></th>
									<th>Sample</th>
									<th>Status</th>
									<th class="text-right">Findings</th>
									<th class="hidden md:table-cell">Grounding</th>
									<th class="hidden text-right md:table-cell">Files sent</th>
									<th class="hidden text-right lg:table-cell">Tokens</th>
									<th class="hidden text-right lg:table-cell">Cost</th>
									<th class="hidden text-right sm:table-cell">Latency</th>
								</tr>
							</thead>
							<tbody>
								{#each samples as s (s.id)}
									{@const open = expanded.has(s.id)}
									<tr>
										<td class="w-8 pr-0">
											<button
												type="button"
												class="inline-flex size-6 cursor-pointer items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
												onclick={() => toggle(s.id)}
												aria-expanded={open}
												aria-controls="sample-{s.id}"
												aria-label="{open ? 'Hide' : 'Show'} details of sample {s.index}"
											>
												<ChevronRight
													class={cn(
														'size-3.5 transition-transform duration-200',
														open && 'rotate-90'
													)}
													aria-hidden="true"
												/>
											</button>
										</td>
										<td class="whitespace-nowrap">
											<span class="font-mono text-xs">#{s.index}</span>
											{#if s.repaired}
												<Hint
													text="The answer failed validation and was fixed by one repair call"
													underline={false}
												>
													<Badge variant="outline" class="ml-1">repaired</Badge>
												</Hint>
											{/if}
										</td>
										<td>
											<div class="flex flex-col items-start gap-0.5">
												<StatusBadge status={s.status} />
												{#if s.failure_reason}<span class="text-[11px] text-danger-fg"
														>{failureReasonLabel(s.failure_reason)}</span
													>{/if}
											</div>
										</td>
										<td class="text-right tabular-nums">
											{#if onsample && s.finding_count > 0}
												<button
													type="button"
													class="cursor-pointer font-medium text-primary underline-offset-2 hover:underline"
													onclick={() => onsample(s.index)}
													aria-label="Show the {s.finding_count} findings of sample {s.index}"
													>{s.finding_count}</button
												>
											{:else}
												{s.finding_count}
											{/if}
										</td>
										<td class="hidden text-xs md:table-cell">
											{#if Object.keys(s.grounding ?? {}).length}
												{GROUNDINGS.filter((g) => (s.grounding[g] ?? 0) > 0)
													.map((g) => `${s.grounding[g]} ${g.replace(/_/g, ' ')}`)
													.join(' · ') || '—'}
											{:else}—{/if}
										</td>
										<td class="hidden text-right text-xs tabular-nums md:table-cell">
											{filesSent(s)}
											{#if (s.coverage?.files_not_sent?.length ?? 0) > 0}
												<span class="block text-warning-fg"
													>{s.coverage.files_not_sent?.length} not sent</span
												>
											{/if}
										</td>
										<td class="hidden text-right text-xs tabular-nums lg:table-cell"
											>{fmtInt(s.usage?.total_tokens)}</td
										>
										<td class="hidden text-right text-xs tabular-nums lg:table-cell">
											{fmtUsd(s.usage?.cost_usd ?? null)}{#if s.usage?.cost_estimated}<span
													class="text-muted-foreground">*</span
												>{/if}
										</td>
										<td class="hidden text-right text-xs tabular-nums sm:table-cell"
											>{fmtSeconds(s.latency_seconds)}</td
										>
									</tr>
									{#if open}
										<tr id="sample-{s.id}" class="hover:bg-transparent">
											<td colspan="9" class="bg-muted/20 px-3 py-3 sm:px-4">
												<div class="grid gap-4 text-xs lg:grid-cols-2">
													<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
														{#if s.error_message}
															<dt class="text-muted-foreground">Error</dt>
															<dd class="break-words text-danger-fg">
																{s.error_message}
															</dd>
														{/if}
														<dt class="text-muted-foreground">Model</dt>
														<dd class="break-all font-mono">
															{s.model_canonical_slug || s.model_slug}
														</dd>
														<dt class="text-muted-foreground">Providers</dt>
														<dd>
															{entries(s.providers)
																.map(([p, n]) => `${p} ×${n}`)
																.join(', ') || '—'}
														</dd>
														<dt class="text-muted-foreground">Finish reasons</dt>
														<dd>
															{entries(s.finish_reasons)
																.map(([p, n]) => `${p} ×${n}`)
																.join(', ') || '—'}
														</dd>
														<dt class="text-muted-foreground">Calls</dt>
														<dd>
															{fmtInt(s.usage?.llm_calls)} ({fmtInt(s.usage?.llm_calls_failed)} failed,
															{fmtInt(s.usage?.retries)} retries)
														</dd>
														<dt class="text-muted-foreground">Tokens</dt>
														<dd>
															{fmtInt(s.usage?.prompt_tokens)} in · {fmtInt(
																s.usage?.completion_tokens
															)} out · {fmtInt(s.usage?.reasoning_tokens)} reasoning · {fmtInt(
																s.usage?.cached_tokens
															)} cached
														</dd>
														<dt class="text-muted-foreground">File order seed</dt>
														<dd class="font-mono">
															{s.file_order_seed ?? '—'}
														</dd>
														<dt class="text-muted-foreground">Chunks</dt>
														<dd>
															{s.coverage?.chunks_completed ?? '—'}/{s.coverage?.chunks ?? '—'} completed
															·
															{fmtInt(s.coverage?.chars_sent)} chars sent
														</dd>
														<dt class="text-muted-foreground">Review complete</dt>
														<dd>
															{s.coverage?.review_complete == null
																? '—'
																: s.coverage.review_complete
																	? 'yes'
																	: 'no (the model said it did not finish)'}
														</dd>
													</dl>
													<div class="space-y-3">
														{#if (s.coverage?.files_not_sent?.length ?? 0) > 0}
															<div>
																<p class="section-label">Files not sent</p>
																<ul class="mt-1 space-y-0.5 font-mono text-warning-fg">
																	{#each s.coverage.files_not_sent ?? [] as path (path)}<li
																			class="break-all"
																		>
																			{path}
																		</li>{/each}
																</ul>
															</div>
														{/if}
														{#if (s.coverage?.files_claimed_not_shown?.length ?? 0) > 0}
															<div>
																<p class="section-label">Cited but never shown to the model</p>
																<ul class="mt-1 space-y-0.5 font-mono text-warning-fg">
																	{#each s.coverage.files_claimed_not_shown ?? [] as path (path)}<li
																			class="break-all"
																		>
																			{path}
																		</li>{/each}
																</ul>
															</div>
														{/if}
														<div>
															<p class="section-label">Raw responses</p>
															{#if s.artifacts.length === 0}
																<p class="mt-1 text-muted-foreground">None stored.</p>
															{:else}
																<ul class="mt-1 space-y-1">
																	{#each s.artifacts as a (a.name)}
																		<li class="flex flex-wrap items-center gap-2">
																			<span class="font-mono">{a.name}</span>
																			<span class="text-muted-foreground">{fmtBytes(a.bytes)}</span>
																			<a
																				href={artifactUrl(a, {
																					download: true
																				})}
																				download
																				class="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-primary hover:bg-primary/10"
																			>
																				<Download class="size-3" aria-hidden="true" />Download<span
																					class="sr-only"
																				>
																					{a.name}</span
																				>
																			</a>
																		</li>
																	{/each}
																</ul>
															{/if}
														</div>
													</div>
												</div>
											</td>
										</tr>
									{/if}
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Consensus clusters -->
		<Card.Root>
			<Card.Header>
				<Card.Title>Consensus clusters</Card.Title>
				<Card.Description
					>The AI findings that count: grounded, and reported by at least the threshold of samples.</Card.Description
				>
			</Card.Header>
			<Card.Content class="p-0">
				{#if !ok}
					<p class="px-4 pb-2 text-sm text-muted-foreground">
						{active
							? 'Available when the review finishes.'
							: 'No consensus: the AI review did not succeed.'}
					</p>
				{:else if clustersError}
					<div class="px-4 pb-2">
						<ErrorAlert message={clustersError} onretry={loadClusters} />
					</div>
				{:else if !clusters}
					<p class="px-4 pb-2 text-sm text-muted-foreground" role="status">Loading…</p>
				{:else if clusters.items.length === 0}
					<p class="px-4 pb-2 text-sm text-muted-foreground">
						No finding reached consensus with grounded evidence (every sample finished).
					</p>
				{:else}
					<div class="table-scroll-wrapper">
						<table class="table-card-mobile">
							<thead>
								<tr><th>Severity</th><th>Finding</th><th>Location</th><th>Samples</th></tr>
							</thead>
							<tbody>
								{#each clusters.items as c (c.issue_key)}
									<tr class="relative">
										<td data-label="Severity"
											><Badge class={severityColors[c.severity]}>{c.severity}</Badge></td
										>
										<td data-label="Finding" class="max-w-md">
											<button
												type="button"
												class="block w-full cursor-pointer text-left after:absolute after:inset-0 after:content-[''] focus-visible:outline-none focus-visible:after:rounded focus-visible:after:ring-2 focus-visible:after:ring-ring/50"
												onclick={() => onfindings?.('ai', c.issue_key)}
											>
												<span class="line-clamp-2 text-sm font-medium">{c.title}</span>
												<span class="block text-[11px] text-muted-foreground">
													{categoryLabel(c.category)}{#if c.cwe.length}{' · '}<span
															class="font-mono">{c.cwe.join(', ')}</span
														>{/if}
												</span>
											</button>
										</td>
										<td data-label="Location" class="max-w-xs">
											<span class="block truncate font-mono text-xs" title={c.file_path}>
												{c.file_path}{c.line_start ? `:${c.line_start}` : ''}
											</span>
										</td>
										<td data-label="Samples" class="whitespace-nowrap font-mono text-xs">
											{c.samples.map((i) => `#${i}`).join(' ')}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					{#if clusters.pages > 1}
						<div
							class="flex items-center justify-between gap-2 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground"
						>
							<span>{clusters.total} clusters</span>
							<span class="flex gap-1">
								<Button
									variant="ghost"
									size="sm"
									class="h-6 px-2"
									disabled={clusterPage <= 1}
									onclick={() => clusterPage--}>Previous</Button
								>
								<Button
									variant="ghost"
									size="sm"
									class="h-6 px-2"
									disabled={clusterPage >= clusters.pages}
									onclick={() => clusterPage++}>Next</Button
								>
							</span>
						</div>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>
	</div>
{/if}
