<script lang="ts">
	// Evidence for one agent-mode run: how it ended, what it cost, what the
	// gateway saw, and the frozen configuration it ran under. Shared by the
	// sample generator's result panel and the application detail page.
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { evaluateGenerationJob, runRecordUrl, type GenerationJob } from '$lib/api/client';
	import { Button } from '$lib/components/ui/button';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import { toneClasses } from '$lib/constants/colors';
	import Download from '@lucide/svelte/icons/download';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';

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
	const recordFiles = $derived(
		Object.entries((agent?.run_record ?? {}) as Record<string, any>).filter(([name]) => !name.startsWith('_')),
	);

	let showBrief = $state(false);
	let showFinal = $state(false);
	let showChecks = $state(false);
	let evaluating = $state(false);
	let evaluateMessage = $state('');

	const evaluation = $derived((job.result_data?.evaluation ?? null) as Record<string, any> | null);
	const checks = $derived(((evaluation?.checks ?? []) as { category: string; name: string; passed: boolean; detail: string }[]));
	const failedChecks = $derived(checks.filter((c) => !c.passed));
	const validity = $derived((agent?.validity ?? null) as { valid: boolean; notes: { code: string; severity: string; detail: string }[] } | null);
	const visibleNotes = $derived((validity?.notes ?? []).filter((n) => n.severity !== 'info' || !compact));

	async function reevaluate() {
		evaluating = true;
		evaluateMessage = '';
		try {
			await evaluateGenerationJob(job.id);
			evaluateMessage = 'Evaluation queued — reload the page in a few minutes.';
		} catch (err: any) {
			evaluateMessage = err?.detail ?? err?.message ?? 'Could not queue the evaluation';
		} finally {
			evaluating = false;
		}
	}

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
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Tokens in / out</dt>
					<dd class="font-mono tabular-nums">
						{(metrics.prompt_tokens ?? 0).toLocaleString()} / {(metrics.completion_tokens ?? 0).toLocaleString()}
					</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Tool calls</dt>
					<dd class="font-mono tabular-nums">{metrics.tool_calls ?? 0}</dd>
				</div>
				<div>
					<dt class="text-[11px] text-muted-foreground">Steps</dt>
					<dd class="font-mono tabular-nums">{metrics.harness_steps ?? 0}</dd>
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

			{#if agent.budget_exhausted}
				<p class="text-[11px] text-warning-fg">Stopped by budget: {agent.budget_exhausted.replace(/_/g, ' ')}.</p>
			{/if}

			{#if evaluation}
				<div class="space-y-1.5 rounded-md border p-3">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<p class="section-label">Platform evaluation</p>
						<div class="flex items-center gap-1.5">
							<StatusBadge status={evaluation.status} />
							{#if evaluation.summary?.total}
								<span class="font-mono text-xs tabular-nums">
									{evaluation.summary.passed}/{evaluation.summary.total} checks · {Math.round((evaluation.summary.pass_rate ?? 0) * 100)}%
								</span>
							{/if}
						</div>
					</div>
					{#if evaluation.summary?.by_category}
						<div class="flex flex-wrap gap-1">
							{#each Object.entries(evaluation.summary.by_category as Record<string, { passed: number; total: number }>) as [cat, v] (cat)}
								<Badge variant="secondary" class="font-mono">{cat} {v.passed}/{v.total}</Badge>
							{/each}
						</div>
					{/if}
					{#if evaluation.error}
						<p class="text-[11px] text-danger-fg">{evaluation.error}</p>
					{/if}
					{#if evaluation.build && !evaluation.build.ok}
						<pre class="max-h-40 overflow-auto rounded border bg-muted/20 p-2 text-[11px] font-mono">{evaluation.build.log_tail}</pre>
					{/if}
					{#if failedChecks.length}
						<button
							type="button"
							class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
							aria-expanded={showChecks}
							onclick={() => (showChecks = !showChecks)}
						>
							<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showChecks ? 'rotate-90' : ''}" />
							{failedChecks.length} failed {failedChecks.length === 1 ? 'check' : 'checks'}
						</button>
						{#if showChecks}
							<ul class="space-y-1 text-xs">
								{#each failedChecks as c (c.category + c.name)}
									<li class="rounded border px-2 py-1">
										<span class="font-mono">{c.name}</span>
										{#if c.detail}<span class="block text-muted-foreground">{c.detail}</span>{/if}
									</li>
								{/each}
							</ul>
						{/if}
					{/if}
					<p class="text-[11px] text-muted-foreground">
						Built with the stack's own Dockerfile, run without network access, graded by conformance suite
						v{evaluation.tester_version} (contract v{evaluation.contract_version}){evaluation.contract_stated_in_prompt === false ? ' — this sample\'s prompt did not state that contract, so compare its score with care' : ''}.
					</p>
					{#if !compact}
						<div class="flex items-center gap-2">
							<Button variant="outline" size="sm" onclick={reevaluate} disabled={evaluating}>
								<RefreshCw class="h-3.5 w-3.5" /> Re-evaluate
							</Button>
							{#if evaluateMessage}<span class="text-[11px] text-muted-foreground">{evaluateMessage}</span>{/if}
						</div>
					{/if}
				</div>
			{:else if !compact}
				<div class="flex items-center gap-2">
					<Button variant="outline" size="sm" onclick={reevaluate} disabled={evaluating}>
						<RefreshCw class="h-3.5 w-3.5" /> Evaluate
					</Button>
					{#if evaluateMessage}<span class="text-[11px] text-muted-foreground">{evaluateMessage}</span>{/if}
				</div>
			{/if}

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
						<div class="flex justify-between gap-2 sm:col-span-2"><dt class="text-muted-foreground">Harness prompt</dt><dd class="truncate">{snapshot.harness?.system_prompt_source ?? '—'}</dd></div>
						<div class="flex justify-between gap-2 sm:col-span-2"><dt class="text-muted-foreground">Images</dt><dd class="font-mono truncate">{snapshot.images?.harness ?? '—'} on {snapshot.images?.base ?? '—'}</dd></div>
						<div class="flex justify-between gap-2 sm:col-span-2"><dt class="text-muted-foreground">Routing</dt><dd class="font-mono truncate">{JSON.stringify(snapshot.llm?.provider ?? {})}</dd></div>
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
{/if}
