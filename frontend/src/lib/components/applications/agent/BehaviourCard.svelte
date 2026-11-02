<script lang="ts">
	// What the agent did during one run, from its trajectory record
	// (result_data.agent.trajectory, built from the gateway call log).
	// Descriptive only; categories are heuristics from a versioned rule table.
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Check from '@lucide/svelte/icons/check';
	import Minus from '@lucide/svelte/icons/minus';
	import type { RuleTable, TrajectoryRecord } from '$lib/api/trajectory';
	import ContextChart from './ContextChart.svelte';
	import {
		categoryLabel,
		fmtBool,
		fmtInt,
		fmtPct,
		fmtSeconds,
		fmtTokensShort,
		isNum,
		outcomeLabel,
		sortedCounts,
	} from './format';

	interface Props {
		record: TrajectoryRecord;
		rules?: RuleTable | null;
		recordSource?: 'stored' | 'computed' | null;
	}

	let { record, rules = null, recordSource = 'stored' }: Props = $props();

	let showRules = $state(false);

	const verification = $derived(record.verification ?? null);
	const time = $derived(record.time ?? null);
	const context = $derived(record.context ?? null);
	const categories = $derived(sortedCounts(record.action_categories));
	const maxCategory = $derived(categories.length ? categories[0][1] : 0);
	const failureKinds = $derived(sortedCounts(record.failure_kinds));
	const failedShare = $derived(
		isNum(record.failed_actions) && isNum(record.actions_total) && record.actions_total > 0
			? record.failed_actions / record.actions_total
			: null,
	);
	const llm = $derived(time?.llm_s ?? null);
	const between = $derived(time?.between_calls_s ?? null);
	const timeTotal = $derived(isNum(llm) && isNum(between) ? llm + between : null);
	const checks = $derived([
		{ key: 'built', label: 'Built', value: verification?.built ?? null },
		{ key: 'ran_server', label: 'Ran the server', value: verification?.ran_server ?? null },
		{ key: 'probed_http', label: 'Probed over HTTP', value: verification?.probed_http ?? null },
		{ key: 'ran_tests', label: 'Ran tests', value: verification?.ran_tests ?? null },
		{ key: 'syntax_checked', label: 'Syntax / lint check', value: verification?.syntax_checked ?? null },
	]);
	const firstPass = $derived(record.first_pass ?? null);
</script>

