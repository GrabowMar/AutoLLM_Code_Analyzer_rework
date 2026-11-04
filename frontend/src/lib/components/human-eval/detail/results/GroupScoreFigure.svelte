<script lang="ts">
	/**
	 * GroupScoreFigure — a numbered figure of per-group means (mean of per-app
	 * values) with bootstrap-over-apps 95% intervals; the floor sensitivity
	 * (unevaluable apps at the scale minimum / 0) is drawn as a hollow ring.
	 *
	 * Props: `id`, `number`, `title`, `scores` (GroupScore[]), `domain`,
	 * `tickKind`, `format`, `groupNoun`, `primary` (mark as the pre-registered
	 * primary outcome), `note` (caption text), `children` (extra content under the plot).
	 */
	import type { Snippet } from 'svelte';
	import StudyFigure from '$lib/components/studies/study/StudyFigure.svelte';
	import type { GroupScore } from '$lib/api/human-eval';
	import { plural, type Domain } from '../../format';
	import DotCiPlot from './DotCiPlot.svelte';
	import type { PlotRow } from './types';

	interface Props {
		id: string;
		number: string;
		title: string;
		scores: GroupScore[];
		domain: Domain;
		tickKind?: 'score' | 'share' | 'log';
		format: (v: number) => string;
		groupNoun: string;
		primary?: boolean;
		note?: string;
		children?: Snippet;
	}

	let { id, number, title, scores, domain, tickKind = 'score', format, groupNoun, primary = false, note = '', children }: Props = $props();

	const rows = $derived<PlotRow[]>(
		scores.map((s) => ({
			key: s.group,
			label: s.label,
			value: s.mean,
			ci: s.ci,
			secondary: s.floor?.mean ?? null,
			n: `${plural(s.n_apps, 'app')} · ${s.n_ratings}`,
		}))
	);
	const hasFloor = $derived(scores.some((s) => s.floor?.mean != null));
</script>

<StudyFigure {id} {number} {title} flush>
	{#snippet controls()}
		{#if primary}<span class="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">Primary outcome</span>{/if}
	{/snippet}
	{#if rows.length === 0}
		<p class="px-4 py-3 text-sm text-muted-foreground">No submitted data yet.</p>
	{:else}
		<DotCiPlot
			{rows}
			{domain}
			{tickKind}
			{format}
			labelHeader={groupNoun}
			nHeader="Apps · ratings"
			secondaryLabel={hasFloor ? 'Unevaluable apps at the floor (sensitivity)' : ''}
		/>
	{/if}
	{#if children}<div class="space-y-3 border-t border-border/60 p-4">{@render children()}</div>{/if}
	{#snippet caption()}
		Dot: mean of per-app means (apps are the unit of analysis). Whisker: 95% percentile bootstrap over apps (2000 draws, seeded), shown
		from 3 apps.{#if hasFloor}{' '}Ring: the same with unevaluable apps counted at the floor.{/if}
		{#if note}{' '}{note}{/if}
	{/snippet}
</StudyFigure>
