<script lang="ts">
	/**
	 * StudyMasthead — the study record's header, set like the first page of a paper (DESIGN_SYSTEM
	 * "Experiment page"): accession id, mode and kind as the eyebrow, the serif title, status and dates,
	 * the lock badge with `prereg_hash`, the hypothesis as a pull quote, a spec strip (design formula ·
	 * outcome · evaluation · randomisation), then the progress strip and section tabs. Owns the page
	 * `<h1>` and `<title>` (it stands in for PageHeader on this page).
	 *
	 * Props: `study` (`StudyOut`), `actions`, `progress`, `nav` snippets.
	 */
	import type { Snippet } from 'svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Lock from '@lucide/svelte/icons/lock';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import type { StudyOut } from '$lib/api/v3/types';
	import { KIND_LABELS, MODE_LABELS, STUDY_STATUS_TONES, accessionId, pauseLabel, plural, shortHash } from '../format';

	interface Props {
		study: StudyOut;
		actions?: Snippet;
		progress?: Snippet;
		nav?: Snippet;
	}

	let { study, actions, progress, nav }: Props = $props();

	$effect(() => {
		const title = study.name;
		pageTitle.current = title;
		return () => {
			if (pageTitle.current === title) pageTitle.current = '';
		};
	});

	const plan = $derived(study.active_plan);
	const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
	const primary = $derived(String((study.analysis_plan as Record<string, unknown> | null)?.primary_metric ?? (study.mode === 'slot' ? 'pattern prevalence' : 'pass rate')).replace(/_/g, ' '));
	const units = $derived(study.mode === 'app' ? study.design.units.tasks.length : study.design.units.slot_targets.length);
	const conditions = $derived(plan?.n_conditions ?? null);
</script>

<svelte:head>
	<title>{study.name} · LLM Lab</title>
</svelte:head>

