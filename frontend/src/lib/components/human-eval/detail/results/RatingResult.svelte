<script lang="ts">
	/**
	 * RatingResult — a rating block: the composite (mean of criteria rescaled
	 * to 0..1) per group and per app, then every criterion on its own scale
	 * per group, with inter-rater agreement (units = apps) and the per-app table.
	 *
	 * Props: `result` (RatingBlockResult), `section` (number prefix), `ctx`.
	 */
	import type { RatingBlockResult } from '$lib/api/human-eval';
	import { formatScore } from '../../format';
	import AgreementLine from './AgreementLine.svelte';
	import AppScoreTable from './AppScoreTable.svelte';
	import GroupScoreFigure from './GroupScoreFigure.svelte';
	import type { ResultsContext } from './types';

	let { result, section, ctx }: { result: RatingBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = (metric: string) => ctx.primary?.block_id === result.block_id && ctx.primary.metric === metric;
	const two = (v: number) => formatScore(v, 2);
</script>

<div class="space-y-4">
	<GroupScoreFigure
		id="fig-{result.block_id}-composite"
		number="{section}.1"
		title="Composite rating by {ctx.groupNoun.toLowerCase()} (0–1)"
		scores={result.composite.per_group}
		domain={[0, 1]}
		format={two}
		groupNoun={ctx.groupNoun}
		primary={isPrimary('composite')}
		note="Composite: mean of the criteria, each rescaled to 0–1 (N/A ignored)."
	>
		<details>
			<summary class="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Per app ({result.composite.per_app.length})</summary>
			<div class="mt-2">
				<AppScoreTable scores={result.composite.per_app} groupLabel={ctx.groupLabel} groupNoun={ctx.groupNoun} format={two} naColumn={false} />
			</div>
		</details>
	</GroupScoreFigure>

	{#each result.criteria as c, i (c.criterion_id)}
		<GroupScoreFigure
			id="fig-{result.block_id}-{c.criterion_id}"
			number="{section}.{i + 2}"
			title="{c.label} ({c.scale.min}–{c.scale.max})"
			scores={c.per_group}
			domain={[c.scale.min, c.scale.max]}
			format={two}
			groupNoun={ctx.groupNoun}
			primary={isPrimary(c.criterion_id)}
			note={c.scale.min_label || c.scale.max_label ? `Scale: ${c.scale.min} = ${c.scale.min_label || '…'}, ${c.scale.max} = ${c.scale.max_label || '…'}.` : ''}
		>
			<AgreementLine agreement={c.agreement} unit="apps" />
			<details>
				<summary class="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Per app ({c.per_app.length})</summary>
				<div class="mt-2">
					<AppScoreTable scores={c.per_app} groupLabel={ctx.groupLabel} groupNoun={ctx.groupNoun} format={two} />
				</div>
			</details>
		</GroupScoreFigure>
	{/each}
</div>
