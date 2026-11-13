<script lang="ts">
	/**
	 * RunHeader — the sample page header (spec 10.4). Leads with what was built and by which model, the
	 * run's state badges, then a verdict strip (pass rate, security probes, adherence, build, code, cost,
	 * time, tokens) and a one-line "Setup" disclosure with the factor labels. Owns the page <h1>/title.
	 *
	 * Props: `run`, `back` (defaults to the study or the Applications list), `actions` and `nav` snippets
	 * (the page puts its tabs in `nav`), `stream` (SSE state for the live dot).
	 */
	import type { Snippet } from 'svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Scissors from '@lucide/svelte/icons/scissors';
	import FileCode from '@lucide/svelte/icons/file-code';
	import Copy from '@lucide/svelte/icons/copy';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { toast } from 'svelte-sonner';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import type { RunOut } from '$lib/api/v3/types';
	import { fmtInt, fmtRate, fmtSeconds, fmtUsd, kindLabel, OUTCOME_TONES, outcomeLabel, shortSha } from './format';

	interface Props {
		run: RunOut;
		back?: { href: string; label: string };
		actions?: Snippet;
		nav?: Snippet;
		stream?: string;
	}

	let { run, back, actions, nav, stream = 'idle' }: Props = $props();

	const what = $derived(run.task_title ?? run.labels?.task ?? run.labels?.unit ?? kindLabel(run.kind));
	const modelName = $derived(run.model ? run.model.split('/').pop() : null);
	const title = $derived(modelName ? `${what} · ${modelName}` : what);
	$effect(() => {
		const t = title;
		pageTitle.current = t;
		return () => {
			if (pageTitle.current === t) pageTitle.current = '';
		};
	});

	const backLink = $derived(
		back ??
			(run.study.study_id
				? { href: `/experiments/${run.study.study_id}?tab=data`, label: run.study.study_name ?? 'Experiment' }
				: { href: '/applications', label: 'Applications' }),
	);
	const live = $derived(run.status === 'running' || run.status === 'pending');
	const KindIcon = $derived(run.kind === 'slot_single_shot' || run.kind === 'slot_agent' ? Scissors : run.kind === 'app_agentic' ? AppWindow : FileCode);

	// Setup factors (model is in the title).
	const factors = $derived(Object.entries(run.labels ?? {}).filter(([key]) => key !== 'model'));
	const KEY_FACTORS = ['stack_profile', 'harness_preset', 'style', 'sampling_preset'];
	const preview = $derived(
		KEY_FACTORS.map((k) => run.labels?.[k]).filter((v): v is string => !!v),
	);
	let setupOpen = $state(false);

	// ── Verdict figures ─────────────────────────────────────────────────────────
	const evaluation = $derived(run.latest_evaluation);
	const em = $derived(evaluation?.result?.metrics as Record<string, unknown> | undefined);
	const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
	const metrics = $derived((run.metrics ?? {}) as Record<string, unknown>);
	const lastAttempt = $derived(run.attempts.at(-1));
	const timeSplit = $derived((lastAttempt as unknown as { time_split?: Record<string, number> } | undefined)?.time_split ?? null);
	const collection = $derived((run.result?.collection ?? null) as { files?: number; by_origin?: Record<string, number> } | null);

	type Tone = 'good' | 'bad' | 'warn' | 'neutral';
	interface Fig {
		label: string;
		value: string;
		sub?: string;
		tone?: Tone;
		/** 0–1 bar under the value. */
		bar?: number | null;
		title?: string;
	}

	const figures = $derived.by<Fig[]>(() => {
		const out: Fig[] = [];
		const isApp = run.kind === 'app_agentic' || run.kind === 'reference';
		const passRate = num(run.agent_pass_rate ?? em?.agent_pass_rate ?? em?.slot_pass_rate);
		const checks = num(em?.agent_checks);
		const passed = num(em?.agent_passed);
		if (evaluation?.result?.measured === false) {
			out.push({ label: 'Pass rate', value: '—', sub: evaluation.result.not_measured_reason ?? 'not measured', tone: 'neutral' });
		} else {
			out.push({
				label: isApp ? 'Pass rate' : 'Slot pass rate',
				value: fmtRate(passRate, '—'),
				sub: checks != null && passed != null ? `${passed} / ${checks} checks` : evaluation ? evaluation.status : 'not evaluated',
				bar: passRate,
				tone: passRate == null ? 'neutral' : passRate >= 0.9 ? 'good' : passRate >= 0.5 ? 'warn' : 'bad',
				title: 'Agent checks only; infra and flaky checks excluded',
			});
		}
		const probesTested = num(em?.probes_tested);
		if (probesTested) {
			const exploited = num(em?.probes_exploited) ?? 0;
			out.push({
				label: 'Security',
				value: `${exploited} / ${probesTested}`,
				sub: exploited ? 'probes exploited' : 'none exploited',
				tone: exploited ? 'bad' : 'good',
				title: 'Security probes (IDOR, plaintext passwords, mass assignment…) that succeeded against the app',
			});
		}
		const adherence = evaluation?.adherence?.status;
		if (adherence && adherence !== 'not_measured') {
			out.push({
				label: 'Adherence',
				value: adherence[0].toUpperCase() + adherence.slice(1),
				sub: evaluation?.adherence?.deviations?.length ? `${evaluation.adherence.deviations.length} deviation(s)` : 'used the requested stack',
				tone: adherence === 'compliant' ? 'good' : adherence === 'partial' ? 'warn' : 'bad',
			});
		}
		const build = evaluation?.stages?.build;
		if (build) {
			const ok = build.status === 'ok';
			out.push({
				label: 'Build',
				value: ok ? 'OK' : build.status,
				sub: build.seconds != null ? `in ${fmtSeconds(build.seconds)}` : undefined,
				tone: ok ? 'good' : 'bad',
			});
		}
		const loc = num(metrics.loc_model);
		if (loc != null) {
			out.push({ label: 'Code', value: `${fmtInt(loc)}`, sub: `LOC written${collection?.files ? ` · ${collection.files} files` : ''}`, title: 'Source lines the model authored or modified' });
		}
		out.push({
			label: 'Cost',
			value: fmtUsd(run.cost_usd),
			sub: num(metrics.list_cost_usd) != null ? `list ${fmtUsd(num(metrics.list_cost_usd))}` : undefined,
			title: 'Billed cost; "list" is the list-price equivalent of the same usage',
		});
		out.push({
			label: 'Time',
			value: fmtSeconds(run.wall_s),
			sub: timeSplit ? `LLM ${fmtSeconds(timeSplit.llm_s)} · tools ${fmtSeconds(timeSplit.tool_s)}` : 'wall clock',
		});
		const pin = num(metrics.prompt_tokens);
		const pout = num(metrics.completion_tokens);
		const cached = num(metrics.cached_tokens);
		if (pin != null) {
			const compact = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));
			out.push({
				label: 'Tokens',
				value: compact(pin),
				sub: `in · ${pout != null ? compact(pout) : '—'} out${cached && pin ? ` · ${Math.round((cached / pin) * 100)}% cached` : ''}`,
			});
		}
		return out;
	});

	let headerEl = $state<HTMLElement | null>(null);
	let condensed = $state(false);
	$effect(() => {
		if (!headerEl) return;
		const io = new IntersectionObserver(([e]) => (condensed = !e.isIntersecting), { rootMargin: '-96px 0px 0px 0px', threshold: 0 });
		io.observe(headerEl);
		return () => io.disconnect();
	});

	function copyId() {
		navigator.clipboard.writeText(run.id).then(() => toast.success('Run ID copied'));
	}
