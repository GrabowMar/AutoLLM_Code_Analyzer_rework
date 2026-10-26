<script lang="ts">
	/**
	 * TargetTable — candidate targets (generation jobs) with identity, sample
	 * size and eligibility reasons. Used by the target picker's live resolve
	 * and by the preflight report.
	 *
	 * Props: `targets` (resolve or preflight rows), `showGroup` (group/block
	 * columns), `showCost` (AI cost estimate column), `empty` (message).
	 */
	import type { PreflightTarget, TargetDescription } from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import NaValue from '../NaValue.svelte';
	import TargetReasons from './TargetReasons.svelte';
	import { fmtInt, fmtUsd, shortHash } from './format';

	interface Props {
		targets: (TargetDescription | PreflightTarget)[];
		showGroup?: boolean;
		showCost?: boolean;
		empty?: string;
	}

	let {
		targets,
		showGroup = false,
		showCost = false,
		empty = 'No completed job matches these targets.'
	}: Props = $props();

	function variant(t: TargetDescription): string {
		const m = t.meta;
		const parts = [m.condition_label, m.harness ?? m.mode].filter(Boolean);
		return parts.join(' · ');
	}

	function costOf(t: TargetDescription | PreflightTarget): number | null {
		const est = (t as PreflightTarget).ai_cost_estimate;
		return est && 'expected' in est && typeof est.expected === 'number' ? est.expected : null;
	}
</script>

{#if targets.length === 0}
	<EmptyState compact title="No targets" description={empty} />
{:else}
	<div class="table-scroll-wrapper">
		<table class="table-card-mobile">
			<thead>
				<tr>
					<th scope="col">Target</th>
					<th scope="col">Model</th>
					<th scope="col">Condition / harness</th>
					<th scope="col">App</th>
					<th scope="col">Stack</th>
					{#if showGroup}<th scope="col">Group · block</th>{/if}
					<th scope="col" class="text-right">Files</th>
					<th scope="col" class="text-right">Measured SLOC</th>
					{#if showCost}<th scope="col" class="text-right">AI cost (exp.)</th>{/if}
					<th scope="col">Eligibility</th>
				</tr>
			</thead>
			<tbody>
				{#each targets as t, i (t.job_id ?? `inline-${i}`)}
					<tr class={cn(!t.eligible && 'bg-destructive/5')}>
						<td data-label="Target" class="max-w-[14rem]">
							{#if t.job_id}
								<a
									href="/applications/{t.job_id}"
									class="font-mono text-xs text-foreground hover:text-primary hover:underline"
									title="Open application {t.job_id}"
								>
									{shortHash(t.job_id, 8)}
								</a>
							{:else}
								<span class="text-xs text-muted-foreground">inline</span>
							{/if}
							{#if t.meta.repeat_index !== null && t.meta.repeat_index !== undefined}
								<span class="ml-1 text-[11px] text-muted-foreground">r{t.meta.repeat_index}</span>
							{/if}
						</td>
						<td data-label="Model" class="max-w-[14rem]">
							<span class="block truncate" title={t.meta.model_name ?? t.meta.model_slug ?? ''}>
								{t.meta.model_slug ?? t.meta.model_name ?? '—'}
							</span>
						</td>
						<td data-label="Condition" class="text-muted-foreground">{variant(t) || '—'}</td>
						<td data-label="App">{t.meta.app_name ?? t.meta.app_slug ?? '—'}</td>
						<td data-label="Stack" class="whitespace-nowrap text-muted-foreground"
							>{t.meta.stack_slug ?? '—'}</td
						>
						{#if showGroup}
							{@const pt = t as PreflightTarget}
							<td data-label="Group" class="text-xs">
								<span class="block">{pt.group || '—'}</span>
								{#if pt.block}<span class="block text-muted-foreground">{pt.block}</span>{/if}
							</td>
						{/if}
						<td data-label="Files" class="md:text-right tabular-nums">
							{t.files}<span class="text-muted-foreground">/{t.files_total}</span>
						</td>
						<td data-label="Measured SLOC" class="md:text-right tabular-nums">
							{fmtInt(t.sloc_measured)}
							{#if t.sloc_total !== t.sloc_measured}
								<span class="text-[11px] text-muted-foreground">of {fmtInt(t.sloc_total)}</span>
							{/if}
						</td>
						{#if showCost}
							{@const cost = costOf(t)}
							<td data-label="AI cost" class="md:text-right tabular-nums">
								{#if cost === null}
									<NaValue
										reason={t.eligible
											? 'No AI review estimate for this target (no files for the reviewer).'
											: 'Excluded targets are not reviewed.'}
									/>
								{:else}
									{fmtUsd(cost)}
								{/if}
							</td>
						{/if}
						<td data-label="Eligibility">
							<TargetReasons eligible={t.eligible} reasons={t.reasons} warnings={t.warnings} />
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
