<script lang="ts">
	// Evidence for one agent-mode run: how it ended, what it cost, what the
	// gateway saw, and the frozen configuration it ran under. Shared by the
	// sample generator's result panel and the application detail page.
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { runRecordUrl, type GenerationJob } from '$lib/api/client';
	import { toneClasses } from '$lib/constants/colors';
	import Download from '@lucide/svelte/icons/download';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import AgentBehaviour from './agent/AgentBehaviour.svelte';
	import { auxiliaryCallsNote, effectiveSteps, effectiveToolCalls } from './agent/format';

	interface Props {
		job: GenerationJob;
		/** Compact = sidebar panel: key numbers only, no config/brief sections. */
		compact?: boolean;
	}

	let { job, compact = false }: Props = $props();

	const agent = $derived((job.result_data?.agent ?? null) as Record<string, any> | null);
	const snapshot = $derived((job.resolved_bundle ?? {}) as Record<string, any>);
	const metrics = $derived((job.metrics ?? {}) as Record<string, any>);
	const budget = $derived((agent?.budget ?? snapshot.agent ?? {}) as Record<string, any>);
	const workspace = $derived((agent?.workspace ?? {}) as Record<string, any>);
	const providers = $derived(Object.entries((agent?.providers ?? {}) as Record<string, number>).sort((a, b) => b[1] - a[1]));
	// Gateway-derived counts (trajectory record / metrics.behaviour) next to the harness's self-report.
	const trajectory = $derived((agent?.trajectory ?? null) as Record<string, any> | null);
	const behaviour = $derived((metrics.behaviour ?? null) as Record<string, any> | null);
	const toolCalls = $derived(
		effectiveToolCalls(metrics.tool_calls, behaviour?.tool_calls_gateway ?? trajectory?.tool_calls_gateway),
	);
	const steps = $derived(effectiveSteps(metrics.harness_steps, trajectory?.model_turns));
	const auxNote = $derived(auxiliaryCallsNote(trajectory?.call_roles, agent?.llm_calls));
	const listCost = $derived(typeof metrics.list_cost === 'number' ? (metrics.list_cost as number) : null);
	const harnessPrompt = $derived(String(snapshot.harness?.system_prompt_source ?? '—'));
	const images = $derived(`${snapshot.images?.harness ?? '—'} on ${snapshot.images?.base ?? '—'}`);
	const routing = $derived(JSON.stringify(snapshot.llm?.provider ?? {}));
	const recordFiles = $derived(
		Object.entries((agent?.run_record ?? {}) as Record<string, any>).filter(([name]) => !name.startsWith('_')),
	);

	let showBrief = $state(false);
	let showFinal = $state(false);

	const evaluation = $derived((job.result_data?.evaluation ?? null) as Record<string, any> | null);
	const validity = $derived((agent?.validity ?? null) as { valid: boolean; notes: { code: string; severity: string; detail: string }[] } | null);
	const visibleNotes = $derived((validity?.notes ?? []).filter((n) => n.severity !== 'info' || !compact));

	const severityTone: Record<string, 'danger' | 'warning' | 'neutral'> = { exclude: 'danger', warn: 'warning', info: 'neutral' };

	function pct(used: number | undefined, max: number | undefined): string {
		if (!used || !max) return '';
		return ` (${Math.round((used / max) * 100)}%)`;
	}

	function fmtCost(v: number | undefined): string {
		return v == null ? '—' : `$${v.toFixed(v < 0.01 ? 5 : 3)}`;
	}

	function fmtBytes(n: number): string {
		if (n < 1024) return `${n} B`;
		if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
		return `${(n / 1024 / 1024).toFixed(1)} MB`;
	}

</script>