</script>

<svelte:head>
	<title>{title} · LLM Lab</title>
</svelte:head>

<header class="rh" class:has-nav={!!nav} bind:this={headerEl}>
	<div class="rh-top">
		<a href={backLink.href} class="page-header-back" aria-label="Back to {backLink.label}" title="Back to {backLink.label}">
			<ArrowLeft class="size-4" aria-hidden="true" />
		</a>
		<span class="rh-tile" aria-hidden="true"><KindIcon class="size-5" /></span>
		<div class="rh-id">
			<div class="rh-titlebar">
				<h1 class="rh-title">
					{what}{#if modelName}{" "}<span class="rh-by">by</span>{" "}<a class="rh-model" href="/models?q={encodeURIComponent(modelName)}" title={run.model}>{modelName}</a>{/if}
				</h1>
				<StatusBadge status={run.status} />
				{#if run.outcome}<StatusBadge status={run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(run.outcome)} />{/if}
				{#if run.usable === false}<span class="rh-badge" title="Infra error or excluded: not analysed as data">not usable</span>{/if}
				{#if run.validity?.truncated}<span class="rh-badge rh-badge-warn" title="Truncated: included in the default (ITT) analysis, excluded in the sensitivity analysis">truncated</span>{/if}
				{#if run.beyond_plan}<span class="rh-badge" title="Created on a cell that already had a usable run: excluded from results and analysis exports">beyond plan</span>{/if}
				{#if live}
					<span class="rh-live" role="status" title={stream === 'open' ? 'Live: updates as the run progresses' : 'Refreshing every few seconds'}>
						<span class="status-dot status-dot-live h-1.5 w-1.5 text-info" aria-hidden="true"></span>
						{run.status === 'pending' ? 'queued, waiting for the executor' : run.phase ? run.phase.replace(/_/g, ' ') : 'running'}{stream === 'open' ? ' · live' : ''}
					</span>
				{/if}
			</div>
			<div class="rh-sub">
				<span>{kindLabel(run.kind)}</span>
				{#if run.study.study_id}
					<span aria-hidden="true" class="opacity-40">·</span>
					<a href="/experiments/{run.study.study_id}?tab=data" class="truncate hover:text-foreground" title="Experiment">{run.study.study_name ?? 'Experiment'}</a>
				{/if}
				<span aria-hidden="true" class="opacity-40">·</span>
				<button type="button" class="rh-copy" onclick={copyId} title="Copy run ID">{shortSha(run.id, 8)}<Copy class="size-3" aria-hidden="true" /></button>
				{#if run.attempts.length > 1}<span aria-hidden="true" class="opacity-40">·</span><span>{run.attempts.length} attempts</span>{/if}
			</div>
		</div>
		{#if actions}<div class="rh-actions">{@render actions()}</div>{/if}
	</div>

	<div class="rh-figs" aria-label="Verdict">
		{#each figures as f (f.label)}
			<div class="rh-fig" data-tone={f.tone ?? 'neutral'} title={f.title}>
				<span class="rh-fig-label">{f.label}</span>
				<span class="rh-fig-value">{f.value}</span>
				{#if f.bar != null}<span class="rh-bar" aria-hidden="true"><span style:width="{Math.max(2, f.bar * 100)}%"></span></span>{/if}
				{#if f.sub}<span class="rh-fig-sub">{f.sub}</span>{/if}
			</div>
		{/each}
	</div>

	{#if factors.length}
		<div class="rh-setup">
			<button type="button" class="rh-setup-toggle" aria-expanded={setupOpen} onclick={() => (setupOpen = !setupOpen)}>
				<span class="rh-setup-label">Setup</span>
				{#if !setupOpen}
					<span class="rh-setup-preview">
						{#each preview as v (v)}<span class="rh-chip">{v}</span>{/each}
						{#if factors.length > preview.length}<span class="text-muted-foreground">+{factors.length - preview.length} more</span>{/if}
					</span>
				{:else}
					<span class="text-muted-foreground">{factors.length} factors</span>
				{/if}
				<ChevronDown class="ml-auto size-3.5 shrink-0 transition-transform duration-150 {setupOpen ? 'rotate-180' : ''}" aria-hidden="true" />
			</button>
			{#if setupOpen}
				<dl class="rh-setup-grid">
					{#each factors as [key, value] (key)}
						<div><dt>{key.replace(/_/g, ' ')}</dt><dd>{value}</dd></div>
					{/each}
					{#if run.model}<div><dt>model</dt><dd>{run.model}</dd></div>{/if}
				</dl>
			{/if}
		</div>
	{/if}

</header>

{#if nav}
	<!-- Sticky tab bar under the app top bar; shows what this page is once the header scrolls away. -->
	<div class="rh-sticky" class:is-condensed={condensed}>
		<div class="rh-mini" aria-hidden={!condensed}>
			<span class="rh-mini-tile"><KindIcon class="size-3.5" /></span>
			<span class="rh-mini-title">{title}</span>
			{#if run.outcome}<StatusBadge status={run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(run.outcome)} />{/if}
		</div>
		<div class="rh-nav">{@render nav()}</div>
	</div>
{/if}

<style>
	.rh {
		overflow: hidden;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.rh-top {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem 1rem;
		padding: 1rem 1.25rem 0.875rem;
	}
	.rh-tile {
		display: inline-flex;
		width: 2.75rem;
		height: 2.75rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: calc(var(--radius) + 0.25rem);
		border: 1px solid var(--border);
		background: color-mix(in oklab, var(--primary) 9%, var(--card));
		color: var(--primary);
	}
	.rh-id {
		display: flex;
		min-width: 0;
		flex: 1 1 22rem;
		flex-direction: column;
		gap: 0.25rem;
	}
	.rh-titlebar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.375rem 0.5rem;
	}
	.rh-title {
		margin-right: 0.25rem;
		font-family: var(--font-display);
		font-size: clamp(1.375rem, 1.1rem + 0.9vw, 1.75rem);
		font-weight: 600;
		line-height: 1.15;
		letter-spacing: -0.015em;
		overflow-wrap: anywhere;
	}
	.rh-by {
		font-weight: 400;
		font-style: italic;
		color: var(--muted-foreground);
	}
	.rh-model {
		font-weight: 500;
		font-style: italic;
		color: var(--primary);
	}
	.rh-model:hover {
		text-decoration: underline;
		text-underline-offset: 3px;
	}
	.rh-badge {
		display: inline-flex;
		height: 1.25rem;
		align-items: center;
		border-radius: 9999px;
		border: 1px solid var(--border);
		padding-inline: 0.5rem;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--muted-foreground);
	}
	.rh-badge-warn {
		color: var(--warning-fg);
		border-color: color-mix(in oklab, var(--warning) 40%, transparent);
		background: color-mix(in oklab, var(--warning) 10%, transparent);
	}
	.rh-live {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.rh-sub {
		display: flex;
		min-width: 0;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 0.5rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.rh-copy {
		display: inline-flex;
		cursor: pointer;
		align-items: center;
		gap: 0.3rem;
		font-family: var(--font-mono);
	}
	.rh-copy:hover {
		color: var(--foreground);
	}
	.rh-actions {
		display: flex;
		flex-shrink: 0;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		margin-left: auto;
	}

	/* Verdict strip */
	.rh-figs {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
		border-top: 1px solid var(--border);
	}
	.rh-fig {
		position: relative;
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.75rem 0.875rem 0.875rem;
		border-right: 1px solid var(--border);
	}
	.rh-fig:last-child {
		border-right: 0;
	}
	/* Tone: a short coloured tick on top, and the value tinted only for good/bad verdicts. */
	.rh-fig::before {
		content: '';
		position: absolute;
		top: -1px;
		left: 0.875rem;
		width: 1.25rem;
		height: 2px;
		background: transparent;
	}
	.rh-fig[data-tone='good']::before {
		background: var(--success);
	}
	.rh-fig[data-tone='warn']::before {
		background: var(--warning);
	}
	.rh-fig[data-tone='bad']::before {
		background: var(--destructive);
	}
	.rh-fig[data-tone='good'] .rh-fig-value {
		color: var(--success-fg);
	}
	.rh-fig[data-tone='bad'] .rh-fig-value {
		color: var(--danger-fg);
	}
	.rh-fig-label {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.rh-fig-value {
		font-family: var(--font-mono);
		font-size: 1.25rem;
		font-weight: 500;
		line-height: 1.1;
		letter-spacing: -0.02em;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.rh-fig-sub {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.rh-bar {
		display: block;
		height: 3px;
		max-width: 6rem;
		border-radius: 9999px;
		background: var(--muted);
		overflow: hidden;
	}
	.rh-bar > span {
		display: block;
		height: 100%;
		background: currentColor;
		color: var(--muted-foreground);
	}
	.rh-fig[data-tone='good'] .rh-bar > span {
		background: var(--success);
	}
	.rh-fig[data-tone='warn'] .rh-bar > span {
		background: var(--warning);
	}
	.rh-fig[data-tone='bad'] .rh-bar > span {
		background: var(--destructive);
	}

	/* Setup disclosure */
	.rh-setup {
		border-top: 1px solid var(--border);
	}
	.rh-setup-toggle {
		display: flex;
		width: 100%;
		min-width: 0;
		cursor: pointer;
		align-items: center;
		gap: 0.75rem;
		padding: 0.5rem 1.25rem;
		font-size: 0.75rem;
		text-align: left;
		transition: background-color 150ms;
	}
	.rh-setup-toggle:hover {
		background: var(--surface-2, var(--muted));
	}
	.rh-setup-toggle:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.rh-setup-label {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.rh-setup-preview {
		display: flex;
		min-width: 0;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.375rem;
		overflow: hidden;
		max-height: 1.375rem;
	}
	.rh-chip {
		border-radius: 0.25rem;
		border: 1px solid var(--border);
		padding: 0 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		line-height: 1.125rem;
	}
	.rh-setup-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
		gap: 0.5rem 1.25rem;
		padding: 0.25rem 1.25rem 0.875rem;
	}
	.rh-setup-grid dt {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.rh-setup-grid dd {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		overflow-wrap: anywhere;
	}

	.rh.has-nav {
		border-bottom-left-radius: 0;
		border-bottom-right-radius: 0;
		margin-bottom: 0 !important;
	}
	.rh-sticky {
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
		transition: box-shadow 200ms;
	}
	.rh-sticky.is-condensed {
		border-top-color: var(--border);
		box-shadow: var(--shadow-pop);
	}
	.rh-mini {
		display: flex;
		flex-shrink: 0;
		align-items: center;
		gap: 0.5rem;
		max-width: 0;
		overflow: hidden;
		opacity: 0;
		transition: max-width 220ms ease-out, opacity 160ms ease-out;
	}
	.rh-sticky.is-condensed .rh-mini {
		max-width: 26rem;
		opacity: 1;
		padding-left: 0.5rem;
	}
	.rh-mini-tile {
		display: inline-flex;
		width: 1.5rem;
		height: 1.5rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 0.375rem;
		border: 1px solid var(--border);
		color: var(--primary);
	}
	.rh-mini-title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-display);
		font-size: 0.9375rem;
		font-weight: 600;
	}
	.rh-nav {
		display: flex;
		min-width: 0;
		flex: 1 1 auto;
	}
	.rh-nav :global([role='tablist']) {
		border-bottom: 0;
	}
	@media (prefers-reduced-motion: reduce) {
		.rh-mini,
		.rh-sticky {
			transition: none;
		}
	}
	@media (max-width: 900px) {
		.rh-sticky.is-condensed .rh-mini :global(.status-badge),
		.rh-sticky.is-condensed .rh-mini :global([data-slot='badge']) {
			display: none;
		}
		.rh-sticky.is-condensed .rh-mini {
			max-width: 10rem;
		}
	}

	@media (max-width: 640px) {
		.rh-top {
			padding: 0.875rem 1rem 0.75rem;
		}
		.rh-figs {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.rh-fig:nth-child(2n) {
			border-right: 0;
		}
		.rh-fig {
			border-bottom: 1px solid var(--border);
			padding-inline: 1rem;
		}
		.rh-actions {
			margin-left: 0;
			width: 100%;
		}
	}
</style>