<header class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)] {nav ? 'sm-has-nav' : ''}">
	<div class="flex flex-wrap items-start gap-x-4 gap-y-3 px-4 pb-3 pt-3.5 sm:px-5">
		<a href="/experiments" class="page-header-back mt-0.5" aria-label="Back to Experiments" title="Back to Experiments">
			<ArrowLeft class="size-4" aria-hidden="true" />
		</a>
		<div class="min-w-0 flex-[1_1_20rem] space-y-1.5">
			<p class="page-eyebrow flex-wrap">
				<FlaskConical class="size-3.5" aria-hidden="true" />
				<span>Experiment</span>
				<span aria-hidden="true">·</span>
				<span>{MODE_LABELS[study.mode]}</span>
				<span aria-hidden="true">·</span>
				<span>{KIND_LABELS[study.kind]}</span>
				<span aria-hidden="true">·</span>
				<span class="text-foreground" title={study.id}>{accessionId(study.id)}</span>
			</p>
			<h1 class="break-words font-display text-[1.6rem] font-semibold leading-tight tracking-tight text-balance sm:text-[1.85rem]">{study.name}</h1>
			<p class="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
				<StatusBadge status={study.status} tone={STUDY_STATUS_TONES[study.status]} />
				{#if study.status === 'paused' && study.pause_reason}<span class="text-warning-fg">{pauseLabel(study.pause_reason)}</span>{/if}
				{#if study.stop_reason}<span class="text-warning-fg">stopped: {study.stop_reason.code.replace(/_/g, ' ')}{study.stop_reason.detail ? ` (${study.stop_reason.detail.replace(/(\d+\.\d{2})\d+/g, '$1')})` : ''}</span>{/if}
				{#if study.created_at}<span>Registered {dateFmt.format(new Date(study.created_at))}</span>{/if}
				{#if study.launched_at}<span aria-hidden="true">·</span><span>launched {dateFmt.format(new Date(study.launched_at))}</span>{/if}
				{#if study.completed_at}<span aria-hidden="true">·</span><span>finished {dateFmt.format(new Date(study.completed_at))}</span>{/if}
				{#if study.cloned_from}<span aria-hidden="true">·</span><span>clone of <a class="settings-link" href="/experiments/{study.cloned_from}">{accessionId(study.cloned_from)}</a></span>{/if}
				{#if study.locked}
					<span class="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-foreground" title="Pre-registered: design and analysis plan are locked. prereg_hash {study.prereg_hash ?? ''}">
						<Lock class="size-3" aria-hidden="true" /> Pre-registered <span class="font-mono">{shortHash(study.prereg_hash)}</span>
					</span>
				{/if}
			</p>
		</div>
		{#if actions}
			<div class="ml-auto flex shrink-0 flex-wrap items-center gap-2 max-sm:ml-0 max-sm:w-full max-sm:shrink">{@render actions()}</div>
		{/if}
	</div>
	{#if study.hypothesis}
		<div class="grid gap-x-4 gap-y-1 border-t border-border/60 px-4 py-3 sm:grid-cols-[8rem_minmax(0,1fr)] sm:px-5">
			<p class="section-label pt-1">Hypothesis</p>
			<blockquote class="max-w-4xl border-l-2 border-accent-2/60 pl-3 font-display text-base italic leading-relaxed text-foreground/90">{study.hypothesis}</blockquote>
		</div>
	{/if}
	<dl class="grid grid-cols-1 border-t border-border/60 sm:grid-cols-2 xl:grid-cols-4">
		<div class="min-w-0 px-4 py-2.5 sm:px-5">
			<dt class="section-label">Design</dt>
			<dd class="mt-0.5 text-sm">
				<b class="font-mono font-medium tabular-nums">{conditions ?? '—'}</b> conditions
				<span class="text-muted-foreground">×</span>
				<b class="font-mono font-medium tabular-nums">{units}</b> {study.mode === 'app' ? 'tasks' : 'targets'}
				<span class="text-muted-foreground">×</span>
				<b class="font-mono font-medium tabular-nums">{study.repeats}</b> repeats
				{#if plan}<span class="text-muted-foreground">=</span> <b class="font-mono font-medium tabular-nums">{plan.n_cells.toLocaleString()}</b> cells{/if}
			</dd>
		</div>
		<div class="min-w-0 border-border/60 px-4 py-2.5 max-sm:border-t sm:border-l sm:px-5">
			<dt class="section-label">Outcome</dt>
			<dd class="mt-0.5 text-sm">{primary} <span class="text-xs text-muted-foreground">· intention-to-treat</span></dd>
		</div>
		<div class="min-w-0 border-t border-border/60 px-4 py-2.5 sm:px-5 xl:border-l xl:border-t-0">
			<dt class="section-label">Evaluation</dt>
			<dd class="mt-0.5 text-sm">{study.design.evaluation.eval_level} <span class="text-xs text-muted-foreground">· {study.design.evaluation.eval_suite.split('#')[0]}</span></dd>
		</div>
		<div class="min-w-0 border-t border-border/60 px-4 py-2.5 sm:border-l sm:px-5 xl:border-t-0">
			<dt class="section-label">Randomisation</dt>
			<dd class="mt-0.5 text-sm">
				seed <span class="font-mono text-xs">{study.base_seed}</span>
				<span class="text-xs text-muted-foreground">· CRN per (unit, repeat) · {study.scheduling}{plan ? ` · ${plural(plan.n_blocks, 'block')}` : ''}</span>
			</dd>
		</div>
	</dl>
	{#if progress}<div class="border-t border-border/60 px-4 py-2.5 sm:px-5">{@render progress()}</div>{/if}
</header>
{#if nav}
	<!-- Sticky under the app top bar, so the sections stay one click away on long pages. -->
	<div class="sm-sticky">
		<span class="sm-mini" title={study.name}>{study.name}</span>
		<div class="min-w-0 flex-1 overflow-x-auto">{@render nav()}</div>
	</div>
{/if}

<style>
	.sm-has-nav {
		border-bottom-left-radius: 0;
		border-bottom-right-radius: 0;
		margin-bottom: 0 !important;
	}
	.sm-sticky {
		position: sticky;
		top: 3rem;
		z-index: 20;
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0 !important;
		border: 1px solid var(--border);
		border-top-color: color-mix(in oklab, var(--border) 60%, transparent);
		border-radius: 0 0 var(--radius) var(--radius);
		background: color-mix(in oklab, var(--card) 92%, transparent);
		backdrop-filter: blur(8px);
		padding-inline: 0.5rem;
	}
	.sm-sticky :global([role='tablist']) {
		border-bottom: 0;
	}
	/* The name shows only once the masthead has scrolled away (pure CSS: scroll-driven where supported). */
	.sm-mini {
		display: none;
		max-width: 20rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		padding-left: 0.5rem;
		font-family: var(--font-display);
		font-size: 0.875rem;
		font-weight: 600;
	}
	@supports (animation-timeline: scroll()) {
		@media (min-width: 1024px) and (prefers-reduced-motion: no-preference) {
			.sm-mini {
				display: block;
				animation: sm-reveal linear both;
				animation-timeline: scroll(root);
				animation-range: 260px 300px;
			}
		}
	}
	@keyframes sm-reveal {
		from {
			opacity: 0;
			max-width: 0;
			padding-left: 0;
		}
		to {
			opacity: 1;
			max-width: 20rem;
		}
	}
</style>