{#if agent}
	<div class="space-y-4">
	<Card.Root>
		<Card.Header class="pb-2">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<Card.Title class="text-sm">Agent run</Card.Title>
				<div class="flex items-center gap-1.5">
					<Badge variant="outline" class="font-mono">{agent.harness}@{agent.harness_version}</Badge>
					<StatusBadge status={agent.termination} />
				</div>
			</div>
		</Card.Header>
		<Card.Content class="space-y-4 text-sm">
			<dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 {compact ? '' : 'sm:grid-cols-4'}">
				<div>
					<dt class="text-[11px] text-muted-foreground">Wall time</dt>
					<dd class="font-mono tabular-nums">{Math.round(agent.wall_seconds ?? 0)}s{pct(agent.wall_seconds, budget.wall_clock_seconds)}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">LLM calls</dt>
					<dd class="font-mono tabular-nums">
						{agent.llm_calls}{pct(agent.llm_calls, budget.max_llm_requests)}
						{#if agent.llm_calls_failed}<span class="text-danger-fg"> · {agent.llm_calls_failed} failed</span>{/if}
					</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Cost</dt>
					<dd class="font-mono tabular-nums">{fmtCost(metrics.cost)}{pct(metrics.cost, budget.max_cost_usd)}</dd>
					{#if listCost != null}
						<dd class="text-[11px] text-muted-foreground" title="Sum of the provider's per-call list price; the billed cost above is what this subscription run actually cost">
							list-price equivalent {fmtCost(listCost)}{metrics.billing_source ? ` · billed via ${metrics.billing_source}` : ''} (not billed)
						</dd>
					{/if}
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Tokens in / out</dt>
					<dd class="font-mono tabular-nums">
						{(metrics.prompt_tokens ?? 0).toLocaleString()} / {(metrics.completion_tokens ?? 0).toLocaleString()}
					</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Tool calls</dt>
					<dd class="font-mono tabular-nums" title={toolCalls.source === 'gateway' ? 'counted from the gateway call log' : 'reported by the harness'}>
						{toolCalls.value ?? '—'}{#if toolCalls.source === 'gateway'}<span class="text-[11px] text-muted-foreground"> (gateway)</span>{/if}
					</dd>
					{#if toolCalls.note}<dd class="text-[11px] text-muted-foreground">{toolCalls.note}</dd>{/if}
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Steps</dt>
					<dd class="font-mono tabular-nums" title={steps.source === 'gateway' ? 'model turns counted from the gateway call log' : 'reported by the harness'}>
						{steps.value ?? '—'}{#if steps.source === 'gateway'}<span class="text-[11px] text-muted-foreground"> (gateway)</span>{/if}
					</dd>
					{#if steps.note && steps.note !== toolCalls.note}<dd class="text-[11px] text-muted-foreground">{steps.note}</dd>{/if}
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Files (added / modified)</dt>
					<dd class="font-mono tabular-nums">{workspace.origins?.added ?? 0} / {workspace.origins?.modified ?? 0}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Static checks</dt>
					<dd>
						<StatusBadge
							status={job.result_data?.validation?.passed ? 'passed' : 'failed'}
							label={job.result_data?.validation?.passed ? 'pass' : 'fail'}
						/>
					</dd>
				</div>
			</dl>

			{#if auxNote}
				<p class="text-[11px] text-muted-foreground">{auxNote}.</p>
			{/if}

			{#if agent.budget_exhausted}
				<p class="text-[11px] text-warning-fg">Stopped by budget: {agent.budget_exhausted.replace(/_/g, ' ')}.</p>
			{/if}

			<!-- The platform evaluation (build + conformance suite, re-evaluate) is the Overview tab's EvaluationCard. -->
			<p class="text-[11px] text-muted-foreground">
				Platform evaluation:
				{#if evaluation}
					<StatusBadge status={evaluation.status} />
					{#if evaluation.summary?.total}<span class="font-mono tabular-nums">{evaluation.summary.passed}/{evaluation.summary.total} checks</span>{/if}
				{:else}
					not evaluated yet
				{/if}
				· details and re-evaluation on the Overview tab.
			</p>

			{#if visibleNotes.length}
				<div class="space-y-1">
					<div class="flex items-center gap-1.5">
						<ShieldAlert class="h-3.5 w-3.5 text-muted-foreground" />
						<p class="section-label">Validity notes</p>
						{#if validity && !validity.valid}<StatusBadge status="excluded" tone="danger" label="exclude from analyses" />{/if}
					</div>
					<ul class="space-y-1 text-xs">
						{#each visibleNotes as note (note.code)}
							<li class="flex items-start gap-2">
								<StatusBadge status={note.severity} tone={severityTone[note.severity] ?? 'neutral'} />
								<span>{note.detail}</span>
							</li>
						{/each}
					</ul>
				</div>
			{/if}

			{#if providers.length}
				<div class="space-y-1">
					<p class="section-label">Served by</p>
					<div class="flex flex-wrap gap-1">
						{#each providers as [name, count]}
							<Badge variant="secondary" class="font-mono">{name} · {count}</Badge>
						{/each}
					</div>
				</div>
			{/if}

			{#if agent.harness_report?.final_message}
				<div class="space-y-1">
					<button
						type="button"
						class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
						aria-expanded={showFinal}
						onclick={() => (showFinal = !showFinal)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showFinal ? 'rotate-90' : ''}" />
						Agent's final message (self-reported)
					</button>
					{#if showFinal}
						<pre class="max-h-64 overflow-auto rounded border bg-muted/20 p-3 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{agent.harness_report.final_message}</pre>
					{/if}
				</div>
			{/if}

			{#if !compact}
				{#if agent.diffstat}
					<div class="space-y-1">
						<p class="section-label">Changes vs. scaffold</p>
						<pre class="max-h-48 overflow-auto rounded border bg-muted/20 p-3 text-[11px] font-mono leading-relaxed">{agent.diffstat}</pre>
					</div>
				{/if}

				<div class="space-y-1">
					<p class="section-label">Reproducibility</p>
					<dl class="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Prompt hash</dt><dd class="font-mono">{job.prompt_hash || '—'}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Run fingerprint</dt><dd class="font-mono">{snapshot.run_fingerprint ?? '—'}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Seed</dt><dd class="font-mono">{snapshot.gateway?.forced_params?.seed ?? 'not forwarded'}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Network</dt><dd class="font-mono">{agent.network_policy}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Sampling</dt><dd class="font-mono">{agent.sampling_policy ?? snapshot.gateway?.sampling_policy ?? '—'}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Run order</dt><dd class="font-mono">#{agent.schedule?.run_order ?? '—'}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Brief</dt><dd class="font-mono">v{snapshot.brief?.version} · {snapshot.brief?.sha256?.slice(0, 12)}</dd></div>
						<!-- min-w-0 on the row and value: long refs ellipsize at phone width instead of clipping; full value in the tooltip. -->
						<div class="flex min-w-0 justify-between gap-2 sm:col-span-2"><dt class="shrink-0 text-muted-foreground">Harness prompt</dt><dd class="min-w-0 truncate" title={harnessPrompt}>{harnessPrompt}</dd></div>
						<div class="flex min-w-0 justify-between gap-2 sm:col-span-2"><dt class="shrink-0 text-muted-foreground">Images</dt><dd class="min-w-0 truncate font-mono" title={images}>{images}</dd></div>
						<div class="flex min-w-0 justify-between gap-2 sm:col-span-2"><dt class="shrink-0 text-muted-foreground">Routing</dt><dd class="min-w-0 truncate font-mono" title={routing}>{routing}</dd></div>
						{#if snapshot.gateway?.dropped_params && Object.keys(snapshot.gateway.dropped_params).length}
							<div class="flex justify-between gap-2 sm:col-span-2"><dt class="text-muted-foreground">Not forwarded</dt><dd class="font-mono">{Object.keys(snapshot.gateway.dropped_params).join(', ')}</dd></div>
						{/if}
					</dl>
				</div>

				{#if snapshot.brief?.text}
					<div class="space-y-1">
						<button
							type="button"
							class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
							aria-expanded={showBrief}
							onclick={() => (showBrief = !showBrief)}
						>
							<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showBrief ? 'rotate-90' : ''}" />
							Task brief the agent received
						</button>
						{#if showBrief}
							<pre class="max-h-96 overflow-auto rounded border bg-muted/20 p-3 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{snapshot.brief.text}</pre>
						{/if}
					</div>
				{/if}

				{#if recordFiles.length}
					<div class="space-y-1">
						<p class="section-label">Run record</p>
						<div class="flex flex-wrap gap-1.5">
							{#each recordFiles as [name, entry]}
								<a
									href={runRecordUrl(job.id, name)}
									class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-mono text-muted-foreground hover:text-foreground hover:border-foreground/30"
									title="sha256 {entry.sha256}"
								>
									<Download class="h-3 w-3" />{name}<span class="opacity-70">{fmtBytes(entry.bytes)}</span>
								</a>
							{/each}
						</div>
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
	{#if !compact}
		<AgentBehaviour {job} />
	{/if}
	</div>
{/if}
