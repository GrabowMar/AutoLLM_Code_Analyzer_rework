<script lang="ts">
	/**
	 * ProtocolView — the frozen pre-run protocol of a locked study, rendered
	 * for reading and citing: integrity (sha256 + verification), research
	 * questions, targets, exclusion rules, design, outcomes, statistical
	 * analysis plan, toolchain, AI reviewer, controls (self-test evidence),
	 * estimates, comparability, preflight at lock, threats to validity and
	 * platform provenance.
	 *
	 * Props: `envelope` (`GET /studies/{id}/protocol/`), `options` (target
	 * options, for readable target specs).
	 */
	import { outcomeLabel } from '$lib/components/insights/format';
	import type { StudyProtocolEnvelope, TargetOptions } from '$lib/api/analysis';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import HeatmapGrid from '$lib/components/charts/HeatmapGrid.svelte';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import IssueList from './IssueList.svelte';
	import NaValue from '../NaValue.svelte';
	import TargetReasons from './TargetReasons.svelte';
	import {
		CORRECTION_LABELS,
		DESIGN_FACTOR_LABELS,
		factorList,
		fmtDateTime,
		fmtInt,
		fmtSeconds,
		fmtUsd,
		humanize,
		shortHash,
		targetSpecSummary
	} from './format';

	interface Props {
		envelope: StudyProtocolEnvelope;
		options?: TargetOptions | null;
	}

	let { envelope, options = null }: Props = $props();

	const p = $derived(envelope.protocol);
	const plan = $derived(p.analysis_plan);
	const reviewer = $derived(p.reviewer);
	const designGroups = $derived(Object.keys(p.design.groups ?? {}));
	const designBlocks = $derived(Object.keys(p.design.blocks ?? {}));
	const resolved = $derived(p.targets.resolved);

	function yesNo(v: boolean | null | undefined): string {
		return v === true ? 'yes' : v === false ? 'no' : '—';
	}
</script>

