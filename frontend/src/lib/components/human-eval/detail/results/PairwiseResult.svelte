<script lang="ts">
	/**
	 * PairwiseResult — A/B preferences per criterion (and overall):
	 * Bradley–Terry strengths per group with cluster-bootstrap 95% intervals on
	 * the log scale (plotted on a log axis, 1 = average), one plot per
	 * connected comparison component (strengths only compare within one);
	 * per-app strengths in a table; win / tie / loss counts; position bias
	 * (preference for the left side; p only when not provisional); agreement.
	 *
	 * Props: `result` (PairwiseBlockResult), `section`, `ctx`.
	 */
	import StudyFigure from '$lib/components/experiments/study/StudyFigure.svelte';
	import type { BtEntry, PairwiseBlockResult } from '$lib/api/human-eval';
	import { formatP, plural, symmetricDomain } from '../../format';
	import AgreementLine from './AgreementLine.svelte';
	import DotCiPlot from './DotCiPlot.svelte';
	import type { PlotRow, ResultsContext } from './types';

	let { result, section, ctx }: { result: PairwiseBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = (metric: string) => ctx.primary?.block_id === result.block_id && ctx.primary.metric === metric;
	const strength = (logValue: number) => {
		const s = Math.exp(logValue);
		return s >= 10 ? s.toFixed(0) : s.toFixed(2);
	};
	const tick = (logValue: number) => {
		const s = Math.exp(logValue);
		return s >= 10 ? s.toFixed(0) : s >= 0.995 && s <= 1.005 ? '1' : s.toFixed(s < 1 ? 2 : 1);
	};

	function byComponent(entries: BtEntry[]): [string, BtEntry[]][] {
		const map = new Map<string, BtEntry[]>();
		for (const e of entries) {
			const list = map.get(e.component) ?? [];
			list.push(e);
			map.set(e.component, list);
		}
		return [...map.entries()].map(([c, list]) => [c, [...list].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9))]);
	}

	function rowsOf(entries: BtEntry[]): PlotRow[] {
		return entries.map((e) => ({
			key: e.key,
			label: e.label,
			value: e.log_strength,
			ci: e.ci,
			rank: e.rank,
			n: `${e.wins}–${e.ties}–${e.losses} · ${e.comparisons}`,
		}));
	}

	function domainOf(entries: BtEntry[]) {
		return symmetricDomain(entries.flatMap((e) => [e.log_strength, e.ci?.low, e.ci?.high]));
	}
</script>

<div class="space-y-4">
	{#each result.criteria as c, i (c.criterion_id)}
		{@const groups = byComponent(c.by_group)}
		{@const apps = byComponent(c.by_app)}
		<StudyFigure id="fig-{result.block_id}-{c.criterion_id}" number="{section}.{i + 1}" title="{c.criterion_id === 'overall' ? 'Overall preference' : c.label}: Bradley–Terry strength by {ctx.groupNoun.toLowerCase()}" flush>
			{#snippet controls()}
				{#if isPrimary(c.criterion_id)}<span class="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">Primary outcome</span>{/if}
				<span class="font-mono text-xs text-muted-foreground">{plural(c.n_comparisons, 'comparison')}</span>
			{/snippet}
			{#if c.by_group.length === 0}
				<p class="px-4 py-3 text-sm text-muted-foreground">No comparisons between {ctx.groupNoun.toLowerCase()}s yet.</p>
			{:else}
				{#each groups as [component, entries] (component)}
					{#if groups.length > 1}
						<p class="border-b border-border/60 px-4 py-1.5 text-xs text-muted-foreground">Component {component} — strengths compare only within it</p>
					{/if}
					<DotCiPlot
						rows={rowsOf(entries)}
						domain={domainOf(entries)}
						tickFormat={tick}
						format={strength}
						reference={0}
						ranked
						labelHeader={ctx.groupNoun}
						estimateHeader="Strength [95% CI]"
						nHeader="W–T–L · n"
					/>
				{/each}
			{/if}
			<div class="space-y-3 border-t border-border/60 p-4">
				<p class="text-xs text-muted-foreground">
					<span class="font-medium text-foreground">Position bias</span> · left side preferred {c.position_bias.left}×, right
					{c.position_bias.right}×, ties {c.position_bias.ties}
					{#if c.position_bias.p_value != null}
						· exact binomial p = <span class="font-mono text-foreground">{formatP(c.position_bias.p_value)}</span>
					{:else if ctx.pWithheld}
						· p withheld while provisional
					{/if}
				</p>
				<AgreementLine agreement={c.agreement} unit="items" />
				{#if c.by_app.length > 0}
					<details>
						<summary class="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Per app ({c.by_app.length})</summary>
						<div class="mt-2 max-h-96 overflow-auto">
							<table class="text-sm">
								<thead>
									<tr><th class="num">#</th><th>App</th>{#if apps.length > 1}<th>Component</th>{/if}<th class="num">Strength [95% CI]</th><th class="num">W–T–L</th><th class="num">Comparisons</th></tr>
								</thead>
								<tbody>
									{#each apps as [component, entries] (component)}
										{#each entries as e (e.key)}
											<tr>
												<td class="num text-xs text-muted-foreground">{e.rank ?? '—'}</td>
												<td class="font-mono text-xs">{e.label}</td>
												{#if apps.length > 1}<td class="text-xs text-muted-foreground">{component}</td>{/if}
												<td class="num text-xs">
													<span class="font-medium">{e.strength != null ? e.strength.toFixed(2) : '—'}</span>
													{#if e.ci?.low != null && e.ci?.high != null}<span class="text-muted-foreground">[{strength(e.ci.low)}, {strength(e.ci.high)}]</span>{/if}
												</td>
												<td class="num text-xs">{e.wins}–{e.ties}–{e.losses}</td>
												<td class="num text-xs text-muted-foreground">{e.comparisons}</td>
											</tr>
										{/each}
									{/each}
								</tbody>
							</table>
						</div>
					</details>
				{/if}
			</div>
			{#snippet caption()}
				Bradley–Terry strengths (geometric mean 1; a prior of half a win and half a loss against a fixed virtual opponent), ties count
				half; axis on the log scale, hairline at 1. 95% interval: cluster bootstrap (items for apps; apps within groups for {ctx.groupNoun.toLowerCase()}s).
				W–T–L: wins, ties, losses.
			{/snippet}
		</StudyFigure>
	{/each}
</div>
