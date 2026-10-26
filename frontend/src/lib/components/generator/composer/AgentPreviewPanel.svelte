<script lang="ts">
	/**
	 * AgentPreviewPanel — the agent composer's Run summary. Before a task and a
	 * model are chosen it is a neutral checklist; afterwards it renders the
	 * server preview (`POST /jobs/agent/preview/`): tally, cost bound, resolved
	 * settings, per-model effects and issues, plus the Start button (two-column layout only;
	 * the StartBar carries it otherwise).
	 *
	 * Props: `checklist`, `ready`, `preview` (latest successful one, possibly
	 * `stale` for the current form), `loading`, `error` + `onretry`, `localInvalid`,
	 * `baseSeed`, `harnessSchemas` (harness → option schema, for chips),
	 * `issues` + `actions`, Start state (`canStart`, `blockedReason`, `submitting`,
	 * `submitError`, `startLabel`, `onstart`).
	 */
	import type { AgentPreview, HarnessOptionField } from '$lib/api/generation';
	import { splitPreviewLLMParams } from '$lib/api/generation';
	import { formatTokens } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { harnessOptionChips, summarizeBudget } from '../presets.svelte';
	import EffectiveParams from './EffectiveParams.svelte';
	import SummaryChecklist from './SummaryChecklist.svelte';
	import SummaryIssues from './SummaryIssues.svelte';
	import { type ChecklistItem, type IssueAction, type SummaryIssue } from './issues';
	import Check from '@lucide/svelte/icons/check';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Minus from '@lucide/svelte/icons/minus';
	import Play from '@lucide/svelte/icons/play';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		checklist: ChecklistItem[];
		ready: boolean;
		preview: AgentPreview | null;
		stale: boolean;
		loading: boolean;
		error: string;
		onretry: () => void;
		localInvalid: boolean;
		baseSeed: number | null;
		harnessSchemas: Record<string, HarnessOptionField[]>;
		issues: SummaryIssue[];
		actions?: Record<string, IssueAction>;
		canStart: boolean;
		blockedReason: string;
		submitting: boolean;
		submitError: string;
		startLabel: string;
		onstart: () => void;
	}

	let {
		checklist,
		ready,
		preview,
		stale,
		loading,
		error,
		onretry,
		localInvalid,
		baseSeed,
		harnessSchemas,
		issues,
		actions = {},
		canStart,
		blockedReason,
		submitting,
		submitError,
		startLabel,
		onstart,
	}: Props = $props();

	const llm = $derived(splitPreviewLLMParams(preview?.llm_params));
	const budget = $derived(
		preview
			? summarizeBudget({
					max_llm_requests: preview.agent_config.max_llm_requests,
					wall_clock_seconds: preview.agent_config.wall_clock_seconds,
					max_cost_usd: preview.agent_config.max_cost_usd,
					max_total_tokens: preview.agent_config.max_total_tokens,
					network_policy: preview.agent_config.network_policy,
				})
			: ''
	);
	const samplingOverridden = $derived(
		Object.values(llm.sources).includes('inline') || preview?.agent_config_sources?.sampling_policy === 'inline'
	);
	const budgetOverridden = $derived(
		['max_llm_requests', 'wall_clock_seconds', 'max_cost_usd', 'max_total_tokens', 'network_policy'].some(
			(key) => preview?.agent_config_sources?.[key] === 'inline'
		)
	);
	const policy = $derived(
		preview?.agent_config.sampling_policy === 'harness_native' ? 'harness-native' : 'provider defaults'
	);

	function usd(n: number | null | undefined): string {
		if (n == null || !Number.isFinite(n)) return '—';
		return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
	}

	function plural(n: number, word: string): string {
		return `${n} ${word}${n === 1 ? '' : 's'}`;
	}

	const chip = 'inline-flex items-center rounded-full border px-1.5 text-[11px] font-medium leading-4';
</script>