<Card.Root>
	<Card.Header class="pb-2">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<Card.Title class="text-sm">Behaviour</Card.Title>
			<div class="flex items-center gap-1.5">
				{#if recordSource === 'computed'}
					<Badge variant="secondary" title="Computed on the fly from the run record; not stored on the job yet">not stored yet</Badge>
				{/if}
				<Badge variant="outline" class="font-mono" title="Classification rule table version">rules v{record.parser_version}</Badge>
			</div>
		</div>
		<Card.Description class="text-xs">
			What the agent did, rebuilt from the gateway's call log ({record.source?.n_calls ?? '—'} calls{record.source?.harness_events ? ' + OpenCode tool events' : ''}).
			Action categories are heuristics: compare them within a harness.
		</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4 text-sm">
		{#if !record.comparable}
			<p class="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
				Action metrics do not apply to this run{record.comparable_reason ? `: ${record.comparable_reason}` : ''}. Time, context and token
				fields below are still measured.
			</p>
		{/if}

		<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
			{#if record.comparable}
			<div class="kpi-card" title="Tool calls the model issued, counted from the gateway log (independent of the harness's own report); mini-swe-agent's submit command is not counted.">
				<span class="kpi-label">Actions</span>
				<span class="kpi-value">{fmtInt(record.actions_total)}</span>
				<span class="text-[11px] text-muted-foreground">tool calls in the gateway log: {fmtInt(record.tool_calls_gateway)}</span>
			</div>
			<div class="kpi-card" style="--kpi-accent: var(--chart-2)" title="Non-zero exits, kills/timeouts, edits that did not apply and piped builds/tests whose output shows a failure. grep/diff exit 1 counts as 'no match', not a failure. Tool calls the harness's own permission rules refused are counted apart (denied).">
				<span class="kpi-label">Failed actions</span>
				<span class="kpi-value">{fmtInt(record.failed_actions)}</span>
				<span class="text-[11px] text-muted-foreground">
					{fmtPct(failedShare)} of actions · killed {fmtInt(record.killed_actions)} · plus {fmtInt(record.policy_denials)} denied by harness policy
				</span>
			</div>
			<div class="kpi-card" style="--kpi-accent: var(--chart-3)" title="Build commands (npm/vite build, tsc, ...). Success is read from the output because piped commands report the pipe's exit code.">
				<span class="kpi-label">Builds</span>
				<span class="kpi-value">{fmtInt(verification?.n_builds)}</span>
				<span class="text-[11px] text-muted-foreground">
					{#if isNum(verification?.first_build_action)}
						first at action {verification?.first_build_action} of {fmtInt(record.actions_total)} · {verification?.builds_failed ?? 0} failed
					{:else}
						no build command
					{/if}
				</span>
			</div>
			<div class="kpi-card" style="--kpi-accent: var(--chart-5)" title="Distinct workspace files written or edited by tool calls (write/edit tools, heredocs, redirects, sed -i, Python writes).">
				<span class="kpi-label">Files written</span>
				<span class="kpi-value">{fmtInt(record.files_written)}</span>
				<span class="text-[11px] text-muted-foreground">
					max {fmtInt(record.max_writes_per_file)} writes / file · {fmtInt(record.writes_after_first_build)} after first build
				</span>
			</div>
			{/if}
			<div class="kpi-card" title="Prompt tokens of the first and the largest main-role call: how far the conversation grew.">
				<span class="kpi-label">Context</span>
				<span class="kpi-value">{fmtTokensShort(context?.prompt_tokens_first)} → {fmtTokensShort(context?.prompt_tokens_max)}</span>
				<span class="text-[11px] text-muted-foreground">first → peak prompt tokens</span>
			</div>
			<div class="kpi-card" style="--kpi-accent: var(--chart-2)" title="Cached prompt tokens / prompt tokens over successful calls (as the provider reported them).">
				<span class="kpi-label">Cache hits</span>
				<span class="kpi-value">{fmtPct(record.cache_hit_ratio)}</span>
				<span class="text-[11px] text-muted-foreground">of prompt tokens</span>
			</div>
			<div class="kpi-card" style="--kpi-accent: var(--chart-3)" title="Reasoning tokens / completion tokens, as reported by the provider.">
				<span class="kpi-label">Reasoning share</span>
				<span class="kpi-value">{fmtPct(record.reasoning_share)}</span>
				<span class="text-[11px] text-muted-foreground">of completion tokens</span>
			</div>
			<div class="kpi-card" style="--kpi-accent: var(--chart-5)" title="Time inside LLM calls (gateway-measured; a retried call includes its retry backoff) vs time between calls (tool execution and harness overhead).">
				<span class="kpi-label">LLM time</span>
				<span class="kpi-value">{fmtPct(time?.llm_share)}</span>
				<span class="text-[11px] text-muted-foreground">{fmtSeconds(llm)} LLM · {fmtSeconds(between)} between calls</span>
			</div>
		</div>

		{#if record.comparable}
		<div class="space-y-1.5">
			<p class="section-label">Self-verification</p>
			<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
				{#each checks as c (c.key)}
					<li class="inline-flex items-center gap-1.5" title={fmtBool(c.value)}>
						{#if c.value === true}
							<Check class="h-3.5 w-3.5 text-foreground" aria-hidden="true" />
						{:else}
							<Minus class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
						{/if}
						<span class={c.value === true ? '' : 'text-muted-foreground'}>{c.label}</span>
						<span class="sr-only">: {fmtBool(c.value)}</span>
					</li>
				{/each}
			</ul>
		</div>

		{/if}

		<div class="grid gap-4 lg:grid-cols-2">
			{#if record.comparable}
			<div class="space-y-1.5">
				<p class="section-label">Action categories</p>
				{#if categories.length}
					<ul class="space-y-1">
						{#each categories as [cat, count] (cat)}
							<li class="grid grid-cols-[8.5rem_1fr_2.5rem] items-center gap-2 text-xs" title={rules?.categories?.[cat] ?? ''}>
								<span class="truncate">{categoryLabel(cat)}</span>
								<span class="h-2 rounded-sm bg-muted" aria-hidden="true">
									<span class="block h-2 rounded-sm" style="width: {maxCategory ? (count / maxCategory) * 100 : 0}%; background: var(--chart-1)"></span>
								</span>
								<span class="text-right font-mono tabular-nums">{count}</span>
							</li>
						{/each}
					</ul>
					<p class="text-[11px] text-muted-foreground">One primary category per action (what it was for); hover a row for the rule.</p>
				{:else}
					<p class="text-xs text-muted-foreground">No tool actions recorded.</p>
				{/if}
			</div>
			{/if}
			<div class="space-y-1.5">
				<p class="section-label">Context growth</p>
				{#if context?.series?.length}
					<ContextChart series={context.series} />
				{:else}
					<p class="text-xs text-muted-foreground">No per-call prompt tokens recorded.</p>
				{/if}
			</div>
		</div>

		{#if isNum(timeTotal) && timeTotal > 0}
			<div class="space-y-1.5">
				<p class="section-label">Where the time went</p>
				<div class="flex h-3 overflow-hidden rounded-sm bg-muted" role="img" aria-label="LLM {fmtSeconds(llm)}, between calls {fmtSeconds(between)}">
					<span class="h-full" style="width: {((llm ?? 0) / timeTotal) * 100}%; background: var(--chart-1)"></span>
					<span class="h-full" style="width: {((between ?? 0) / timeTotal) * 100}%; background: var(--chart-3)"></span>
				</div>
				<div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
					<span class="inline-flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm" style="background: var(--chart-1)"></span>LLM calls {fmtSeconds(llm)}</span>
					<span class="inline-flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm" style="background: var(--chart-3)"></span>between calls {fmtSeconds(between)}</span>
					{#if isNum(time?.tool_exec_s)}<span>tool execution (OpenCode events) {fmtSeconds(time?.tool_exec_s)}</span>{/if}
					{#if isNum(time?.tail_s)}<span title="Approximate: run start (gateway start) + wall time − end of the last call">after the last call ≈ {fmtSeconds(time?.tail_s)}</span>{/if}
				</div>
			</div>
		{/if}

		{#if failureKinds.length || record.outcome_unknown || record.format_errors}
			<div class="space-y-1.5">
				<p class="section-label">Failures by kind</p>
				<div class="flex flex-wrap gap-1">
					{#each failureKinds as [kind, n] (kind)}
						<Badge variant="secondary" class="font-mono">{outcomeLabel(kind)} · {n}</Badge>
					{/each}
					{#if record.format_errors}
						<Badge variant="secondary" class="font-mono" title="Responses the harness rejected for not using its tool/edit format">format errors · {record.format_errors}</Badge>
					{/if}
				</div>
				{#if record.outcome_unknown}
					<p class="text-[11px] text-muted-foreground">{record.outcome_unknown} actions have no recorded exit status (outcome unknown, not counted as failed).</p>
				{/if}
			</div>
		{/if}

		{#if firstPass && firstPass.files_with_first_version > 0}
			<div class="space-y-1">
				<p class="section-label">First draft vs final</p>
				<p class="text-xs">
					{firstPass.files_with_first_version} of {firstPass.final_files} final files have a first whole-file write
					({fmtPct(firstPass.coverage)}); {fmtInt(firstPass.unchanged)} ended unchanged, mean line similarity
					{fmtPct(firstPass.mean_similarity)}.
				</p>
				<p class="text-[11px] text-muted-foreground">{firstPass.method}. Files only edited in place have no known first version.</p>
			</div>
		{/if}

		{#if record.most_rewritten?.length}
			<div class="space-y-1">
				<p class="section-label">Most rewritten files</p>
				<div class="flex flex-wrap gap-1">
					{#each record.most_rewritten as f (f.path)}
						<Badge variant="outline" class="font-mono">{f.path} · {f.writes}×</Badge>
					{/each}
				</div>
			</div>
		{/if}

		{#if rules}
			<div class="space-y-1">
				<button
					type="button"
					class="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
					aria-expanded={showRules}
					onclick={() => (showRules = !showRules)}
				>
					<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showRules ? 'rotate-90' : ''}" />
					Classification rules (v{rules.parser_version})
				</button>
				{#if showRules}
					<div class="space-y-2 rounded-md border p-3 text-xs">
						<p class="text-muted-foreground">Priority (first match is the primary category): {rules.priority.map(categoryLabel).join(' › ')}</p>
						<dl class="grid gap-x-3 gap-y-1 sm:grid-cols-[10rem_1fr]">
							{#each Object.entries(rules.categories) as [cat, text] (cat)}
								<dt class="font-medium">{categoryLabel(cat)}</dt>
								<dd class="text-muted-foreground">{text}</dd>
							{/each}
						</dl>
						<ul class="list-disc space-y-0.5 pl-4 text-muted-foreground">
							{#each rules.notes as n (n)}<li>{n}</li>{/each}
						</ul>
					</div>
				{/if}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
