<script lang="ts">
	/**
	 * AbstractSection — section 1 of the study record, "Overview" (spec 10.3). Figures first:
	 *  1. a status line that says where the experiment is and what to do next;
	 *  2. four headline figures: progress, spend against the cap, attrition, conditions measured;
	 *  3. the leaderboard as a chart (mean with its 95% CI per condition; p-values withheld while interim);
	 *  4. "About": the description, the generated design paragraph, the lock and `prereg_hash`.
	 * Nothing here is new data: every figure comes from the study, its results and its live budget.
	 *
	 * Props: `study`, `results` (null until loaded), `cellsDone`, `cellsTotal`, `budget` (live, optional),
	 * `onOpenTab(tab)`.
	 */
	import type { StudyBudgetOut, StudyOut, StudyResultsOut } from '$lib/api/v3/types';
	import StudySection from '../study/StudySection.svelte';
	import { KIND_LABELS, MODE_LABELS, formatPct, formatUsd, pauseLabel, plural, shortHash } from '../format';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';

	interface Props {
		study: StudyOut;
		results: StudyResultsOut | null;
		cellsDone: number;
		cellsTotal: number;
		budget?: StudyBudgetOut | null;
		onOpenTab?: (tab: string) => void;
	}

	let { study, results, cellsDone, cellsTotal, budget = null, onOpenTab }: Props = $props();

	const units = $derived(study.mode === 'app' ? study.design.units.tasks.length : study.design.units.slot_targets.length);
	const varied = $derived(study.design.factors.filter((f) => f.levels.length > 1).map((f) => f.key.replace(/_/g, ' ')));
	const primary = $derived(String((study.analysis_plan as Record<string, unknown> | null)?.primary_metric ?? 'pass_rate').replace(/_/g, ' '));

	// ── Status line + next step ──────────────────────────────────────────────────
	type Tone = 'info' | 'good' | 'warn' | 'muted';
	const statusLine = $derived.by<{ tone: Tone; text: string; action?: { label: string; tab: string } }>(() => {
		switch (study.status) {
			case 'draft':
			case 'planned':
				return { tone: 'muted', text: 'Not started yet.', action: { label: 'Review the design', tab: 'method' } };
			case 'paused':
				return { tone: 'warn', text: `Paused (${pauseLabel(study.pause_reason)}): ${cellsDone} of ${cellsTotal} cells done.`, action: { label: 'Open Operations', tab: 'operations' } };
			case 'completed':
				return { tone: 'good', text: `Completed: ${plural(cellsDone, 'cell')} done.`, action: { label: 'Read the results', tab: 'results' } };
			case 'stopped':
				return {
					tone: 'warn',
					text: `Stopped early (${study.stop_reason?.code.replace(/_/g, ' ') ?? 'unknown'}): ${cellsDone} of ${cellsTotal} cells done; budget stops are balanced across units.`,
					action: { label: 'See what ran', tab: 'operations' },
				};
			case 'cancelled':
				return { tone: 'muted', text: `Cancelled: ${cellsDone} of ${cellsTotal} cells done. Cancelled cells are excluded, never results.`, action: { label: 'See the runs', tab: 'data' } };
			default:
				return {
					tone: 'info',
					text: `${study.status === 'piloting' ? 'Piloting' : 'Running'}: ${cellsDone} of ${cellsTotal} cells done.`,
					action: { label: 'Watch it run', tab: 'operations' },
				};
		}
	});

	// ── Headline figures ─────────────────────────────────────────────────────────
	const progress = $derived(cellsTotal ? cellsDone / cellsTotal : null);
	const spent = $derived(budget?.spent ?? null);
	const cap = $derived(budget?.cap ?? null);
	const spendFrac = $derived(spent !== null && cap ? spent / cap : null);
	const attrition = $derived.by(() => {
		const rows = results?.attrition ?? [];
		const planned = rows.reduce((a, r) => a + r.planned_cells, 0);
		const lost = rows.reduce((a, r) => a + r.excluded + r.infra_error, 0);
		return planned ? { rate: lost / planned, lost, planned } : null;
	});
	const measured = $derived(results ? results.leaderboard.filter((r) => r.pass_rate_mean !== null).length : null);

	// ── Leaderboard chart ────────────────────────────────────────────────────────
	const MAX_ROWS = 10;
	const rows = $derived([...(results?.leaderboard ?? [])].sort((a, b) => (b.pass_rate_mean ?? -1) - (a.pass_rate_mean ?? -1)));
	const significant = $derived(
		results && !results.interim ? results.paired.rows.filter((r) => r.p_adjusted !== null && r.p_adjusted < results.paired.alpha).length : 0,
	);
</script>