<Card.Root class="scroll-mt-20 gap-3" id="run-summary" aria-labelledby="run-summary-title">
	<Card.Header class="flex-row items-center justify-between gap-2">
		<Card.Title><h2 id="run-summary-title">Run summary</h2></Card.Title>
		{#if ready && loading}
			<span class="flex items-center gap-1.5 text-[11px] text-muted-foreground" role="status">
				<LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />Updating…
			</span>
		{:else if ready && preview && !stale && !error}
			<span class="flex items-center gap-1 text-[11px] text-success-fg">
				<CircleCheck class="size-3" aria-hidden="true" />Up to date
			</span>
		{/if}
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if !ready}
			<p class="text-xs text-muted-foreground">
				Choose what to run. Every run is resolved here — models, harnesses, settings and costs — before anything
				starts.
			</p>
			<SummaryChecklist items={checklist} />
		{:else}
			{#if localInvalid}
				<p class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-fg" role="status">
					Some fields hold values that weren’t applied. Fix them to update the summary.
				</p>
			{/if}
			{#if error}
				<ErrorAlert title="Couldn’t resolve these runs" message={error} {onretry} />
			{/if}
			{#if !preview}
				{#if !error && !localInvalid}<LoadingState label="resolving runs" class="py-10" />{/if}
			{:else}
				<div class={cn('space-y-4 transition-opacity duration-200', stale && 'opacity-60')} aria-busy={stale}>
					<div class="rounded-md border border-border bg-surface-2 p-3">
						<p class="flex items-baseline gap-2" aria-live="polite">
							<span class="font-mono text-2xl font-medium tabular-nums">{preview.run_count}</span>
							<span class="text-sm text-muted-foreground">run{preview.run_count === 1 ? '' : 's'}</span>
						</p>
						<p class="text-xs text-muted-foreground tabular-nums">
							{plural(preview.task_count, 'task')} × {plural(preview.model_count, 'model')} × {plural(
								preview.harness_count,
								'harness preset'
							)} × {plural(preview.trials, 'trial')}
						</p>
						<p class="mt-2 text-xs">
							Max cost <span class="font-medium tabular-nums">≤ {usd(preview.max_cost_usd_total)}</span>
							<span class="text-muted-foreground">({usd(preview.agent_config.max_cost_usd)} cap per run)</span>
						</p>
						<p class="mt-0.5 text-xs text-muted-foreground">
							{preview.graded
								? 'Graded by each app spec’s hidden conformance suite.'
								: 'Not graded: a free-form task has no conformance suite.'}
						</p>
					</div>

					<dl class="space-y-3 text-xs">
						<div class="space-y-1">
							<dt class="section-label">Stack &amp; seeds</dt>
							<dd>
								<span class="font-mono">{preview.stack_slug}</span>
								<span class="text-muted-foreground">
									· {baseSeed != null
										? `base seed ${baseSeed}: one seed per task × trial, shared by every model and harness`
										: 'random seed per run'}</span
								>
							</dd>
						</div>
						<div class="space-y-1">
							<dt class="section-label">Harness presets</dt>
							<dd>
								<ul class="space-y-1">
									{#each preview.harnesses as h, i (h.preset?.id ?? `${h.harness}-${i}`)}
										{@const chips = harnessOptionChips(
											Object.fromEntries(h.non_default_options.map((k) => [k, h.effective_options[k]])),
											harnessSchemas[h.harness] ?? []
										)}
										<li class="flex flex-wrap items-center gap-1.5">
											<span class="font-medium">{h.preset?.name ?? h.label}</span>
											{#if h.preset}<span class="font-mono text-muted-foreground">v{h.preset.version}</span>{/if}
											<span class="text-muted-foreground">{h.label} {h.version}</span>
											{#each chips as text (text)}
												<span class={cn(chip, 'border-info/30 bg-info/10 font-mono text-info-fg')}>{text}</span>
											{/each}
										</li>
									{/each}
								</ul>
							</dd>
						</div>
						<div class="space-y-1">
							<dt class="flex flex-wrap items-baseline gap-x-1.5">
								<span class="section-label">Sampling</span>
								{#if preview.presets?.sampling}
									<span class="text-[11px] text-muted-foreground">
										{preview.presets.sampling.name} v{preview.presets.sampling.version}{samplingOverridden ? ' + overrides' : ''}
									</span>
								{/if}
							</dt>
							<dd class="space-y-1">
								<EffectiveParams effective={llm.effective} sources={llm.sources} emptyText="Provider defaults: no sampling parameter is set." />
								<p class="text-muted-foreground">Policy: {policy}</p>
							</dd>
						</div>
						<div class="space-y-1">
							<dt class="flex flex-wrap items-baseline gap-x-1.5">
								<span class="section-label">Budget per run</span>
								{#if preview.presets?.budget}
									<span class="text-[11px] text-muted-foreground">
										{preview.presets.budget.name} v{preview.presets.budget.version}{budgetOverridden ? ' + overrides' : ''}
									</span>
								{/if}
							</dt>
							<dd>{budget || 'Platform defaults'}</dd>
						</div>
					</dl>

					{#if preview.models.length > 0}
						<div class="space-y-1.5">
							<p class="section-label">Per model</p>
							<div class="overflow-x-auto rounded-md border border-border">
								<table class="text-xs">
									<thead>
										<tr>
											<th scope="col">Model</th>
											<th scope="col">Tools</th>
											<th scope="col" class="num">Output cap</th>
											<th scope="col">Seed</th>
										</tr>
									</thead>
									<tbody>
										{#each preview.models as m (m.model_id)}
											{@const dropped = Object.entries(m.dropped_params)}
											<tr class="align-top">
												<td class="min-w-[9rem]">
													<span class="font-medium text-foreground">{m.name}</span>
													{#if dropped.length > 0 || m.context_window_source === 'fallback' || m.params_support === 'unknown'}
														<span class="mt-1 flex flex-wrap gap-1">
															{#each dropped as [key, reason] (key)}
																<span
																	class={cn(chip, 'border-warning/30 bg-warning/10 font-mono text-warning-fg')}
																	title={reason}>−{key}</span
																>
															{/each}
															{#if m.params_support === 'unknown'}
																<span
																	class={cn(chip, 'border-warning/30 bg-warning/10 text-warning-fg')}
																	title="The catalog lists no supported parameters for this model: its params are forwarded unverified"
																	>support unknown</span
																>
															{/if}
															{#if m.context_window_source === 'fallback'}
																<span class={cn(chip, 'border-border text-muted-foreground')} title="Context window not in the catalog; a fallback is assumed">ctx fallback</span>
															{/if}
														</span>
													{/if}
												</td>
												<td>
													{#if !m.tool_harness_ok}
														<span class="inline-flex items-center gap-0.5 font-medium text-danger-fg"
															><X class="size-3.5" aria-hidden="true" />missing</span
														>
													{:else if m.supports_tools}
														<Check class="size-3.5 text-success-fg" aria-label="supported" />
													{:else}
														<Minus class="size-3.5 text-muted-foreground" aria-label="not needed" />
													{/if}
												</td>
												<td class="num">
													{#if m.output_cap != null}{formatTokens(m.output_cap)}{:else}<span class="text-muted-foreground">—</span>{/if}
													{#if m.context_window != null}
														<span class="cell-sub block text-[11px] text-muted-foreground">{formatTokens(m.context_window)} ctx</span>
													{/if}
												</td>
												<td>
													{#if baseSeed == null}
														<span class="text-muted-foreground">random</span>
													{:else if m.seed_forwarded}
														<span class="text-foreground">sent</span>
													{:else}
														<span class="text-muted-foreground" title="The model or its providers don't accept a seed">not sent</span>
													{/if}
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
							{#if preview.models.some((m) => Object.keys(m.dropped_params).length > 0)}
							<p class="text-[11px] text-muted-foreground">
								−key: parameter the model doesn’t accept, dropped from its runs (hover for why).
							</p>
							{/if}
						</div>
					{/if}

					<SummaryIssues {issues} {actions} />
				</div>
			{/if}
		{/if}

		{#if submitError}
			<ErrorAlert title="Couldn’t start the batch" message={submitError} />
		{/if}

		<div class="hidden space-y-1.5 border-t border-border/60 pt-3 @4xl/composer:block">
			<Button class="w-full" disabled={!canStart || submitting} aria-busy={submitting} onclick={onstart}>
				{#if submitting}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}
				{startLabel}
			</Button>
			{#if !canStart && blockedReason}
				<p class="text-center text-[11px] text-muted-foreground">{blockedReason}</p>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
