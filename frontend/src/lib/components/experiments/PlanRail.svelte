<script lang="ts">
	/**
	 * PlanRail — the composer's sticky plan (server preview, debounced): size
	 * of the design, where the cells run (one row per endpoint with slots,
	 * resident limit, swaps and an est-hours bar), total time and the
	 * bottleneck, the cost bound, issues, the comparability check, and the
	 * Save draft / Launch buttons. Before the design is complete it shows a
	 * checklist.
	 *
	 * Props: `checklist`, `ready`, `preview` (latest successful, maybe `stale`),
	 * `loading`, `error` + `onretry`, `localInvalid`, client-side tallies
	 * (`conditions`, `apps`, `repeats`), `issues` + `actions`, submit state
	 * (`canLaunch`, `canSave`, `blockedReason`, `submitting`, `submitError`,
	 * `onsave`, `onlaunch`, `saveLabel`), `experimentStack`.
	 */
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import * as Sheet from '$lib/components/ui/sheet';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SummaryChecklist from '$lib/components/generator/composer/SummaryChecklist.svelte';
	import SummaryIssues from '$lib/components/generator/composer/SummaryIssues.svelte';
	import type { ChecklistItem, IssueAction, SummaryIssue } from '$lib/components/generator/composer/issues';
	import { formatUsd } from '$lib/components/generator/batches/batch-utils';
	import type { ExperimentPreviewV2 } from '$lib/api/types/experiments';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Rocket from '@lucide/svelte/icons/rocket';
	import Save from '@lucide/svelte/icons/save';
	import ComparabilityTable from './ComparabilityTable.svelte';
	import EndpointPlanRow from './EndpointPlanRow.svelte';
	import { bottleneckLabel, formatHours, policyLabel } from './format';

	interface Props {
		checklist: ChecklistItem[];
		ready: boolean;
		preview: ExperimentPreviewV2 | null;
		stale: boolean;
		loading: boolean;
		error: string;
		onretry: () => void;
		localInvalid: boolean;
		conditions: number;
		apps: number;
		repeats: number;
		issues: SummaryIssue[];
		actions?: Record<string, IssueAction>;
		canLaunch: boolean;
		canSave: boolean;
		blockedReason: string;
		submitting: 'save' | 'launch' | null;
		submitError: string;
		saveLabel?: string;
		onsave: () => void;
		onlaunch: () => void;
		experimentStack?: string | null;
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
		conditions,
		apps,
		repeats,
		issues,
		actions = {},
		canLaunch,
		canSave,
		blockedReason,
		submitting,
		submitError,
		saveLabel = 'Save draft',
		onsave,
		onlaunch,
		experimentStack = null,
	}: Props = $props();

	let comparabilityOpen = $state(false);

	const cells = $derived(conditions * apps * repeats);
	const endpoints = $derived(
		[...(preview?.endpoints ?? [])].sort((a, b) => (b.est_hours ?? 0) - (a.est_hours ?? 0))
	);
	const maxHours = $derived(Math.max(0, ...endpoints.map((e) => e.est_hours ?? 0)));
	const bottleneck = $derived(bottleneckLabel(preview?.estimate.bottleneck));
	const bottleneckSlug = $derived(
		preview?.estimate.bottleneck?.startsWith('endpoint:') ? preview.estimate.bottleneck.slice('endpoint:'.length) : null
	);

	function costText(p: ExperimentPreviewV2): string {
		switch (p.cost.kind) {
			case 'local_zero':
				return 'No API cost: every model runs on your endpoints.';
			case 'mixed':
				return `≤ ${formatUsd(p.cost.openrouter_upper_bound_usd)} on OpenRouter; ${p.cost.local_cells} local cells cost nothing.`;
			case 'heuristic':
				return `≈ ${formatUsd(p.cost.openrouter_upper_bound_usd)} (estimate)`;
			default:
				return `≤ ${formatUsd(p.cost.openrouter_upper_bound_usd)} (budget cap × runs)`;
		}
	}
</script>

