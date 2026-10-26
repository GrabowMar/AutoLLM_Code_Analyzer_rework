<script lang="ts">
	/**
	 * StudyMasthead — the experiment page's header, set like the first page of
	 * a paper: accession id and method as the eyebrow, the title in the display
	 * serif, status and dates, the hypothesis as a pull quote, a spec strip
	 * (design formula · baseline · outcome · randomisation), then the page's
	 * progress strip and section tabs. Owns the page `<h1>` and `<title>` (it
	 * stands in for PageHeader on this page).
	 *
	 * Props:
	 * - `experiment`: the detail; `status`: live status (null for drafts).
	 * - `policyEffective`: the launched run order per endpoint.
	 * - `actions`, `progress`, `nav` snippets.
	 */
	import type { Snippet } from 'svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import type { ExperimentDetail, ExperimentStatusV2Payload } from '$lib/api/types/experiments';
	import { timeAgo } from '$lib/utils/dashboard';
	import { MODE_LABELS, conditionName, effectivePolicyLabel, experimentTones, isTerminal, plural, policyLabel } from '../format';
	import { designShape, shortExperimentId } from './findings';

	interface Props {
		experiment: ExperimentDetail;
		status?: ExperimentStatusV2Payload | null;
		policyEffective?: Record<string, string> | string | null;
		actions?: Snippet;
		progress?: Snippet;
		nav?: Snippet;
	}

	let { experiment, status = null, policyEffective = null, actions, progress, nav }: Props = $props();

	$effect(() => {
		const title = experiment.name;
		pageTitle.current = title;
		return () => {
			if (pageTitle.current === title) pageTitle.current = '';
		};
	});

	const shape = $derived(designShape(experiment));
	const lifecycle = $derived(status?.status ?? experiment.status);
	const baseline = $derived(experiment.conditions.find((c) => c.id === experiment.baseline_condition_id) ?? null);
	const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

	const outcome = $derived(
		experiment.mode === 'agent'
			? { value: 'Pass rate', detail: 'hidden conformance suite, app-level mean' }
			: experiment.post_run?.evaluate
				? { value: 'Pass rate', detail: 'evaluation checks, app-level mean' }
				: { value: 'Pass rate', detail: 'needs Evaluate (Operations → Automation)' }
	);
	const order = $derived(
		experiment.status === 'draft'
			? policyLabel(experiment.scheduling_policy)
			: effectivePolicyLabel(policyEffective, experiment.scheduling_policy)
	);
</script>

<svelte:head>
	<title>{experiment.name} · LLM Lab</title>
</svelte:head>

