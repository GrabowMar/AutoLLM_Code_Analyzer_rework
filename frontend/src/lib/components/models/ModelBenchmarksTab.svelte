<script lang="ts">
	/**
	 * Benchmarks tab: external scores — Artificial Analysis indices and Design
	 * Arena (both embedded in the OpenRouter catalog payload) with catalog
	 * percentiles, plus benchmark rows seeded into the rankings.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Trophy from '@lucide/svelte/icons/trophy';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import type { LLMModelDetail, ModelInsights } from '$lib/api/client';
	import PercentileMeter from './PercentileMeter.svelte';
	import { getArtificialAnalysis, getDesignArena } from './helpers';

	interface Props {
		model: LLMModelDetail;
		insights: ModelInsights | null;
	}

	let { model, insights }: Props = $props();

	const AA_LABELS = {
		coding_index: 'Coding index',
		intelligence_index: 'Intelligence index',
		agentic_index: 'Agentic index',
	} as const;

	const aa = $derived(getArtificialAnalysis(model));
	const arena = $derived(getDesignArena(model));
	const arenaGroups = $derived.by(() => {
		const groups = new Map<string, typeof arena>();
		for (const e of arena) groups.set(e.arena, [...(groups.get(e.arena) ?? []), e]);
		return [...groups.entries()].map(([name, rows]) => ({ name, rows: [...rows].sort((a, b) => a.rank - b.rank) }));
	});
	const aaCatalog = $derived(insights?.catalog.artificial_analysis);
	const local = $derived(insights?.benchmarks ?? []);
	const hasAny = $derived(!!aa || arena.length > 0 || local.length > 0);

	// Design Arena category ids are run-together words; unknown ids show as-is.
	const CATEGORY_LABELS: Record<string, string> = {
		agenticgamedev: 'Game dev (agentic)',
		agentichtmlslides: 'HTML slides (agentic)',
		agenticslides: 'Slides (agentic)',
		'agenticslides(html)': 'Slides, HTML (agentic)',
		'agenticslides(python-pptx)': 'Slides, python-pptx (agentic)',
		androidnative: 'Android native',
		blender3d: 'Blender 3D',
		fullstack: 'Full-stack',
		godotgamedev: 'Godot game dev',
		htmlslides: 'HTML slides',
		mobileapps: 'Mobile apps',
		pptxslides: 'PPTX slides',
		'python-pptxslides': 'python-pptx slides',
		spreadsheet: 'Spreadsheet',
		webapps: 'Web apps',
		'3d': '3D',
		asciiart: 'ASCII art',
		codecategories: 'Code (all categories)',
		dataviz: 'Data viz',
		gamedev: 'Game dev',
		graphicdesign: 'Graphic design',
		image: 'Image',
		imageediting: 'Image editing',
		logo: 'Logo',
		svg: 'SVG',
		uicomponent: 'UI component',
		website: 'Website',
	};

	function categoryLabel(c: string): string {
		return CATEGORY_LABELS[c] ?? c;
	}
</script>

{#if !hasAny}
	<EmptyState icon={Trophy} title="No benchmark data" description="OpenRouter publishes no Artificial Analysis or Design Arena scores for this model, and no benchmark rows were seeded for it." />
{:else}
	<div class="space-y-4">
		{#if aa}
			<Card.Root>
				<Card.Header class="pb-0">
					<Card.Title class="flex items-center gap-2 text-sm font-medium">
						Artificial Analysis
						<a href="https://artificialanalysis.ai/models" target="_blank" rel="noopener noreferrer" class="text-muted-foreground hover:text-foreground" aria-label="Artificial Analysis website"><ExternalLink class="size-3.5" /></a>
					</Card.Title>
					<Card.Description class="text-xs">Composite indices across their evaluation suites, via OpenRouter; meters place the score among catalog models that have one</Card.Description>
				</Card.Header>
				<Card.Content class="grid gap-5 sm:grid-cols-3">
					{#each Object.entries(AA_LABELS) as [key, label] (key)}
						{@const value = aa[key as keyof typeof AA_LABELS]}
						{@const cat = aaCatalog?.[key as keyof typeof AA_LABELS]}
						<PercentileMeter
							{label}
							value={value != null ? String(value) : '—'}
							percentile={value != null ? cat?.percentile : null}
							hint={value == null
								? 'Not published'
								: cat?.percentile != null
									? `Above ${Math.round(cat.percentile * 100)}% of ${cat.n} models · median ${cat.median}`
									: ''}
						/>
					{/each}
				</Card.Content>
			</Card.Root>
		{/if}

		{#if arenaGroups.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="flex items-center gap-2 text-sm font-medium">
						Design Arena
						<a href="https://www.designarena.ai/leaderboard" target="_blank" rel="noopener noreferrer" class="text-muted-foreground hover:text-foreground" aria-label="Design Arena leaderboard"><ExternalLink class="size-3.5" /></a>
					</Card.Title>
					<Card.Description class="text-xs">Pairwise human votes on generated UIs, apps and agent builds — Elo, leaderboard rank and win rate per category</Card.Description>
				</Card.Header>
				<Card.Content class="grid p-0 lg:grid-cols-2 lg:divide-x lg:divide-border">
					{#each arenaGroups as group (group.name)}
						<div class="overflow-x-auto">
							<table>
								<thead>
									<tr>
										<th><span class="capitalize">{group.name}</span> arena</th>
										<th class="num">Elo</th>
										<th class="num">Rank</th>
										<th class="num">Win rate</th>
									</tr>
								</thead>
								<tbody>
									{#each group.rows as e (e.category)}
										<tr>
											<td>{categoryLabel(e.category)}</td>
											<td class="num">{e.elo}</td>
											<td class="num">#{e.rank}</td>
											<td class="num">{e.win_rate.toFixed(1)}%</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/each}
				</Card.Content>
			</Card.Root>
		{/if}

		{#if local.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="text-sm font-medium">Seeded benchmarks</Card.Title>
					<Card.Description class="text-xs">Rows feeding the rankings' benchmark score (<code class="font-mono">manage.py seed_benchmarks</code>)</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table>
							<thead><tr><th>Benchmark</th><th class="num">Score</th><th class="num">Percentile</th><th>Source</th><th class="num">Updated</th></tr></thead>
							<tbody>
								{#each local as b (b.benchmark)}
									<tr>
										<td class="font-mono text-xs">{b.benchmark}</td>
										<td class="num">{b.score}</td>
										<td class="num">{b.percentile != null ? `p${Math.round(b.percentile * 100)}` : '—'}<span class="cell-sub block text-[11px] text-muted-foreground">of {b.n}</span></td>
										<td><Badge variant="outline" class="text-[11px]">{b.source}</Badge></td>
										<td class="num text-xs text-muted-foreground">{b.fetched_at.slice(0, 10)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}
	</div>
{/if}
