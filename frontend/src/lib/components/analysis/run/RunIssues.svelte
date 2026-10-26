<script lang="ts">
	/**
	 * RunIssues — deduplicated issues of a run: static issues (counted
	 * findings of several tools grouped by rule equivalence and location,
	 * with the tools that detected each) or AI consensus clusters (with the
	 * review samples that reported each). A row opens its findings.
	 *
	 * Props: `run`, `revision` (silent reload when it changes),
	 * `onfindings(kind, issueKey)` (show the issue's findings).
	 */
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import type {
		AnalysisRunDetail,
		IssueFilters,
		IssueKind,
		IssuePage,
		Severity
	} from '$lib/api/analysis';
	import {
		isMeasurement,
		isRunActive,
		isToolOk,
		listRunIssues,
		SEVERITIES
	} from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import { severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import { categoryLabel, problemLabel, toolProblems } from '../run-utils';
	import { csvParam, intParam, ISSUE_PARAMS, replaceParams } from './url';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import Layers from '@lucide/svelte/icons/layers';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import SearchX from '@lucide/svelte/icons/search-x';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		run: AnalysisRunDetail;
		revision: string;
		onfindings?: (kind: IssueKind, issueKey: string) => void;
	}

	let { run, revision, onfindings }: Props = $props();

	const PER_PAGE = 50;

	const sp = $derived(page.url.searchParams);
	const kind = $derived<IssueKind>(sp.get('ikind') === 'ai' ? 'ai' : 'static');
	const severities = $derived(
		csvParam(sp.get('isev')).filter((s): s is Severity =>
			(SEVERITIES as readonly string[]).includes(s)
		)
	);
	const category = $derived(sp.get('icat') ?? '');
	const pageNo = $derived(intParam(sp.get('ipage'), 1));

	const filters = $derived<IssueFilters>({
		kind,
		severity: severities.length ? severities : undefined,
		category: category || undefined,
		page: pageNo,
		per_page: PER_PAGE
	});
	const filterKey = $derived(JSON.stringify(filters));

	function update(updates: Record<string, string | number | null>, resetPage = true) {
		void replaceParams(page.url, resetPage ? { ...updates, ipage: null } : updates);
	}

	let data = $state<IssuePage | null>(null);
	let loading = $state(true);
	let refreshing = $state(false);
	let error = $state('');
	let controller: AbortController | null = null;
	let lastKey = '';

	async function load(silent: boolean) {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		if (!silent) refreshing = true;
		try {
			data = await listRunIssues(run.id, filters, { signal: mine.signal });
			error = '';
		} catch (e) {
			if (isAbortError(e)) return;
			if (!(silent && data)) error = formatApiError(e, 'Could not load the issues.');
		} finally {
			if (controller === mine) {
				loading = false;
				refreshing = false;
			}
		}
	}

	$effect(() => {
		const key = filterKey;
		void revision;
		untrack(() => {
			const silent = key === lastKey;
			lastKey = key;
			void load(silent);
		});
		return () => controller?.abort();
	});

	const m = $derived(isMeasurement(run.measurement) ? run.measurement : null);
	const aiResult = $derived(run.results.find((r) => r.tool_kind === 'ai') ?? null);
	const staticProblems = $derived(toolProblems(run.results).filter((p) => !p.isAi));
	const active = $derived(isRunActive(run.status));

	const categoryItems = $derived.by(() => {
		const source = kind === 'ai' ? m?.ai?.consensus.by_category : m?.static.issues.by_category;
		return Object.entries(source ?? {}).map(([c, n]) => ({
			value: c,
			label: `${categoryLabel(c)} (${n})`
		}));
	});

	function toggleSeverity(s: Severity) {
		const next = severities.includes(s) ? severities.filter((x) => x !== s) : [...severities, s];
		update({
			isev: next.length ? SEVERITIES.filter((x) => next.includes(x)).join(',') : null
		});
	}

	const filtered = $derived(severities.length > 0 || !!category);
	const samplesOk = $derived(m?.ai?.samples_ok ?? null);

	function location(start: number | null, end: number | null): string {
		if (!start) return '';
		return end && end > start ? `:${start}–${end}` : `:${start}`;
	}
</script>

