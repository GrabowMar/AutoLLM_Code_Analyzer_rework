<script lang="ts">
	/**
	 * SameSetupPanel — a run's same-setup group (`GET /generation/runs/{id}/replicates/`):
	 * every visible run sharing its configuration fingerprint, with each run's relation
	 * (this run, retry = same cell, replay = same run fingerprint, rerun = ad hoc /
	 * beyond plan, replicate),
	 * its metrics, its code similarity to this run and links to its page;
	 * summary rows (mean, SD, min, max, CV) under the members; the
	 * determinism probe for seeded replays; the server's caveats.
	 * Self-fetching; unmeasured values show "—", never 0.
	 *
	 * Props:
	 * - `jobId`: the generation run the page shows (a v3 run id).
	 * - `variant`: 'app' (default: generation metrics first) or 'analysis'
	 *   (findings first: code issues, issues per kLOC, measured or not).
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getRunReplicates, type ReplicateMember, type RunReplicates } from '$lib/api/variability';
	import {
		DASH,
		RELATION_HELP,
		RELATION_LABEL,
		formatMetricValue,
		formatRatio,
		shortId,
	} from '$lib/components/studies/variability/variability';
	import Layers from '@lucide/svelte/icons/layers';

	interface Props {
		jobId: string;
		variant?: 'app' | 'analysis';
	}

	let { jobId, variant = 'app' }: Props = $props();

	let data = $state<RunReplicates | null>(null);
	let loading = $state(false);
	let error = $state('');
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		loading = true;
		error = '';
		try {
			const next = await getRunReplicates(jobId, { signal: ctrl.signal });
			if (controller === ctrl) data = next;
		} catch (e) {
			if (!isAbortError(e) && controller === ctrl) error = formatApiError(e, 'Could not load runs with the same setup');
		} finally {
			if (controller === ctrl) loading = false;
		}
	}

	$effect(() => {
		void jobId;
		untrack(() => void load());
		return () => controller?.abort();
	});

	interface Column {
		key: keyof ReplicateMember & string;
		label: string;
		unit: string;
		title?: string;
	}

	const GENERATION: Column[] = [
		{ key: 'pass_rate', label: 'Pass', unit: 'share', title: 'Conformance pass rate' },
		{ key: 'source_loc', label: 'LOC', unit: 'lines', title: 'Source lines of code (non-test)' },
		{ key: 'source_files', label: 'Files', unit: 'files', title: 'Source files (non-test)' },
		{ key: 'cost', label: 'Cost', unit: 'USD' },
		{ key: 'total_tokens', label: 'Tokens', unit: 'tokens' },
		{ key: 'llm_calls', label: 'Calls', unit: 'calls', title: 'LLM calls' },
		{ key: 'wall_seconds', label: 'Wall', unit: 's', title: 'Sandbox wall clock (agent runs), else the run duration' },
	];
	const FINDINGS: Column[] = [
		{ key: 'code_issues', label: 'Code issues', unit: 'issues', title: 'Deduplicated code issues (common tools of this group, latest result per tool)' },
		{ key: 'issues_per_kloc', label: 'Issues/kLOC', unit: 'per kLOC', title: 'Code issues per 1,000 source lines' },
	];
	const columns = $derived<Column[]>(
		variant === 'analysis'
			? [...FINDINGS, GENERATION[0], GENERATION[1], GENERATION[3]]
			: [...GENERATION, ...FINDINGS]
	);
	const STATS = ['mean', 'sd', 'min', 'max', 'cv'] as const;
	const STAT_LABEL: Record<(typeof STATS)[number], string> = { mean: 'Mean', sd: 'SD', min: 'Min', max: 'Max', cv: 'CV' };

	const members = $derived(data?.members ?? []);
	const others = $derived(members.filter((m) => !m.is_self));
	const completed = $derived(members.filter((m) => m.status === 'completed').length);
	const probes = $derived(data?.determinism ?? []);

	function cellValue(m: ReplicateMember, col: Column): number | null {
		const v = m[col.key];
		return typeof v === 'number' ? v : null;
	}

	function relationVariant(m: ReplicateMember): 'default' | 'secondary' | 'outline' {
		if (m.is_self) return 'default';
		return m.relation === 'replicate' ? 'outline' : 'secondary';
	}

	function origin(m: ReplicateMember): string {
		if (m.study_name) {
			const repeat = m.repeat_index != null ? ` · repeat ${m.repeat_index + 1}` : '';
			return `${m.study_name}${repeat}${m.beyond_plan ? ' · beyond plan' : ''}`;
		}
		return 'ad hoc';
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<Card.Title class="flex flex-wrap items-center gap-2 text-sm font-medium">
			Same setup
			{#if data}
				<Badge variant="outline" title="Runs in this group (yours)">{data.n} run{data.n === 1 ? '' : 's'}</Badge>

			{/if}
		</Card.Title>
		<Card.Description class="text-xs">
			{#if !data}
				Runs that share this run's configuration: their spread is the model's run-to-run variation.
			{:else if data.grouping.key === 'config_fingerprint'}
				Runs with configuration fingerprint <code class="font-mono">{data.grouping.value}</code> (seed excluded): their spread is run-to-run
				variation.
			{:else}
				This run has no configuration fingerprint (reference runs have none), so it is its own group.
			{/if}
		</Card.Description>
	</Card.Header>
	<Card.Content class="p-0">
		{#if error}
			<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
		{/if}
		{#if loading && !data}
			<LoadingState label="loading runs with the same setup" class="py-8" />
		{:else if data && others.length === 0}
			<EmptyState
				compact
				icon={Layers}
				title="No other run shares this setup yet"
				description="Replay this run (same seed) or rerun it (new seed) to measure how much its result varies."
			/>
		{:else if data}
			<div class="table-frame" {@attach tableFrame}>
				<table class="min-w-[56rem]">
					<thead>
						<tr>
							<th scope="col" class="col-sticky">Run</th>
							<th scope="col">Relation</th>
							<th scope="col" class="num">Seed</th>
							<th scope="col">From</th>
							<th scope="col">Status</th>
							{#each columns as col (col.key)}
								<th scope="col" class="num" title={col.title}>{col.label}</th>
							{/each}
							{#if variant === 'analysis'}
								<th scope="col" title="Whether the group's common instrument set measured this run">Analyzed</th>
							{/if}
							<th scope="col" class="num" title="Code similarity to this run: 5-token shingle Jaccard · file-path Jaccard of agent-written source">Similar to this run</th>
						</tr>
					</thead>
					<tbody>
						{#each members as m (m.run_id)}
							<tr data-selected={m.is_self ? 'true' : undefined} class={m.is_self ? 'bg-primary/5' : ''}>
								<td class="col-sticky font-mono text-xs {m.is_self ? 'bg-primary/5' : 'bg-card'}">
									{#if m.is_self}
										<span class="font-medium">{shortId(m.run_id)}</span>
									{:else}
										<a href="/samples/{m.run_id}" class="text-primary hover:underline">{shortId(m.run_id)}</a>
									{/if}
								</td>
								<td><Badge variant={relationVariant(m)} title={RELATION_HELP[m.relation]}>{RELATION_LABEL[m.relation]}</Badge></td>
								<td class="num text-xs">{m.seed ?? DASH}</td>
								<td class="min-w-44 max-w-64 text-xs">
									<span class="line-clamp-2 break-words" title={m.condition_label ?? undefined}>{origin(m)}</span>
								</td>
								<td><StatusBadge status={m.status} /></td>
								{#each columns as col (col.key)}
									{@const v = cellValue(m, col)}
									<td class="num text-xs">
										{#if v == null}<span class="text-muted-foreground">{DASH}</span>{:else}{formatMetricValue(v, col.unit)}{/if}
									</td>
								{/each}
								{#if variant === 'analysis'}
									<td class="text-xs">{m.status !== 'completed' ? DASH : m.findings_measured ? 'yes' : 'not analyzed'}</td>
								{/if}
								<td class="num text-xs">
									{#if m.is_self}
										<span class="text-muted-foreground">this run</span>
									{:else if m.similarity_to_self}
										{formatRatio(m.similarity_to_self.shingle_jaccard)} · {formatRatio(m.similarity_to_self.path_jaccard)}
									{:else}
										<span class="text-muted-foreground">{DASH}</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
					<tfoot>
						{#each STATS as stat (stat)}
							<tr class="border-t border-border/60 bg-muted/30">
								<th scope="row" class="col-sticky bg-muted/30 text-left text-xs font-medium">{STAT_LABEL[stat]}</th>
								<td colspan="4" class="text-xs text-muted-foreground">{stat === 'mean' ? `over ${completed} completed run${completed === 1 ? '' : 's'}` : ''}</td>
								{#each columns as col (col.key)}
									{@const s = data.summary[col.key]}
									<td class="num text-xs">
										{#if stat === 'cv'}
											{formatRatio(s?.cv)}
										{:else}
											{formatMetricValue(s?.[stat], col.unit)}
										{/if}
										{#if stat === 'mean' && s}<span class="block text-muted-foreground">n {s.n}</span>{/if}
									</td>
								{/each}
								{#if variant === 'analysis'}<td></td>{/if}
								<td></td>
							</tr>
						{/each}
					</tfoot>
				</table>
			</div>
			{#if probes.length > 0}
				<div class="border-t border-border/60 px-4 py-2.5">
					<p class="section-label">Seeded replays</p>
					<ul class="mt-1 space-y-1 text-xs">
						{#each probes as p (p.run_id)}
							<li>
								<a href="/samples/{p.run_id}" class="font-mono text-primary hover:underline">{shortId(p.run_id)}</a>:
								{#if !p.comparable}
									<span class="text-muted-foreground">no stored LLM calls to compare.</span>
								{:else if p.reproduced}
									every stored call response matched ({p.calls_a} calls).
								{:else}
									first differs at call {p.first_divergent_call} ({p.calls_a} calls here, {p.calls_b} there){p.same_providers
										? `; served by ${p.providers_a.join(', ') || 'unknown'} in both`
										: `; providers ${p.providers_a.join(', ') || 'unknown'} vs ${p.providers_b.join(', ') || 'unknown'}`}.
								{/if}
							</li>
						{/each}
					</ul>
				</div>
			{/if}
		{/if}
		{#if data && data.notes.length > 0}
			<ul class="space-y-1 border-t border-border/60 px-4 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
				{#each data.notes as note (note)}
					<li>{note}</li>
				{/each}
			</ul>
		{/if}
	</Card.Content>
</Card.Root>
