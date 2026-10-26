<script lang="ts">
	/**
	 * Leaderboard — conditions ranked by mean pass rate (app-level means,
	 * cluster-bootstrap 95% CI), with the paired Δ against the baseline, what
	 * is missing and why, timing (only comparable within one endpoint), tokens,
	 * cost, and validity / termination mixes as small stacked bars.
	 *
	 * Props:
	 * - `results`: `GET /results/`.
	 * - `sort` (bindable): 'rank' | 'delta' | 'time' (time sorts within each endpoint).
	 */
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import { tableFrame } from '$lib/components/table';
	import { statusTone, type Tone } from '$lib/constants/colors';
	import { formatUsd } from '$lib/components/generator/batches/batch-utils';
	import { formatTokens } from '$lib/utils/formatters';
	import type { ExperimentResults, LeaderboardRow } from '$lib/api/types/experiments';
	import { formatDeltaPp, formatPercent } from './format';

	interface Props {
		results: ExperimentResults;
		sort?: 'rank' | 'delta' | 'time';
	}

	let { results, sort = $bindable('rank') }: Props = $props();

	const paired = $derived(new Map(results.paired.rows.map((r) => [r.condition_id, r])));
	const baselineId = $derived(results.baseline_condition_id);

	const rows = $derived.by(() => {
		const list = [...results.leaderboard];
		if (sort === 'delta') {
			list.sort(
				(a, b) =>
					(paired.get(b.condition_id)?.mean_delta ?? -Infinity) - (paired.get(a.condition_id)?.mean_delta ?? -Infinity) ||
					a.rank - b.rank
			);
		} else if (sort === 'time') {
			// Timings confound model and machine: compare only within one endpoint.
			list.sort(
				(a, b) =>
					(a.timing_endpoint_slug ?? '').localeCompare(b.timing_endpoint_slug ?? '') ||
					(a.median_seconds ?? Infinity) - (b.median_seconds ?? Infinity)
			);
		} else {
			list.sort((a, b) => a.rank - b.rank);
		}
		return list;
	});

	const BAR: Record<Tone, string> = {
		success: 'bg-success',
		warning: 'bg-warning',
		danger: 'bg-destructive',
		info: 'bg-info',
		neutral: 'bg-muted-foreground/40',
	};

	function missingTotal(row: LeaderboardRow): number {
		const m = row.n_missing;
		return m.failed + m.ungraded + m.excluded_invalid + m.excluded_truncated + m.not_common;
	}

	function missingTitle(row: LeaderboardRow): string {
		const m = row.n_missing;
		return [
			m.failed ? `${m.failed} run failed` : '',
			m.ungraded ? `${m.ungraded} not graded` : '',
			m.excluded_invalid ? `${m.excluded_invalid} excluded as invalid` : '',
			m.excluded_truncated ? `${m.excluded_truncated} excluded as truncated` : '',
			m.not_common ? `${m.not_common} outside the common cells` : '',
		]
			.filter(Boolean)
			.join(' · ') || 'Nothing missing';
	}

	function mix(entries: [string, number][], tone: (k: string) => Tone): { key: string; n: number; cls: string }[] {
		return entries.filter(([, n]) => n > 0).map(([key, n]) => ({ key, n, cls: BAR[tone(key)] }));
	}

	function terminationTone(key: string): Tone {
		if (key === 'completed' || key === 'success') return 'success';
		return statusTone(key);
	}

	const VALIDITY_TONES: Record<string, Tone> = { ok: 'success', warn: 'warning', invalid: 'danger' };
</script>