<StudySection id="abstract" number="1" title="Overview">
	<div class="space-y-4">
		<!-- 1 · Status -->
		<div class="ov-status" data-tone={statusLine.tone} role="status">
			<span class="ov-status-dot" aria-hidden="true"></span>
			<p class="min-w-0 flex-1 text-sm">{statusLine.text}</p>
			{#if statusLine.action && onOpenTab}
				{@const a = statusLine.action}
				<button type="button" class="ov-next" onclick={() => onOpenTab?.(a.tab)}>{a.label}<ArrowRight class="size-3.5" /></button>
			{/if}
		</div>

		<!-- 2 · Headline figures -->
		<dl class="ov-figs">
			<div class="ov-fig">
				<dt>Progress</dt>
				<dd class="ov-val">{cellsDone}<span class="ov-of">/{cellsTotal}</span></dd>
				<dd class="ov-bar"><span style:width="{(progress ?? 0) * 100}%" data-tone={progress !== null && progress >= 1 ? 'good' : 'info'}></span></dd>
				<dd class="ov-sub">{cellsTotal ? 'main cells done' : 'no main-stage cells yet (pilot only)'}</dd>
			</div>
			<div class="ov-fig">
				<dt>Spend</dt>
				<dd class="ov-val">{formatUsd(spent)}<span class="ov-of">{cap ? ` / ${formatUsd(cap)}` : ''}</span></dd>
				<dd class="ov-bar"><span style:width="{Math.min(1, spendFrac ?? 0) * 100}%" data-tone={spendFrac !== null && spendFrac >= 0.8 ? 'warn' : 'muted'}></span></dd>
				<dd class="ov-sub">{spendFrac !== null ? `${formatPct(spendFrac)} of the cap` : budget ? 'no cap set' : 'loading…'}</dd>
			</div>
			<div class="ov-fig">
				<dt>Attrition</dt>
				<dd class="ov-val {attrition && attrition.rate >= 0.2 ? 'text-warning-fg' : ''}">{attrition ? formatPct(attrition.rate, 1) : '—'}</dd>
				<dd class="ov-sub">{attrition ? `${attrition.lost} of ${attrition.planned} planned cells excluded or infra` : results ? 'no cells yet' : 'loading…'}</dd>
			</div>
			<div class="ov-fig">
				<dt>Conditions measured</dt>
				<dd class="ov-val">{measured ?? '—'}<span class="ov-of">{results ? `/${results.leaderboard.length}` : ''}</span></dd>
				<dd class="ov-sub">
					{results
						? measured === 0
							? 'nothing measured yet'
							: results.interim
							? 'interim: p-values withheld'
							: significant
								? `${significant} differ from the baseline (${results.paired.correction})`
								: 'none differs from the baseline after correction'
						: 'loading…'}
				</dd>
			</div>
		</dl>

		<!-- 3 · Leaderboard -->
		{#if rows.length && measured === 0}
			<p class="ov-card px-4 py-3 text-sm text-muted-foreground">
				No condition has a measured {primary} yet ({plural(rows.length, 'condition')} planned). Results appear here once main-stage cells are evaluated.
			</p>
		{:else if rows.length}
			<figure class="ov-card">
				<figcaption class="ov-card-head">
					<h3 class="ov-h">Mean {primary} by condition</h3>
					<span class="text-xs text-muted-foreground">bar = mean · whisker = 95% CI · intention-to-treat</span>
					{#if onOpenTab}<button type="button" class="ov-link" onclick={() => onOpenTab?.('results')}>All results →</button>{/if}
				</figcaption>
				<ol class="ov-board">
					{#each rows.slice(0, MAX_ROWS) as r (r.condition_id)}
						{@const m = r.pass_rate_mean}
						<li class="ov-row">
							<span class="ov-label" title={r.label}>{r.label}{#if r.is_baseline}<span class="ov-base">baseline</span>{/if}</span>
							<span class="ov-track" aria-hidden="true">
								{#if m !== null}
									<span class="ov-fill" style:width="{m * 100}%"></span>
									{#if r.pass_rate_ci95}
										<span class="ov-ci" style:left="{r.pass_rate_ci95[0] * 100}%" style:width="{(r.pass_rate_ci95[1] - r.pass_rate_ci95[0]) * 100}%"></span>
									{/if}
								{/if}
							</span>
							<span class="ov-num">{#if m === null}<span class="text-muted-foreground">not measured</span>{:else}{formatPct(m, 1)}{/if}</span>
							<span class="ov-n" title="units · graded runs">{r.n_units} units · {r.n_graded} graded</span>
						</li>
					{/each}
				</ol>
				{#if rows.length > MAX_ROWS}<p class="px-4 pb-3 text-xs text-muted-foreground">Top {MAX_ROWS} of {rows.length} conditions.</p>{/if}
				{#if results?.gate.deviations.length}<p class="px-4 pb-3 text-xs text-warning-fg">Deviations from the plan: {results.gate.deviations.join(', ').replace(/_/g, ' ')}.</p>{/if}
			</figure>
		{/if}

		<!-- 4 · About -->
		<section class="ov-card ov-about">
			<h3 class="ov-h">About this experiment</h3>
			<div class="max-w-3xl space-y-2.5 font-display text-[15px] leading-relaxed">
				{#if study.description}<p>{study.description}</p>{/if}
				<p class="text-muted-foreground">
					{study.kind === 'exploratory' ? 'An' : 'A'} {KIND_LABELS[study.kind].toLowerCase()} {MODE_LABELS[study.mode].toLowerCase()} experiment
					{#if varied.length}varying {varied.join(', ')}{/if}
					over {plural(units, study.mode === 'app' ? 'task' : 'slot target')} with {plural(study.repeats, 'repeat')} per condition and unit
					(common random numbers: every condition gets the same seed for the same unit and repeat).
					The outcome is {primary}, analysed by intention-to-treat; excluding truncated runs is a sensitivity analysis.
					{#if study.locked}The design and analysis plan were pre-registered at launch (<span class="font-mono text-sm">prereg {shortHash(study.prereg_hash)}</span>).{/if}
				</p>
			</div>
		</section>
	</div>
</StudySection>

<style>
	.ov-status {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		padding: 0.625rem 0.875rem;
	}
	.ov-status-dot {
		width: 0.5rem;
		height: 0.5rem;
		flex-shrink: 0;
		border-radius: 9999px;
		background: var(--muted-foreground);
	}
	.ov-status[data-tone='info'] .ov-status-dot {
		background: var(--info);
	}
	.ov-status[data-tone='good'] .ov-status-dot {
		background: var(--success);
	}
	.ov-status[data-tone='warn'] {
		border-color: color-mix(in oklab, var(--warning) 40%, var(--border));
		background: color-mix(in oklab, var(--warning) 7%, var(--card));
	}
	.ov-status[data-tone='warn'] .ov-status-dot {
		background: var(--warning);
	}
	.ov-next,
	.ov-link {
		display: inline-flex;
		flex-shrink: 0;
		cursor: pointer;
		align-items: center;
		gap: 0.25rem;
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--primary);
	}
	.ov-next:hover,
	.ov-link:hover {
		text-decoration: underline;
	}
	.ov-figs {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		overflow: hidden;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
	}
	.ov-fig {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding: 0.875rem 1.125rem;
		border-right: 1px solid var(--border);
	}
	.ov-fig:last-child {
		border-right: 0;
	}
	.ov-fig dt {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.ov-val {
		font-family: var(--font-mono);
		font-size: 1.5rem;
		font-weight: 500;
		line-height: 1.1;
		font-variant-numeric: tabular-nums;
	}
	.ov-of {
		font-size: 0.9375rem;
		color: var(--muted-foreground);
	}
	.ov-bar {
		height: 4px;
		max-width: 9rem;
		overflow: hidden;
		border-radius: 9999px;
		background: var(--muted);
	}
	.ov-bar > span {
		display: block;
		height: 100%;
		background: var(--muted-foreground);
	}
	.ov-bar > span[data-tone='info'] {
		background: var(--primary);
	}
	.ov-bar > span[data-tone='good'] {
		background: var(--success);
	}
	.ov-bar > span[data-tone='warn'] {
		background: var(--warning);
	}
	.ov-sub {
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.ov-card {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
	}
	.ov-card-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		padding: 0.875rem 1rem 0.5rem;
	}
	.ov-card-head .ov-link {
		margin-left: auto;
	}
	.ov-h {
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 600;
	}
	.ov-board {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding: 0.25rem 1rem 1rem;
	}
	.ov-row {
		display: grid;
		grid-template-columns: minmax(10rem, 22rem) minmax(0, 1fr) 6.5rem 9rem;
		align-items: center;
		gap: 0.75rem;
		font-size: 0.8125rem;
	}
	.ov-label {
		display: flex;
		min-width: 0;
		align-items: center;
		gap: 0.375rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.ov-base {
		flex-shrink: 0;
		border-radius: 9999px;
		border: 1px solid var(--border);
		padding: 0 0.375rem;
		font-size: 0.625rem;
		color: var(--muted-foreground);
	}
	.ov-track {
		position: relative;
		height: 0.75rem;
		border-radius: 0.1875rem;
		background: var(--muted);
	}
	.ov-fill {
		position: absolute;
		inset: 0 auto 0 0;
		border-radius: 0.1875rem;
		background: color-mix(in oklab, var(--primary) 55%, transparent);
	}
	.ov-ci {
		position: absolute;
		top: 50%;
		height: 2px;
		transform: translateY(-50%);
		background: var(--foreground);
		opacity: 0.7;
	}
	.ov-ci::before,
	.ov-ci::after {
		content: '';
		position: absolute;
		top: -4px;
		width: 1.5px;
		height: 10px;
		background: inherit;
	}
	.ov-ci::before {
		left: 0;
	}
	.ov-ci::after {
		right: 0;
	}
	.ov-num {
		text-align: right;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
	.ov-n {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		white-space: nowrap;
	}
	.ov-about {
		display: flex;
		flex-direction: column;
		gap: 0.625rem;
		padding: 0.875rem 1rem 1rem;
	}
	@media (max-width: 760px) {
		.ov-row {
			grid-template-columns: minmax(0, 1fr) 5.5rem;
		}
		.ov-track {
			grid-column: 1 / -1;
			order: 3;
		}
		.ov-n {
			display: none;
		}
	}
</style>
