<script lang="ts">
	/**
	 * External covariates stored on a model: Epoch AI (reported size, training
	 * compute and data, Epoch's confidence) and LMArena (ratings with their 95%
	 * interval, votes, rank) — both CC-BY 4.0 and matched through a hand-curated
	 * name map, so each card shows its attribution, license, fetch date and how
	 * the match was made (exact / variant / assumed). Descriptive only.
	 *
	 *   <ModelExternalCovariates metadata={model.metadata} />
	 *
	 * For a local endpoint model, `metadata` may be its catalog twin's; pass the
	 * model's own metadata as `ownMetadata` so an import curated for that route
	 * (its own `epoch` / `lmarena`) wins over the twin's.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Library from '@lucide/svelte/icons/library';
	import {
		ARENA_CATEGORY_LABELS,
		displayMatch,
		externalView,
		fmtScientific,
		rangePercent,
		type ArenaCategory,
		type ExternalMatch,
	} from '$lib/api/model-covariates';
	import { compactNumber, fmtParams, isoDay } from './helpers';

	interface Props {
		metadata: Record<string, unknown> | null | undefined;
		/** The model's own metadata when `metadata` is inherited (its own epoch / lmarena win). */
		ownMetadata?: Record<string, unknown> | null;
	}

	let { metadata, ownMetadata = null }: Props = $props();

	// Own records win; a record shown from the twin is flagged inherited (its match only assumed).
	const view = $derived(externalView(metadata, ownMetadata));
	const epoch = $derived(view.epoch);
	const arena = $derived(view.arena);
	const epochInherited = $derived(view.epochInherited);
	const arenaInherited = $derived(view.arenaInherited);
	const ARENA_CONFIG_ORDER = ['webdev', 'text', 'agent'];
	const configRank = (config: string) => {
		const i = ARENA_CONFIG_ORDER.indexOf(config);
		return i === -1 ? ARENA_CONFIG_ORDER.length : i;
	};
	const arenaNames = $derived(Object.entries(arena?.names ?? {}).sort(([a], [b]) => configRank(a) - configRank(b)));

	const MATCH_TEXT: Record<ExternalMatch, string> = {
		exact: 'Exact match: same model and the configuration we call.',
		variant: 'Same model, but the source measured a different reasoning effort or release than we call.',
		assumed: 'Identity assumed (undisclosed served model or effort, or a disputed catalog link).',
	};

	const ARENA_CONFIG_LABELS: Record<string, string> = { webdev: 'WebDev', text: 'Text', agent: 'Agent' };

	function fmtValue(c: ArenaCategory, v: number | null): string {
		if (v == null) return '—';
		const text = c.metric === 'score' ? v.toFixed(3) : String(Math.round(v));
		return text.replace(/^-/, '−');
	}

	function ciText(c: ArenaCategory): string {
		if (c.lower == null || c.upper == null) return '';
		return `${fmtValue(c, c.lower)} – ${fmtValue(c, c.upper)}`;
	}

	function rankText(c: ArenaCategory): string {
		if (c.rank == null) return '—';
		return c.n_ranked != null ? `#${c.rank} of ${c.n_ranked}` : `#${c.rank}`;
	}

	const epochFacts = $derived.by(() => {
		if (!epoch) return [];
		const rows: { label: string; value: string; title?: string; sub?: string; text?: boolean }[] = [
			{
				label: 'Reported parameters',
				value: fmtParams(epoch.parameters),
				title: epoch.parameters != null ? epoch.parameters.toLocaleString() : undefined,
			},
			{
				label: 'Active parameters',
				value: fmtParams(epoch.active_params_parsed),
				sub: epoch.active_params_parsed != null ? 'parsed from the notes below' : 'not stated unambiguously',
			},
			{
				label: 'Training compute',
				value: epoch.training_compute_flop != null ? `${fmtScientific(epoch.training_compute_flop)} FLOP` : '—',
			},
			{
				label: 'Training data',
				value: epoch.training_dataset_size != null ? compactNumber(epoch.training_dataset_size) : '—',
				sub: epoch.training_dataset_size != null ? 'datapoints (tokens for language models)' : undefined,
				title: epoch.training_dataset_size_unit ?? undefined,
			},
			{ label: 'Published', value: epoch.publication_date ?? '—' },
			{ label: 'Accessibility', value: epoch.model_accessibility ?? '—', text: true },
			{ label: 'Epoch confidence', value: epoch.confidence ?? '—', text: true },
		];
		return rows;
	});