<div class="space-y-4">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<Segmented
			aria-label="Issue source"
			value={kind}
			items={[
				{
					value: 'static',
					label: 'Static issues',
					icon: ScanSearch,
					badge: m ? String(m.static.issues.total) : undefined
				},
				{
					value: 'ai',
					label: 'AI consensus',
					icon: Sparkles,
					badge: m?.ai?.available ? String(m.ai.consensus.total) : undefined,
					disabled: !aiResult,
					title: aiResult ? 'Counted AI consensus clusters' : 'No AI reviewer in this plan'
				}
			]}
			onValueChange={(v) =>
				void replaceParams(page.url, { ikind: v === 'ai' ? 'ai' : null }, ISSUE_PARAMS)}
		/>
		<p class="max-w-xl text-xs text-muted-foreground">
			{#if kind === 'static'}
				Counted static findings grouped across tools (same rule family and location): each issue is
				counted once.
			{:else}
				Clusters reported by at least the threshold of successful review samples, with grounded
				evidence.
			{/if}
		</p>
	</div>

	<div class="flex flex-wrap items-center gap-2">
		<div class="fb-group" role="group" aria-label="Severity">
			{#each SEVERITIES as s (s)}
				<button
					type="button"
					class={cn('fb-chip capitalize', severities.includes(s) && 'fb-chip-on')}
					aria-pressed={severities.includes(s)}
					onclick={() => toggleSeverity(s)}>{s}</button
				>
			{/each}
		</div>
		<Select.Simple
			size="sm"
			class="w-52"
			aria-label="Category"
			value={category}
			items={categoryItems}
			placeholder="All categories"
			clearLabel="All categories"
			onValueChange={(v) => update({ icat: v || null })}
		/>
		{#if filtered}
			<Button variant="ghost" size="sm" onclick={() => update({ isev: null, icat: null })}
				>Clear</Button
			>
		{/if}
	</div>

	{#if kind === 'static' && staticProblems.length > 0}
		<p class="flex items-start gap-1.5 text-xs text-warning-fg">
			<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
			Issues only {staticProblems.map(problemLabel).join(', ')} could have found are missing.
		</p>
	{/if}

	{#if error}
		<ErrorAlert message={error} onretry={() => load(false)} />
	{/if}

	{#if loading && !data}
		<LoadingState label="loading issues" />
	{:else if data && data.items.length === 0}
		{#if filtered}
			<EmptyState icon={SearchX} title="No issues match these filters">
				{#snippet action()}
					<Button variant="outline" size="sm" onclick={() => update({ isev: null, icat: null })}
						>Clear filters</Button
					>
				{/snippet}
			</EmptyState>
		{:else if active}
			<EmptyState icon={Hourglass} title="Issues are grouped when the run finishes" />
		{:else if kind === 'ai' && aiResult && !isToolOk(aiResult.status)}
			<EmptyState
				icon={TriangleAlert}
				title="The AI review did not succeed"
				description={aiResult.error_message || undefined}
			/>
		{:else if kind === 'static' && staticProblems.length > 0}
			<EmptyState
				icon={TriangleAlert}
				title="No counted issues from the tools that succeeded"
				description="Some tools did not succeed, so this is not a clean result."
			/>
		{:else}
			<EmptyState
				icon={CircleCheck}
				title={kind === 'ai' ? 'No AI consensus' : 'No counted static issues'}
				description={kind === 'ai'
					? 'No finding was reported by enough samples with grounded evidence.'
					: 'Every static tool succeeded or was not applicable and nothing counted was found.'}
			/>
		{/if}
	{:else if data}
		<Card.Root class="gap-0 py-0">
			<Card.Content class="p-0">
				<div
					class={cn('table-scroll-wrapper transition-opacity', refreshing && 'opacity-60')}
					aria-busy={refreshing}
				>
					<table class="table-card-mobile">
						<thead>
							<tr>
								<th>Severity</th>
								<th>Issue</th>
								<th>Location</th>
								<th>{kind === 'ai' ? 'Samples' : 'Detected by'}</th>
								<th class="text-right">Findings</th>
							</tr>
						</thead>
						<tbody>
							{#each data.items as issue (issue.issue_key)}
								<tr class="relative">
									<td data-label="Severity"
										><Badge class={severityColors[issue.severity]}>{issue.severity}</Badge></td
									>
									<td data-label="Issue" class="max-w-md">
										<button
											type="button"
											class="block w-full cursor-pointer text-left after:absolute after:inset-0 after:content-[''] focus-visible:outline-none focus-visible:after:rounded focus-visible:after:ring-2 focus-visible:after:ring-ring/50"
											onclick={() => onfindings?.(kind, issue.issue_key)}
											aria-label="Show the findings of {issue.title}"
										>
											<span class="line-clamp-2 break-words text-sm font-medium">{issue.title}</span
											>
											<span class="block text-[11px] text-muted-foreground">
												{issue.categories
													.map(categoryLabel)
													.join(', ')}{#if issue.cwe.length}{' · '}<span class="font-mono"
														>{issue.cwe.join(', ')}</span
													>{/if}
											</span>
										</button>
									</td>
									<td data-label="Location" class="max-w-xs">
										<div class="min-w-0 flex-1">
											<span class="block truncate font-mono text-xs" title={issue.file_path}
												>{issue.file_path}{location(issue.line_start, issue.line_end)}</span
											>
											{#if issue.files.length > 1}
												<span class="text-[11px] text-muted-foreground"
													>+{issue.files.length - 1} more file{issue.files.length === 2
														? ''
														: 's'}</span
												>
											{/if}
										</div>
									</td>
									<td data-label={kind === 'ai' ? 'Samples' : 'Detected by'}>
										<div class="min-w-0 flex-1">
											{#if kind === 'ai'}
												<span class="flex flex-wrap items-center gap-1">
													{#each issue.samples as s (s)}
														<span class="rounded border border-border px-1 font-mono text-[11px]"
															>#{s}</span
														>
													{/each}
													{#if samplesOk != null}
														<span class="text-[11px] text-muted-foreground"
															>{issue.samples.length}/{samplesOk}</span
														>
													{/if}
												</span>
											{:else}
												<span class="flex flex-wrap gap-1">
													{#each issue.tools as t (t)}
														<span class="rounded border border-border px-1 font-mono text-[11px]"
															>{t}</span
														>
													{/each}
												</span>
												{#if issue.rule_ids.length}
													<span
														class="mt-0.5 block max-w-56 truncate font-mono text-[11px] text-muted-foreground"
														title={issue.rule_ids.join(', ')}
													>
														{issue.rule_ids.join(', ')}
													</span>
												{/if}
											{/if}
										</div>
									</td>
									<td data-label="Findings" class="tabular-nums md:text-right">
										<span class="inline-flex items-center gap-1">
											<Layers
												class="size-3.5 text-muted-foreground"
												aria-hidden="true"
											/>{issue.findings}
										</span>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<PaginationBar
					class="border-t border-border"
					resultsText="{data.total.toLocaleString()} {kind === 'ai'
						? 'consensus cluster'
						: 'issue'}{data.total === 1 ? '' : 's'}"
					page={data.page}
					pages={data.pages}
					onGoToPage={(p) => update({ ipage: p }, false)}
				/>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