{#snippet kv(label: string, value: string | number | null | undefined, mono = false)}
	<dt class="text-muted-foreground">{label}</dt>
	<dd class="min-w-0 break-words" class:font-mono={mono} class:text-xs={mono}>
		{#if value === null || value === undefined || value === ''}<NaValue />{:else}{value}{/if}
	</dd>
{/snippet}

<div class="space-y-4">
	<!-- Integrity -->
	<div
		class="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border px-4 py-3 text-sm {envelope.verified
			? 'border-success/40 bg-success/5'
			: 'border-destructive/40 bg-destructive/5'}"
		role="status"
	>
		<span class="inline-flex items-center gap-2 font-medium">
			{#if envelope.verified}
				<ShieldCheck class="h-4 w-4 text-success-fg" aria-hidden="true" />Protocol hash verified
			{:else}
				<ShieldAlert class="h-4 w-4 text-danger-fg" aria-hidden="true" />Integrity check failed: the
				stored protocol no longer matches its hash
			{/if}
		</span>
		<span class="text-xs text-muted-foreground">
			sha256 <code class="font-mono text-foreground" title={envelope.protocol_hash}
				>{shortHash(envelope.protocol_hash, 16)}</code
			>
		</span>
		<span class="text-xs text-muted-foreground">
			plan <code class="font-mono text-foreground" title={envelope.plan_hash}
				>{shortHash(envelope.plan_hash, 16)}</code
			>
		</span>
		<span class="text-xs text-muted-foreground"
			>Locked {fmtDateTime(envelope.locked_at)} · schema v{p.schema_version}</span
		>
	</div>

	<div class="grid gap-4 xl:grid-cols-2">
		<Card.Root>
			<Card.Header><Card.Title>Study</Card.Title></Card.Header>
			<Card.Content class="space-y-3">
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					{@render kv('Name', p.study.name)}
					{@render kv('Researcher', p.study.researcher?.name)}
					{@render kv('Created', fmtDateTime(p.study.created_at))}
				</dl>
				{#if p.study.description}<p class="text-sm text-muted-foreground">
						{p.study.description}
					</p>{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header><Card.Title>Research questions</Card.Title></Card.Header>
			<Card.Content>
				{#if p.research_questions.length === 0}
					<p class="text-sm text-muted-foreground">None recorded.</p>
				{:else}
					<ol class="space-y-2">
						{#each p.research_questions as rq (rq.id)}
							<li class="text-sm">
								<span class="mono-tag mr-1.5">{rq.id}</span>
								<StatusBadge
									status={rq.kind === 'confirmatory' ? 'locked' : 'draft'}
									label={rq.kind}
								/>
								<p class="mt-1">{rq.text}</p>
								{#if rq.hypothesis}<p class="text-xs text-muted-foreground">
										Hypothesis: {rq.hypothesis}
									</p>{/if}
								{#if rq.outcomes?.length}<p class="text-xs text-muted-foreground">
										Tests: {rq.outcomes.map((k) => outcomeLabel(k).label).join(', ')}
									</p>{/if}
							</li>
						{/each}
					</ol>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Targets -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Targets</Card.Title>
			<Card.Description>{targetSpecSummary(p.targets.spec, options)}</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			<dl class="flex flex-wrap gap-x-6 gap-y-1 text-sm">
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">Resolved</dt>
					<dd class="font-semibold tabular-nums">{p.targets.counts.total}</dd>
				</div>
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">Eligible</dt>
					<dd class="font-semibold tabular-nums text-success-fg">
						{p.targets.counts.eligible}
					</dd>
				</div>
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">Excluded</dt>
					<dd class="font-semibold tabular-nums">
						{p.targets.counts.excluded}
					</dd>
				</div>
			</dl>
			{#if Object.keys(p.targets.counts.by_reason).length > 0}
				<ul class="flex flex-wrap gap-1 text-[11px]">
					{#each Object.entries(p.targets.counts.by_reason) as [reason, n] (reason)}
						<li class="mono-tag">{reason.replace(/_/g, ' ')} · {n}</li>
					{/each}
				</ul>
			{/if}
			<details class="group rounded-md border border-border">
				<summary
					class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium"
				>
					<ChevronRight
						class="h-4 w-4 transition-transform duration-150 group-open:rotate-90"
						aria-hidden="true"
					/>
					Frozen target list ({resolved.length})
				</summary>
				<div class="table-scroll-wrapper border-t border-border">
					<table>
						<thead>
							<tr>
								<th scope="col">Target</th>
								<th scope="col">Group · block</th>
								<th scope="col" class="text-right">Files</th>
								<th scope="col" class="text-right">Measured SLOC</th>
								<th scope="col">Sample</th>
								<th scope="col">Eligibility</th>
							</tr>
						</thead>
						<tbody>
							{#each resolved as t (t.job_id)}
								<tr>
									<td class="max-w-[18rem]">
										<a
											class="block truncate hover:text-primary hover:underline"
											href="/applications/{t.job_id}"
											title={t.label}>{t.label}</a
										>
									</td>
									<td class="text-xs">{t.group}{t.block ? ` · ${t.block}` : ''}</td>
									<td class="text-right tabular-nums">{fmtInt(t.files)}</td>
									<td class="text-right tabular-nums">{fmtInt(t.sloc_measured)}</td>
									<td class="font-mono text-xs" title={t.sample_hash}
										>#{shortHash(t.sample_hash, 8)}</td
									>
									<td><TargetReasons eligible={t.eligible} reasons={t.reasons} /></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</details>
			<p class="text-xs text-muted-foreground">
				Exclusion rules: invalid {p.exclusion_rules.exclude_invalid ? 'excluded' : 'kept'} · empty output
				{p.exclusion_rules.exclude_empty_output ? 'excluded' : 'kept'} · truncated
				{p.exclusion_rules.exclude_truncated ? 'excluded' : 'kept'} · functional result
				{p.exclusion_rules.require_functional ? 'required' : 'not required'} · min measured SLOC {p
					.exclusion_rules.min_measured_sloc}.
			</p>
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 xl:grid-cols-2">
		<!-- Design -->
		<Card.Root>
			<Card.Header
				><Card.Title>Design</Card.Title><Card.Description>{p.design.reason}</Card.Description
				></Card.Header
			>
			<Card.Content class="space-y-3">
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					{@render kv('Group by', factorList(p.design.group_by))}
					{@render kv(
						'Block by',
						p.design.block_by ? DESIGN_FACTOR_LABELS[p.design.block_by] : 'none'
					)}
					{@render kv('Mode', p.design.mode)}
					{@render kv('Complete', yesNo(p.design.complete))}
					{@render kv(
						'Replicates',
						p.design.replicates ? `${p.design.replicates.min}–${p.design.replicates.max}` : null
					)}
				</dl>
				{#if designGroups.length > 0 && designBlocks.length > 0}
					<HeatmapGrid
						rows={designGroups}
						columns={designBlocks}
						values={designGroups.map((g) => designBlocks.map((b) => p.design.cells[g]?.[b] ?? 0))}
						format={(v) => String(v)}
						rowHeader="Group / block"
						legendLabel="Targets"
						label="Eligible targets per group and block"
					/>
				{/if}
				{#each p.design.notes as note, i (i)}<p class="text-xs text-warning-fg">
						{note}
					</p>{/each}
			</Card.Content>
		</Card.Root>

		<!-- Analysis plan -->
		<Card.Root>
			<Card.Header
				><Card.Title>Statistical analysis plan</Card.Title><Card.Description
					>Unit: {plan.unit}</Card.Description
				></Card.Header
			>
			<Card.Content>
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					{@render kv(
						'Omnibus',
						`${humanize(plan.omnibus)} (effect: ${humanize(plan.omnibus_effect_size)})`
					)}
					{@render kv(
						'Pairwise',
						`${humanize(plan.pairwise)} (${plan.pairwise_effect_sizes.map(humanize).join(', ')})`
					)}
					{@render kv('α', plan.alpha)}
					{@render kv(
						'Corrections',
						`pairwise ${CORRECTION_LABELS[plan.pairwise_correction]} · confirmatory ${CORRECTION_LABELS[plan.confirmatory_correction]} · exploratory ${CORRECTION_LABELS[plan.exploratory_correction]}`
					)}
					{@render kv('Cell aggregate', plan.cell_aggregate)}
					{@render kv('Descriptives', plan.descriptives)}
					{@render kv(
						'Intervals',
						`${plan.ci.method}, ${Math.round(plan.ci.level * 100)}%, ${fmtInt(plan.ci.resamples)} resamples${plan.ci.cluster ? `, clustered by ${plan.ci.cluster}` : ''}, seed ${plan.ci.seed}`
					)}
					{@render kv(
						'Effect CI',
						`${plan.effect_ci.method} (${fmtInt(plan.effect_ci.resamples)} resamples)`
					)}
					{@render kv('Proportions', plan.proportions)}
					{@render kv('AI reliability', plan.reliability)}
					{@render kv('Missing data', plan.missing_data)}
					{@render kv(
						'Magnitudes |δ|',
						Object.entries(plan.magnitude_thresholds)
							.sort((a, b) => a[1] - b[1])
							.map(([k, v]) => `${k} < ${v}`)
							.join(' · ')
					)}
				</dl>
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Outcomes -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Outcomes</Card.Title>
			<Card.Description>
				Primary outcome: <span class="font-medium text-foreground"
					>{p.outcomes.definitions.find((d) => d.key === p.outcomes.primary)?.label ??
						p.outcomes.primary}</span
				>
				· density NA below {p.outcomes.config.min_sloc_for_density ?? '—'} measured SLOC
			</Card.Description>
		</Card.Header>
		<Card.Content class="px-0">
			<div class="table-scroll-wrapper">
				<table>
					<thead>
						<tr>
							<th scope="col">Outcome</th>
							<th scope="col">Family</th>
							<th scope="col">Unit</th>
							<th scope="col">Direction</th>
							<th scope="col">Requires</th>
						</tr>
					</thead>
					<tbody>
						{#each p.outcomes.definitions as d (d.key)}
							<tr class:font-medium={d.primary}>
								<td>
									<span class="block">{d.label}{d.primary ? ' (primary)' : ''}</span>
									<span class="block font-mono text-[11px] text-muted-foreground">{d.key}</span>
								</td>
								<td
									><StatusBadge
										status={d.family === 'confirmatory' ? 'locked' : 'draft'}
										label={d.family}
									/></td
								>
								<td class="text-xs text-muted-foreground">{d.unit || '—'}</td>
								<td class="text-xs"
									>{d.higher_is_better === null
										? 'none'
										: d.higher_is_better
											? 'higher is better'
											: 'lower is better'}</td
								>
								<td class="text-xs text-muted-foreground">{d.requires.join(', ') || '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#each p.outcomes.notes as note, i (i)}<p class="px-4 pt-2 text-xs text-muted-foreground">
					{note}
				</p>{/each}
		</Card.Content>
	</Card.Root>

	<!-- Toolchain -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Toolchain</Card.Title>
			<Card.Description>
				Image <code class="font-mono">{p.analyzer_image.tag ?? '—'}</code> · engines
				{Object.entries(p.engine_versions)
					.map(([k, v]) => `${k} ${v}`)
					.join(' · ')}
			</Card.Description>
		</Card.Header>
		<Card.Content class="px-0">
			<div class="table-scroll-wrapper">
				<table>
					<thead>
						<tr>
							<th scope="col">Tool</th>
							<th scope="col">Category</th>
							<th scope="col">Version</th>
							<th scope="col">Config hash</th>
							<th scope="col">Spec hash</th>
							<th scope="col">Languages</th>
							<th scope="col">Self-test</th>
						</tr>
					</thead>
					<tbody>
						{#each p.toolchain as tool (tool.slug)}
							{@const control = p.controls.tools.find((c) => c.slug === tool.slug)}
							<tr>
								<td>
									<span class="font-medium">{tool.name}</span>
									{#if tool.required}<span class="ml-1 text-[11px] text-muted-foreground"
											>required</span
										>{/if}
								</td>
								<td class="text-xs text-muted-foreground">{tool.category} · {tool.kind}</td>
								<td class="font-mono text-xs">{tool.version || '—'}</td>
								<td class="font-mono text-xs" title={tool.config_hash}
									>#{shortHash(tool.config_hash, 10)}</td
								>
								<td class="font-mono text-xs" title={tool.spec_hash}
									>#{shortHash(tool.spec_hash, 10)}</td
								>
								<td class="text-xs text-muted-foreground">{tool.languages.join(', ') || 'any'}</td>
								<td>
									{#if control}
										<StatusBadge
											status={control.passed === true
												? 'passed'
												: control.passed === false
													? 'failed'
													: 'untested'}
										/>
									{:else}
										<NaValue reason="No self-test evidence recorded for this tool." />
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="px-4 pt-2 text-xs text-muted-foreground">
				Controls: {p.controls.source ? `from ${p.controls.source}` : 'no self-test evidence'} · overall
				{p.controls.passed === true
					? 'passed'
					: p.controls.passed === false
						? 'failed'
						: 'unknown'}.
			</p>
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 xl:grid-cols-2">
		<!-- Reviewer -->
		<Card.Root>
			<Card.Header><Card.Title>AI reviewer (frozen)</Card.Title></Card.Header>
			<Card.Content>
				{#if !reviewer}
					<p class="text-sm text-muted-foreground">No AI reviewer: static tools only.</p>
				{:else}
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
						{@render kv('Model', reviewer.model.slug, true)}
						{@render kv('Canonical', reviewer.model.canonical_slug, true)}
						{@render kv(
							'Engine',
							reviewer.engine === 'agent'
								? `agent · ${reviewer.harness} ${reviewer.harness_version}`
								: 'single-shot'
						)}
						{@render kv(
							'Samples · quorum',
							`k = ${reviewer.samples} · ${reviewer.quorum} (${reviewer.consensus.threshold})`
						)}
						{@render kv(
							'Parameters',
							`temperature ${reviewer.params.temperature ?? 'default'} · max ${fmtInt(reviewer.params.max_tokens)} tokens · reasoning ${reviewer.params.reasoning?.effort ?? 'default'} · seed ${reviewer.params.seed_base}`
						)}
						{@render kv('Output', reviewer.output_mode)}
						{@render kv(
							'Prompt',
							`${reviewer.prompt_version} #${shortHash(reviewer.prompt_sha256)}`,
							true
						)}
						{@render kv(
							'Schema',
							`${reviewer.schema_version} #${shortHash(reviewer.schema_sha256)}`,
							true
						)}
						{@render kv(
							'Reviews',
							`${reviewer.include_origins.join(', ')} files · ${fmtInt(reviewer.chunk_char_budget)} chars per chunk`
						)}
						{@render kv(
							'Price snapshot',
							`$${(reviewer.model.pricing.input_per_token * 1e6).toFixed(3)} in · $${(reviewer.model.pricing.output_per_token * 1e6).toFixed(3)} out per 1M (${fmtDateTime(reviewer.model.pricing.snapshot_at)})`
						)}
						{@render kv(
							'Budget / sample',
							`${fmtUsd(reviewer.budget.max_cost_usd)} · ${reviewer.budget.max_llm_requests} calls · ${fmtSeconds(reviewer.budget.wall_clock_seconds)}`
						)}
						{@render kv(
							'Provider routing',
							`${reviewer.provider_routing_source}: ${JSON.stringify(reviewer.provider)}`,
							true
						)}
					</dl>
					{#if Object.keys(reviewer.params.dropped ?? {}).length > 0}
						<p class="mt-2 text-xs text-warning-fg">
							Dropped (unsupported by the model): {Object.entries(reviewer.params.dropped)
								.map(([k, v]) => `${k} (${v})`)
								.join(', ')}
						</p>
					{/if}
					{#each reviewer.notes as note, i (i)}<p class="mt-1 text-xs text-muted-foreground">
							{note}
						</p>{/each}
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Estimates + comparability -->
		<Card.Root>
			<Card.Header><Card.Title>Estimates and comparability</Card.Title></Card.Header>
			<Card.Content class="space-y-3">
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					{@render kv(
						'Targets',
						`${fmtInt(p.estimates.eligible)} eligible of ${fmtInt(p.estimates.targets)}`
					)}
					{@render kv('Tool executions', fmtInt(p.estimates.tool_executions))}
					{@render kv('AI calls', fmtInt(p.estimates.ai_calls))}
					{@render kv(
						'AI cost',
						p.estimates.ai_cost_usd === null || p.estimates.ai_cost_usd === undefined
							? null
							: `${fmtUsd(p.estimates.ai_cost_usd)} (${fmtUsd(p.estimates.ai_cost_usd_low)} – ${fmtUsd(p.estimates.ai_cost_usd_high)})`
					)}
					{@render kv('Wall time', fmtSeconds(p.estimates.wall_seconds))}
				</dl>
				{#if p.comparability.values}
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
						{#each Object.entries(p.comparability.values) as [key, values] (key)}
							<dt class="text-muted-foreground">{humanize(key)}</dt>
							<dd class="flex flex-wrap items-center gap-1">
								{#each values as v (v)}<span class="mono-tag">{v}</span>{:else}<span
										class="text-muted-foreground">none</span
									>{/each}
								{#if p.comparability.mixed && key in p.comparability.mixed}<StatusBadge
										status="warning"
										label="mixed"
									/>{/if}
							</dd>
						{/each}
					</dl>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Preflight at lock -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Preflight at lock</Card.Title>
			<Card.Description
				>Generated {fmtDateTime(p.preflight.generated_at)} · image {p.preflight.image?.tag ??
					'—'}</Card.Description
			>
		</Card.Header>
		<Card.Content class="space-y-3">
			<IssueList
				issues={p.preflight.blocking}
				tone="danger"
				title="Blocking"
				empty="None (required to lock)."
			/>
			<IssueList issues={p.preflight.warnings} tone="warning" title="Warnings" empty="None." />
			<IssueList issues={p.preflight.info} tone="info" title="Info" />
		</Card.Content>
	</Card.Root>

	<!-- Threats -->
	<Card.Root>
		<Card.Header><Card.Title>Threats to validity</Card.Title></Card.Header>
		<Card.Content class="px-0">
			<div class="table-scroll-wrapper">
				<table>
					<thead
						><tr
							><th scope="col">Kind</th><th scope="col">Threat</th><th scope="col">Mitigation</th
							></tr
						></thead
					>
					<tbody>
						{#each p.threats as threat, i (i)}
							<tr>
								<td class="whitespace-nowrap text-xs font-medium">{humanize(threat.kind)}</td>
								<td class="text-sm">{threat.threat}</td>
								<td class="text-sm text-muted-foreground">{threat.mitigation}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Content>
	</Card.Root>

	<p class="text-[11px] text-muted-foreground">
		Platform git {p.platform.git.sha ? shortHash(p.platform.git.sha) : 'unknown'}{p.platform.git.ref
			? ` (${p.platform.git.ref})`
			: ''}
		· stats {p.platform.stats_version ?? '—'} · created {fmtDateTime(p.timestamps.created_at)} · preflight
		{fmtDateTime(p.timestamps.preflight_generated_at)} · locked {fmtDateTime(
			p.timestamps.locked_at
		)}
	</p>
</div>
