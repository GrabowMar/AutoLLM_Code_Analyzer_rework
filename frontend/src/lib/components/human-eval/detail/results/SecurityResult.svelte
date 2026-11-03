<script lang="ts">
	/**
	 * SecurityResult — a security block:
	 * - checklist: per item and group the vulnerable share (mean over apps of
	 *   vulnerable / (vulnerable + not vulnerable), bootstrap CI over apps),
	 *   rater-level counts, agreement per item;
	 * - vulnerability reports by the TESTERS' own severities, per group and app;
	 * - finding review: precision overall, per tool, per the tool's own level
	 *   ("unrated" when it printed none — never a normalized severity) and per
	 *   group, with agreement (units = findings);
	 * - HTTP console use.
	 *
	 * Props: `result` (SecurityBlockResult), `section`, `ctx`.
	 */
	import StudyFigure from '$lib/components/studies/study/StudyFigure.svelte';
	import { severityColors } from '$lib/constants/colors';
	import type { SecurityBlockResult } from '$lib/api/human-eval';
	import { cn } from '$lib/utils';
	import { formatCi, formatScore, formatShare, humanize } from '../../format';
	import AgreementLine from './AgreementLine.svelte';
	import PrecisionTable from './PrecisionTable.svelte';
	import type { ResultsContext } from './types';

	let { result, section, ctx }: { result: SecurityBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = (metric: string) => ctx.primary?.block_id === result.block_id && ctx.primary.metric === metric;
	const share = (v: number) => formatShare(v);
	const two = (v: number) => formatScore(v, 2);
	const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'];
	function severityEntries(counts: Record<string, number>): [string, number][] {
		return Object.entries(counts ?? {})
			.filter(([, n]) => n > 0)
			.sort((a, b) => {
				const ia = SEVERITY_ORDER.indexOf(a[0]);
				const ib = SEVERITY_ORDER.indexOf(b[0]);
				return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
			});
	}
	const fr = $derived(result.finding_review);
	const numbers = $derived.by(() => {
		let k = 0;
		const next = () => `${section}.${++k}`;
		return {
			checklist: result.checklist.length ? next() : '',
			reports: next(),
			findings: fr ? next() : '',
			console: result.console ? next() : '',
		};
	});
</script>

{#snippet severities(counts: Record<string, number>)}
	{@const entries = severityEntries(counts)}
	{#if entries.length === 0}
		<span class="text-muted-foreground">—</span>
	{:else}
		<span class="flex flex-wrap justify-end gap-1">
			{#each entries as [sev, count] (sev)}
				<span class={cn('rounded-full border px-1.5 py-px text-[11px]', severityColors[sev] ?? 'border-border')} title="Tester's severity">{humanize(sev)} {count}</span>
			{/each}
		</span>
	{/if}
{/snippet}

<div class="space-y-4">
	{#if result.checklist.length > 0}
		<StudyFigure id="tab-{result.block_id}-checklist" kind="table" number={numbers.checklist} title="Security checklist: share of apps found vulnerable" flush>
			{#snippet controls()}
				{#if isPrimary('vulnerable_share')}<span class="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">Primary outcome</span>{/if}
			{/snippet}
			<div class="max-h-[32rem] overflow-auto">
				<table class="text-sm">
					<thead>
						<tr>
							<th>Item</th>
							<th>{ctx.groupNoun}</th>
							<th class="num" title="Mean over apps of vulnerable / (vulnerable + not vulnerable) [bootstrap 95% CI over apps]">Vulnerable share [95% CI]</th>
							<th class="num">Apps</th>
							<th class="num" title="Rater-level counts: vulnerable / not vulnerable / not tested">V · NV · not tested</th>
						</tr>
					</thead>
					<tbody>
						{#each result.checklist as item (item.item_id)}
							{#each item.per_group as g, gi (g.group)}
								<tr class={cn(gi === 0 && 'border-t-2 border-t-border')}>
									{#if gi === 0}
										<td rowspan={item.per_group.length + 1} class="max-w-60 align-top">
											<span class="break-words font-medium">{item.label}</span>
											{#if item.category}<span class="block text-[11px] text-muted-foreground">{item.category}</span>{/if}
										</td>
									{/if}
									<td class="max-w-48 truncate text-xs" title={g.label}>{g.label}</td>
									<td class="num text-xs"><span class="font-medium">{formatShare(g.vulnerable_share)}</span> <span class="text-muted-foreground">{formatCi(g.ci, share)}</span></td>
									<td class="num text-xs">{g.n_apps}</td>
									<td class="num text-xs text-muted-foreground">{g.vulnerable} · {g.not_vulnerable} · {g.not_tested}</td>
								</tr>
							{/each}
							<tr>
								{#if item.per_group.length === 0}<td class="max-w-60 font-medium">{item.label}</td>{/if}
								<td colspan="4" class="py-1"><AgreementLine agreement={item.agreement} unit="apps" /></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#snippet caption()}Not-tested outcomes are left out of the share. Counts are rater-level and descriptive; the share and its interval treat apps as the unit.{/snippet}
		</StudyFigure>
	{/if}

	<StudyFigure id="tab-{result.block_id}-reports" kind="table" number={numbers.reports} title="Vulnerability reports (testers’ own severities)" flush>
		<div class="overflow-x-auto">
			<table class="text-sm">
				<thead>
					<tr>
						<th>{ctx.groupNoun}</th>
						<th class="num">Apps</th>
						<th class="num">Reports</th>
						<th class="num" title="Mean over apps of reports per submitted assignment [bootstrap 95% CI]">Per rater [95% CI]</th>
						<th class="text-right">By tester severity</th>
					</tr>
				</thead>
				<tbody>
					{#each result.reports.per_group as g (g.group)}
						<tr>
							<td class="max-w-56 truncate" title={g.label}>{g.label}</td>
							<td class="num text-xs">{g.n_apps}</td>
							<td class="num text-xs">{g.total}</td>
							<td class="num text-xs"><span class="font-medium">{formatScore(g.per_rater_mean)}</span> <span class="text-muted-foreground">{formatCi(g.ci, two)}</span></td>
							<td class="text-right text-xs">{@render severities(g.by_severity)}</td>
						</tr>
					{:else}
						<tr><td colspan="5" class="text-xs text-muted-foreground">No reports.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if result.reports.per_app.length > 0}
			<details class="border-t border-border/60">
				<summary class="cursor-pointer px-4 py-2 text-xs font-medium text-muted-foreground hover:text-foreground">Per app ({result.reports.per_app.length})</summary>
				<div class="max-h-80 overflow-auto">
					<table class="text-sm">
						<thead><tr><th>App</th><th>{ctx.groupNoun}</th><th class="num">Reports</th><th class="text-right">By tester severity</th><th>Categories</th></tr></thead>
						<tbody>
							{#each result.reports.per_app as a (a.app_id)}
								<tr>
									<td class="font-mono text-xs">{a.label}</td>
									<td class="max-w-48 truncate text-xs">{ctx.groupLabel(a.group)}</td>
									<td class="num text-xs">{a.total}</td>
									<td class="text-right text-xs">{@render severities(a.by_severity)}</td>
									<td class="text-[11px] text-muted-foreground">{Object.entries(a.by_category ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</details>
		{/if}
	</StudyFigure>

	{#if fr}
		<StudyFigure id="tab-{result.block_id}-findings" kind="table" number={numbers.findings} title="Precision of automated findings (human TP / FP review)" flush>
			<div class="space-y-4 p-4">
				<p class="text-xs text-muted-foreground">
					{fr.n_findings} findings reviewed, {fr.n_verdicts} verdicts. Overall precision
					<span class="font-mono text-foreground">{formatShare(fr.overall.precision)}</span>
					<span class="font-mono">{formatCi(fr.overall.ci, share)}</span>{fr.overall.weighted ? ' (weighted)' : ''}; majority vote
					<span class="font-mono">{formatShare(fr.overall.majority_precision)}</span>.
				</p>
				<AgreementLine agreement={fr.agreement} unit="findings" />
				<div class="space-y-1">
					<p class="section-label">Per tool</p>
					<PrecisionTable rows={fr.per_tool} keyHeader="Tool" />
				</div>
				<div class="space-y-1">
					<p class="section-label">Per level, as the tool reported it</p>
					<PrecisionTable rows={fr.per_tool_severity} keyHeader="Tool’s own level" levels />
				</div>
				<div class="space-y-1">
					<p class="section-label">Per {ctx.groupNoun.toLowerCase()}</p>
					<PrecisionTable rows={fr.per_group} keyHeader={ctx.groupNoun} />
				</div>
			</div>
			{#snippet caption()}
				Precision = tp / (tp + fp) per finding from the raters’ votes (unsure left out), averaged with inverse-probability weights from each
				app’s sampling frame (per tool unweighted); 95% CI by bootstrap over apps. Vote counts are descriptive. Levels are the tools’ own labels.
			{/snippet}
		</StudyFigure>
	{/if}

	{#if result.console}
		<StudyFigure id="tab-{result.block_id}-console" kind="table" number={numbers.console} title="HTTP console use" flush>
			<div class="space-y-2 p-4 text-sm">
				<p>{result.console.requests} requests from {result.console.participants_using} participants.</p>
				{#if result.console.per_app.length}
					<p class="text-xs text-muted-foreground">{result.console.per_app.map((a) => `${a.label}: ${a.requests}`).join(' · ')}</p>
				{/if}
			</div>
		</StudyFigure>
	{/if}
</div>