</script>

{#snippet matchLine(storedMatch: ExternalMatch | null, note: string | null, inherited: boolean)}
	{@const match = displayMatch(storedMatch, inherited)}
	{#if match || note}
		<div class="space-y-1 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
			{#if match}
				<p class="flex flex-wrap items-center gap-1.5">
					<Badge variant={match === 'exact' ? 'secondary' : 'outline'} class="font-mono">{match}</Badge>
					<span class="text-muted-foreground">{MATCH_TEXT[match]}</span>
				</p>
			{/if}
			{#if inherited}
				<p class="leading-snug text-muted-foreground">Inherited from the catalog twin (this route has no curated entry of its own){#if storedMatch}; the twin's own match is <span class="font-mono">{storedMatch}</span>{/if}.</p>
			{/if}
			{#if note}<p class="leading-snug text-muted-foreground">{note}</p>{/if}
		</div>
	{/if}
{/snippet}

{#snippet attribution(source: string, href: string | null, license: string | null, fetchedAt: string | null, extra?: string)}
	<p class="text-[11px] leading-snug text-muted-foreground">
		Source:
		{#if href}
			<a href={href} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 text-primary hover:underline">{source}<ExternalLink class="size-3" aria-hidden="true" /></a>
		{:else}
			{source}
		{/if}
		{#if license}· {license}{/if}
		{#if extra}· {extra}{/if}
		· fetched <span class="font-mono">{isoDay(fetchedAt) ?? '—'}</span>
	</p>
{/snippet}

{#if !epoch && !arena}
	<EmptyState
		compact
		icon={Library}
		title="No external covariates"
		description="This model is not matched to Epoch AI or LMArena: it has no entry in the curated name map (external_model_map.yaml), or the import has not run yet."
	/>
{:else}
	<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
		<Card.Root class="min-w-0">
			<Card.Header class="pb-0">
				<Card.Title class="flex items-center gap-2 text-sm font-medium">
					Epoch AI
					<a href="https://epoch.ai/data/ai-models" target="_blank" rel="noopener noreferrer" class="text-muted-foreground hover:text-foreground" aria-label="Epoch AI models database"><ExternalLink class="size-3.5" /></a>
				</Card.Title>
				<Card.Description class="text-xs">Reported size, training compute and data as curated by Epoch AI (often estimates; see their confidence)</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				{#if !epoch}
					<EmptyState compact icon={Library} title="Not matched to Epoch AI" description="Epoch lists no model under a name mapped to this one." />
				{:else}
					<p class="text-xs text-muted-foreground">
						Matched as <span class="font-mono text-foreground">{epoch.model}</span>{#if epoch.organization}<span aria-hidden="true">{' · '}</span>{epoch.organization}{/if}
					</p>
					<dl class="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2">
						{#each epochFacts as fact (fact.label)}
							<div class="min-w-0">
								<dt class="text-xs text-muted-foreground">{fact.label}</dt>
								<dd class="text-sm font-medium break-words {fact.text ? '' : 'font-mono tabular-nums'}" class:text-muted-foreground={fact.value === '—'} title={fact.title}>{fact.value}</dd>
								{#if fact.sub}<dd class="text-[11px] text-muted-foreground">{fact.sub}</dd>{/if}
							</div>
						{/each}
					</dl>
					{#if epoch.parameters_notes}
						<div class="space-y-1">
							<p class="section-label">Parameter notes (Epoch)</p>
							<p class="text-xs leading-relaxed break-words whitespace-pre-line text-muted-foreground">{epoch.parameters_notes}</p>
						</div>
					{/if}
					{#if epoch.training_compute_notes}
						<details class="text-xs">
							<summary class="cursor-pointer text-muted-foreground hover:text-foreground">Training compute notes</summary>
							<p class="mt-1 leading-relaxed break-words whitespace-pre-line text-muted-foreground">{epoch.training_compute_notes}</p>
						</details>
					{/if}
					{@render matchLine(epoch.match, epoch.mapping_note, epochInherited)}
					{@render attribution("Epoch AI, 'All AI models'", epoch.source_page ?? epoch.source_url, epoch.license, epoch.fetched_at, epoch.epoch_last_modified ? `row updated ${isoDay(epoch.epoch_last_modified)}` : undefined)}
					{#if epoch.link}
						<a href={epoch.link} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-[11px] text-primary hover:underline">Epoch's reference for this model<ExternalLink class="size-3" aria-hidden="true" /></a>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root class="min-w-0 gap-0 overflow-clip pb-0">
			<Card.Header class="pb-3">
				<Card.Title class="flex items-center gap-2 text-sm font-medium">
					LMArena
					<a href="https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset" target="_blank" rel="noopener noreferrer" class="text-muted-foreground hover:text-foreground" aria-label="LMArena leaderboard dataset"><ExternalLink class="size-3.5" /></a>
				</Card.Title>
				<Card.Description class="text-xs">Pairwise human-preference leaderboards: rating with its 95% interval, placed on the category's full range (bar)</Card.Description>
			</Card.Header>
			{#if !arena}
				<Card.Content class="pb-6">
					<EmptyState compact icon={Library} title="Not matched to LMArena" description="No LMArena leaderboard lists a name mapped to this model." />
				</Card.Content>
			{:else}
				<Card.Content class="p-0">
					{#if arenaNames.length}
						<p class="px-6 pb-2 text-xs text-muted-foreground">
							Matched as
							{#each arenaNames as [config, name], i (config)}
								{#if i > 0}<span aria-hidden="true">{' · '}</span>{/if}<span class="whitespace-nowrap">{ARENA_CONFIG_LABELS[config] ?? config}: <span class="font-mono text-foreground">{name}</span></span>
							{/each}
						</p>
					{/if}
					<div class="overflow-x-auto border-t border-border">
						<table>
							<thead>
								<tr>
									<th>Leaderboard</th>
									<th class="num">Rating</th>
									<th class="min-w-28">95% interval</th>
									<th class="num">Rank</th>
									<th class="num">n</th>
								</tr>
							</thead>
							<tbody>
								{#each arena.categories as c (c.key)}
									{@const lo = rangePercent(c.lower, c.category_min, c.category_max)}
									{@const hi = rangePercent(c.upper, c.category_min, c.category_max)}
									{@const at = rangePercent(c.value, c.category_min, c.category_max)}
									<tr>
										<td>
											{ARENA_CATEGORY_LABELS[c.key] ?? c.key}
											{#if c.metric === 'score'}<span class="block text-[11px] text-muted-foreground">score, not Elo</span>{/if}
										</td>
										<td class="num">{fmtValue(c, c.value)}</td>
										<td>
											<div class="space-y-1">
												{#if at != null}
													<div
														class="relative h-1.5 rounded-full bg-muted"
														role="img"
														aria-label="{ARENA_CATEGORY_LABELS[c.key] ?? c.key}: {fmtValue(c, c.value)}, interval {ciText(c) || 'unknown'}, on the range {fmtValue(c, c.category_min)} to {fmtValue(c, c.category_max)}"
														title="Leaderboard range {fmtValue(c, c.category_min)}–{fmtValue(c, c.category_max)}"
													>
														{#if lo != null && hi != null}
															<span class="absolute inset-y-0 rounded-full bg-primary/30" style:left="{lo}%" style:width="{Math.max(hi - lo, 1)}%" aria-hidden="true"></span>
														{/if}
														<span class="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-primary" style:left="{at}%" aria-hidden="true"></span>
													</div>
												{/if}
												<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{ciText(c) || '—'}</span>
											</div>
										</td>
										<td class="num" class:text-muted-foreground={c.rank == null}>{rankText(c)}</td>
										<td class="num" title={c.metric === 'score' ? 'sessions' : 'votes'}>
											<span class:text-muted-foreground={c.count == null}>{compactNumber(c.count)}</span>
											<span class="block text-[11px] text-muted-foreground">{c.metric === 'score' ? 'sessions' : 'votes'}</span>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<div class="space-y-2 border-t border-border px-6 py-3">
						{@render matchLine(arena.match, arena.mapping_note, arenaInherited)}
						{@render attribution('LMArena leaderboard dataset', arena.source_url, arena.license, arena.fetched_at, arena.publish_date ? `leaderboard of ${arena.publish_date}` : undefined)}
						<p class="text-[11px] leading-snug text-muted-foreground">Arena prompts, scaffolds and voters differ from this platform's tasks; use the rating as an external covariate, not as a measurement of the apps generated here.</p>
					</div>
				</Card.Content>
			{/if}
		</Card.Root>
	</div>
{/if}