{#snippet stacked(parts: { key: string; n: number; cls: string }[], label: string)}
	{@const total = parts.reduce((s, p) => s + p.n, 0)}
	{#if total > 0}
		<span
			class="flex h-1.5 w-16 overflow-hidden rounded-full bg-muted"
			role="img"
			aria-label="{label}: {parts.map((p) => `${p.n} ${p.key.replace(/_/g, ' ')}`).join(', ')}"
			title={parts.map((p) => `${p.key.replace(/_/g, ' ')}: ${p.n}`).join('\n')}
		>
			{#each parts as part (part.key)}
				<span class={part.cls} style="width: {(part.n / total) * 100}%"></span>
			{/each}
		</span>
	{:else}
		<span class="text-muted-foreground">—</span>
	{/if}
{/snippet}

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Content class="p-0">
		<div class="table-frame" {@attach tableFrame}>
			<table class="min-w-[56rem]">
				<thead>
					<tr>
						<th scope="col" class="num w-10">#</th>
						<th scope="col" class="col-sticky">Condition</th>
						<th scope="col" class="num" title="Apps × repeats per app">Apps × R</th>
						<th scope="col" class="num" title="Cells that passed every check / graded cells">k/n</th>
						<th scope="col" class="whitespace-nowrap" title="Mean of app-level pass rates, with a cluster-bootstrap 95% interval over apps">Pass rate (95% CI)</th>
						<th scope="col" class="num" title="Paired mean difference vs the baseline (app-level)">Δ vs baseline</th>
						<th scope="col" class="num" title="Cells without a usable grade">Missing</th>
						<th scope="col" class="num" title="Median run time; only comparable within one endpoint">Median time</th>
						<th scope="col" class="num">Tokens</th>
						<th scope="col" class="num">Cost</th>
						<th scope="col">Validity</th>
						<th scope="col">Termination</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (row.condition_id)}
						{@const p = paired.get(row.condition_id)}
						{@const isBaseline = row.condition_id === baselineId}
						{@const missing = missingTotal(row)}
						<tr class={cn('group', isBaseline && 'bg-primary/5')}>
							<td class="num text-xs text-muted-foreground">{row.rank}</td>
							<th
								scope="row"
								class={cn(
									'col-sticky top-auto z-[1] min-w-52 max-w-72 border-b-border/60 text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground backdrop-blur-none',
									isBaseline ? 'bg-[color-mix(in_oklab,var(--primary)_5%,var(--card))]' : 'bg-card group-hover:bg-[color-mix(in_oklab,var(--primary)_5%,var(--card))]'
								)}
							>
								<span class="flex min-w-0 items-center gap-1.5">
									<span class="truncate font-medium" title={row.label}>{row.label}</span>
									{#if isBaseline}
										<span class="shrink-0 rounded-full border border-primary/40 bg-primary/10 px-1.5 text-[11px] text-primary">baseline</span>
									{/if}
									{#if row.mixed_fingerprints}
										<span
											class="shrink-0 rounded-full border border-warning/30 bg-warning/10 px-1.5 text-[11px] text-warning-fg"
											title="Runs span {row.mixed_fingerprints.count} configuration fingerprints (majority {formatPercent(row.mixed_fingerprints.majority_share)}). Filter with “Majority fingerprint only”."
										>mixed fingerprints</span>
									{/if}
								</span>
								<span class="cell-sub block truncate font-mono text-[11px] text-muted-foreground">{row.model.endpoint_slug ?? 'openrouter'} · {row.harness}</span>
							</th>
							<td class="num text-xs">{row.n_apps}×{row.repeats_per_app}</td>
							<td class="num text-xs">{row.k_passed}/{row.n_graded}</td>
							<td>
								<span class="flex items-center gap-2">
									<span class="w-10 text-right font-mono text-xs font-semibold tabular-nums">{formatPercent(row.pass_rate_mean)}</span>
									<span class="relative h-2 w-24 rounded-full bg-muted" aria-hidden="true">
										{#if row.pass_rate_ci95}
											<span
												class="absolute inset-y-0 rounded-full bg-foreground/25"
												style="left: {row.pass_rate_ci95[0] * 100}%; width: {Math.max(1, (row.pass_rate_ci95[1] - row.pass_rate_ci95[0]) * 100)}%"
											></span>
										{/if}
										{#if row.pass_rate_mean != null}
											<span class="absolute inset-y-[-2px] w-0.5 rounded bg-foreground" style="left: calc({row.pass_rate_mean * 100}% - 1px)"></span>
										{/if}
									</span>
									{#if row.pass_rate_ci95}
										<span class="whitespace-nowrap font-mono text-[11px] tabular-nums text-muted-foreground">
											{formatPercent(row.pass_rate_ci95[0])}–{formatPercent(row.pass_rate_ci95[1])}
										</span>
									{/if}
								</span>
							</td>
							<td class="num text-xs">
								{#if isBaseline}
									<span class="text-muted-foreground" title="This is the baseline">—</span>
								{:else if p?.mean_delta != null}
									<span class={p.mean_delta > 0 ? 'text-success-fg' : p.mean_delta < 0 ? 'text-danger-fg' : ''}>{formatDeltaPp(p.mean_delta)}</span>
								{:else}
									<span class="text-muted-foreground">—</span>
								{/if}
							</td>
							<td class="num text-xs" title={missingTitle(row)}>
								<span class={missing > 0 ? 'text-warning-fg' : 'text-muted-foreground'}>{missing}</span>
							</td>
							<td class="num text-xs" title={row.timing_endpoint_slug ? `Measured on ${row.timing_endpoint_slug}` : ''}>
								{#if row.median_seconds != null}{Math.round(row.median_seconds)} s{:else}<span class="text-muted-foreground">—</span>{/if}
								{#if row.timing_endpoint_slug}<span class="cell-sub block text-[11px] text-muted-foreground">{row.timing_endpoint_slug}</span>{/if}
							</td>
							<td class="num text-xs">{#if row.tokens_total}{formatTokens(row.tokens_total)}{:else}<span class="text-muted-foreground">—</span>{/if}</td>
							<td class="num text-xs">{#if row.cost_usd > 0}{formatUsd(row.cost_usd)}{:else}<span class="text-muted-foreground">—</span>{/if}</td>
							<td>
								{@render stacked(
									mix(
										Object.entries(row.validity) as [string, number][],
										(k) => VALIDITY_TONES[k] ?? 'neutral'
									),
									'Validity'
								)}
							</td>
							<td>{@render stacked(mix(Object.entries(row.terminations), terminationTone), 'Termination')}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