<header class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
	<div class="flex flex-wrap items-start gap-x-4 gap-y-3 px-4 pb-3 pt-3.5 sm:px-5">
		<a href="/experiments" class="page-header-back mt-0.5" aria-label="Back to Experiments" title="Back to Experiments">
			<ArrowLeft class="size-4" aria-hidden="true" />
		</a>
		<div class="min-w-0 flex-[1_1_20rem] space-y-1.5">
			<p class="page-eyebrow flex-wrap">
				<FlaskConical class="size-3.5" aria-hidden="true" />
				<span>Experiment</span>
				<span aria-hidden="true">·</span>
				<span>{MODE_LABELS[experiment.mode] ?? experiment.mode}</span>
				<span aria-hidden="true">·</span>
				<span class="text-foreground" title={experiment.id}>{shortExperimentId(experiment.id)}</span>
			</p>
			<h1 class="break-words font-display text-[1.6rem] font-semibold leading-tight tracking-tight text-balance sm:text-[1.85rem]">
				{experiment.name}
			</h1>
			<p class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
				<StatusBadge status={lifecycle} tones={experimentTones} />
				<span title={new Date(experiment.created_at).toLocaleString()}>Registered {dateFmt.format(new Date(experiment.created_at))}</span>
				{#if experiment.launched_at}
					<span aria-hidden="true">·</span>
					<span title={new Date(experiment.launched_at).toLocaleString()}>launched {timeAgo(experiment.launched_at)}</span>
				{/if}
				{#if experiment.completed_at && isTerminal(lifecycle)}
					<span aria-hidden="true">·</span>
					<span title={new Date(experiment.completed_at).toLocaleString()}>finished {timeAgo(experiment.completed_at)}</span>
				{/if}
				{#if experiment.cloned_from}
					<span aria-hidden="true">·</span>
					<span>clone of <a class="settings-link" href="/experiments/{experiment.cloned_from.id}">{experiment.cloned_from.name}</a></span>
				{/if}
			</p>
		</div>
		{#if actions}
			<div class="ml-auto flex shrink-0 flex-wrap items-center gap-2 max-sm:ml-0 max-sm:w-full max-sm:shrink">{@render actions()}</div>
		{/if}
	</div>

	{#if experiment.hypothesis}
		<div class="grid gap-x-4 gap-y-1 border-t border-border/60 px-4 py-3 sm:grid-cols-[8rem_minmax(0,1fr)] sm:px-5">
			<p class="section-label pt-1">Hypothesis</p>
			<blockquote class="max-w-4xl border-l-2 border-accent-2/60 pl-3 font-display text-base italic leading-relaxed text-foreground/90">
				{experiment.hypothesis}
			</blockquote>
		</div>
	{/if}

	<dl class="grid grid-cols-1 border-t border-border/60 sm:grid-cols-2 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)]">
		<div class="min-w-0 px-4 py-2.5 sm:px-5">
			<dt class="section-label">Design</dt>
			<dd class="mt-0.5 text-sm" title="{plural(shape.models, 'model')} × {plural(shape.variants, 'variant')}{shape.ablations ? ` + ${plural(shape.ablations, 'ablation')}` : ''}">
				<b class="font-mono font-medium tabular-nums">{shape.conditions}</b> condition{shape.conditions === 1 ? '' : 's'}
				<span class="text-muted-foreground">×</span>
				<b class="font-mono font-medium tabular-nums">{shape.apps}</b> app{shape.apps === 1 ? '' : 's'}
				<span class="text-muted-foreground">×</span>
				<b class="font-mono font-medium tabular-nums">{shape.repeats}</b> repeat{shape.repeats === 1 ? '' : 's'}
				<span class="text-muted-foreground">=</span>
				<b class="font-mono font-medium tabular-nums">{shape.cells.toLocaleString()}</b> cells
			</dd>
		</div>
		<div class="min-w-0 border-border/60 px-4 py-2.5 max-sm:border-t sm:border-l sm:px-5">
			<dt class="section-label">Baseline</dt>
			<dd class="mt-0.5 truncate text-sm" title={baseline ? conditionName(baseline) : undefined}>
				{#if baseline}{conditionName(baseline)}{:else}<span class="text-muted-foreground">None set</span>{/if}
			</dd>
		</div>
		<div class="min-w-0 border-t border-border/60 px-4 py-2.5 sm:px-5 xl:border-l xl:border-t-0">
			<dt class="section-label">Outcome</dt>
			<dd class="mt-0.5 text-sm" title={outcome.detail}>
				{outcome.value} <span class="text-xs text-muted-foreground">· {outcome.detail}</span>
			</dd>
		</div>
		<div class="min-w-0 border-t border-border/60 px-4 py-2.5 sm:border-l sm:px-5 xl:border-t-0">
			<dt class="section-label">Randomisation</dt>
			<dd class="mt-0.5 text-sm">
				{#if experiment.base_seed != null}
					seed <span class="font-mono text-xs">{experiment.base_seed}</span>
				{:else}
					<span title="No base seed: runs draw their own seeds (no common random numbers)">random seeds</span>
				{/if}
				<span class="text-xs text-muted-foreground">· {order}</span>
			</dd>
		</div>
	</dl>

	{#if progress}
		<div class="border-t border-border/60 px-4 py-2.5 sm:px-5">{@render progress()}</div>
	{/if}
	{#if nav}
		<div class="page-header-nav">{@render nav()}</div>
	{/if}
</header>