<Card.Root class="scroll-mt-20 gap-3" id="experiment-plan" aria-labelledby="experiment-plan-title">
	<Card.Header class="flex-row items-center justify-between gap-2">
		<Card.Title><h2 id="experiment-plan-title">Plan</h2></Card.Title>
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
		<div class="grid grid-cols-3 gap-2">
			<div class="rounded-md border border-border bg-surface-2 px-2.5 py-2">
				<p class="kpi-label">Conditions</p>
				<p class="font-display text-xl font-semibold tabular-nums">{conditions}</p>
			</div>
			<div class="rounded-md border border-border bg-surface-2 px-2.5 py-2">
				<p class="kpi-label">Cells</p>
				<p class="font-display text-xl font-semibold tabular-nums">{cells.toLocaleString()}</p>
			</div>
			<div class="rounded-md border border-border bg-surface-2 px-2.5 py-2">
				<p class="kpi-label">Apps × R</p>
				<p class="font-display text-xl font-semibold tabular-nums">{apps}×{repeats}</p>
			</div>
		</div>

		{#if !ready}
			<SummaryChecklist items={checklist} />
		{:else}
			{#if localInvalid}
				<p class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-fg" role="status">
					Some fields hold values that weren’t applied. Fix them to update the plan.
				</p>
			{/if}
			{#if error}
				<ErrorAlert title="Couldn’t plan this experiment" message={error} {onretry} />
			{/if}
			{#if !preview}
				{#if !error && !localInvalid}<LoadingState label="planning" class="py-8" />{/if}
			{:else}
				<div class={cn('space-y-4 transition-opacity duration-200', stale && 'opacity-60')} aria-busy={stale}>
					<div class="space-y-1">
						<div class="flex items-baseline justify-between gap-2">
							<p class="section-label">Where it runs</p>
							<p class="text-xs">
								<span class="text-muted-foreground">Total</span>
								<span class="font-mono font-semibold tabular-nums">{formatHours(preview.estimate.total_hours)}</span>
							</p>
						</div>
						{#if endpoints.length > 0}
							<ul class="divide-y divide-border/60">
								{#each endpoints as ep (ep.slug)}
									<EndpointPlanRow
										endpoint={ep}
										{maxHours}
										swaps={preview.schedule.estimated_swaps?.[ep.slug] ?? null}
										policy={preview.schedule.policy_effective_by_endpoint?.[ep.slug]
											? policyLabel(preview.schedule.policy_effective_by_endpoint[ep.slug])
											: ''}
										bottleneck={bottleneckSlug === ep.slug}
									/>
								{/each}
							</ul>
						{/if}
						{#if bottleneck}
							<p class="text-[11px] text-muted-foreground" title={bottleneck.title}>
								Bottleneck: <span class="font-medium text-foreground">{bottleneck.label}</span>
								{#if bottleneck.title}<span class="block">{bottleneck.title}</span>{/if}
							</p>
						{/if}
						<p class="text-[11px] text-muted-foreground">
							Up to {preview.capacity.est_parallel_runs} runs in parallel · sandboxes {preview.capacity.sandbox_slots} · LLM job
							threads {preview.capacity.llm_job_threads}
						</p>
					</div>

					<div class="space-y-0.5 text-xs">
						<p class="section-label">Cost</p>
						<p>{costText(preview)}</p>
					</div>

					<p class="text-[11px] text-muted-foreground">
						Paired tests use apps as the unit: n = {apps} app{apps === 1 ? '' : 's'}{apps < 6
							? ' (fewer than 6: p-values will be flagged; add apps rather than repeats for power)'
							: ''}.
					</p>

					<SummaryIssues {issues} {actions} />

					{#if preview.comparability}
						<Button variant="outline" size="sm" class="w-full" onclick={() => (comparabilityOpen = true)}>
							<GitCompareArrows />View comparability
							{#if preview.comparability.differs_on.length}
								<span class="text-[11px] text-warning-fg">· differs on {preview.comparability.differs_on.length}</span>
							{/if}
						</Button>
					{/if}
				</div>
			{/if}
		{/if}

		{#if submitError}
			<ErrorAlert message={submitError} />
		{/if}
		<div class="hidden space-y-2 border-t border-border pt-3 @4xl/composer:block">
			{#if blockedReason && !canLaunch}
				<p class="text-[11px] text-muted-foreground">{blockedReason}</p>
			{/if}
			<div class="flex gap-2">
				<Button variant="outline" size="sm" class="flex-1" disabled={!canSave || submitting !== null} onclick={onsave}>
					{#if submitting === 'save'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Save />{/if}
					{saveLabel}
				</Button>
				<Button size="sm" class="flex-1" disabled={!canLaunch || submitting !== null} onclick={onlaunch}>
					{#if submitting === 'launch'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Rocket />{/if}
					Launch
				</Button>
			</div>
		</div>
	</Card.Content>
</Card.Root>

{#if preview?.comparability}
	<Sheet.Root bind:open={comparabilityOpen}>
		<Sheet.Content side="right" class="w-full max-w-none overflow-y-auto p-4 sm:max-w-4xl">
			<Sheet.Header class="p-0">
				<Sheet.Title>Comparability</Sheet.Title>
				<Sheet.Description>What each condition will actually run with; highlighted columns differ between conditions.</Sheet.Description>
			</Sheet.Header>
			<div class="mt-4">
				<ComparabilityTable comparability={preview.comparability} {experimentStack} />
			</div>
		</Sheet.Content>
	</Sheet.Root>
{/if}
