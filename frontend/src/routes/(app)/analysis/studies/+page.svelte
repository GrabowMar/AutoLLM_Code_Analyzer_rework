<script lang="ts">
	/**
	 * Studies — every research study (draft → locked → running → completed),
	 * with target counts, the pinned profile version, run progress and report
	 * state. Filters live in the URL; the list refreshes silently while a
	 * study is running or a report is being generated.
	 */
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		listStudies,
		STUDY_STATUSES,
		type Page,
		type Study,
		type StudyStatus
	} from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import FilterBar, { type FilterTag } from '$lib/components/FilterBar.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Plus from '@lucide/svelte/icons/plus';
	import SearchX from '@lucide/svelte/icons/search-x';
	import FileCheck from '@lucide/svelte/icons/file-check';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import Hint from '$lib/components/analysis/Hint.svelte';
	import {
		fmtDate,
		fmtPct,
		runBuckets,
		STUDY_STATUS_DESCRIPTIONS
	} from '$lib/components/analysis/study/format';

	const PER_PAGE = 20;
	const POLL_MS = 5000;

	// ── URL state ─────────────────────────────────────────────────────────
	const sp = $derived(page.url.searchParams);
	const statuses = $derived(
		(sp.get('status') ?? '')
			.split(',')
			.filter((s): s is StudyStatus => (STUDY_STATUSES as readonly string[]).includes(s))
	);
	const q = $derived(sp.get('q') ?? '');
	const pageNo = $derived(Math.max(1, Number(sp.get('page')) || 1));

	function setParams(next: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [k, v] of Object.entries(next)) {
			if (v) url.searchParams.set(k, v);
			else url.searchParams.delete(k);
		}
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function toggleStatus(s: StudyStatus) {
		const next = statuses.includes(s) ? statuses.filter((x) => x !== s) : [...statuses, s];
		setParams({ status: next.join(',') || null, page: null });
	}

	let searchValue = $state(untrack(() => q));
	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	function onSearchInput(value: string) {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => setParams({ q: value.trim() || null, page: null }), 300);
	}

	// ── Data ──────────────────────────────────────────────────────────────
	let result = $state<Page<Study> | null>(null);
	let loading = $state(true);
	let error = $state('');
	let tick = $state(0);

	$effect(() => {
		const params = { status: statuses, q, page: pageNo, tick };
		const ctrl = new AbortController();
		const silent = untrack(() => result !== null);
		if (!silent) loading = true;
		listStudies(
			{
				status: params.status,
				q: params.q || undefined,
				page: params.page,
				per_page: PER_PAGE
			},
			{ signal: ctrl.signal }
		)
			.then((res) => {
				if (ctrl.signal.aborted) return;
				result = res;
				error = '';
			})
			.catch((e) => {
				if (isAbortError(e) || ctrl.signal.aborted) return;
				error = formatApiError(e, 'Could not load the studies.');
			})
			.finally(() => {
				if (!ctrl.signal.aborted) loading = false;
			});
		return () => ctrl.abort();
	});

	const live = $derived(
		(result?.items ?? []).some(
			(s) => s.status === 'running' || s.launch?.pending || s.report_state?.status === 'generating'
		)
	);

	$effect(() => {
		if (!live) return;
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') tick += 1;
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	const tags = $derived<FilterTag[]>([
		...statuses.map((s) => ({
			key: `status-${s}`,
			label: s,
			onRemove: () => toggleStatus(s)
		})),
		...(q
			? [
					{
						key: 'q',
						label: `"${q}"`,
						onRemove: () => ((searchValue = ''), setParams({ q: null, page: null }))
					}
				]
			: [])
	]);

	function progressOf(s: Study) {
		const b = runBuckets(s.run_counts);
		const planned = s.launch?.planned ?? null;
		const denominator = planned ?? b.total;
		return { ...b, planned, denominator };
	}

	const SEGMENTS = [
		{ key: 'completed', label: 'completed', color: 'var(--success)' },
		{ key: 'partial', label: 'partial', color: 'var(--warning)' },
		{ key: 'failed', label: 'failed', color: 'var(--destructive)' },
		{ key: 'cancelled', label: 'cancelled', color: 'var(--muted-foreground)' },
		{ key: 'active', label: 'active', color: 'var(--info)' }
	] as const;
</script>

<div class="space-y-6">
	<PageHeader
		title="Studies"
		description="Research studies: a locked protocol (question, targets, analysis plan) launched as one analysis run per target, with a reproducible post-run report."
	>
		{#snippet actions()}
			<Button href="/analysis/studies/new" size="sm"><Plus />New study</Button>
		{/snippet}
	</PageHeader>

	<FilterBar
		searchPlaceholder="Search studies…"
		bind:searchValue
		{onSearchInput}
		activeTags={tags}
		onClearAll={() => {
			searchValue = '';
			setParams({ status: null, q: null, page: null });
		}}
		resultsText={result ? `${result.total} stud${result.total === 1 ? 'y' : 'ies'}` : ''}
		page={result?.page}
		pages={result?.pages}
		onGoToPage={(p) => setParams({ page: p > 1 ? String(p) : null })}
	>
		{#snippet filters()}
			<div class="fb-group" role="group" aria-label="Status">
				<span class="fb-group-label">Status</span>
				{#each STUDY_STATUSES as s (s)}
					<button
						type="button"
						class={cn('fb-chip', statuses.includes(s) && 'fb-chip-on')}
						aria-pressed={statuses.includes(s)}
						title={STUDY_STATUS_DESCRIPTIONS[s]}
						onclick={() => toggleStatus(s)}
					>
						{s}
					</button>
				{/each}
			</div>
		{/snippet}
	</FilterBar>

	{#if error}<ErrorAlert message={error} onretry={() => (tick += 1)} />{/if}

	{#if loading && !result}
		<LoadingState label="loading studies" />
	{:else if result && result.items.length === 0}
		{#if statuses.length || q}
			<EmptyState
				icon={SearchX}
				title="No study matches"
				description="Change or clear the filters."
			>
				{#snippet action()}
					<Button
						variant="outline"
						size="sm"
						onclick={() => setParams({ status: null, q: null, page: null })}>Clear filters</Button
					>
				{/snippet}
			</EmptyState>
		{:else}
			<EmptyState
				icon={FlaskConical}
				title="No studies yet"
				description="A study analyses a set of generated apps under one frozen protocol, so results are comparable and reproducible."
			>
				{#snippet action()}
					<Button href="/analysis/studies/new" size="sm"><Plus />New study</Button>
				{/snippet}
			</EmptyState>
		{/if}
	{:else if result}
		<Card.Root class="py-0">
			<Card.Content class="p-0">
				<div class="table-scroll-wrapper">
					<table class="table-card-mobile">
						<thead>
							<tr>
								<th scope="col">Study</th>
								<th scope="col">Status</th>
								<th scope="col" class="text-right">Targets</th>
								<th scope="col" class="md:max-lg:hidden">Profile</th>
								<th scope="col" class="min-w-[10rem]">Runs</th>
								<th scope="col" class="text-right">Complete</th>
								<th scope="col" class="md:max-lg:hidden">Report</th>
								<th scope="col" class="md:max-lg:hidden">Created</th>
							</tr>
						</thead>
						<tbody>
							{#each result.items as study (study.id)}
								{@const p = progressOf(study)}
								<tr>
									<td data-label="Study" class="max-w-[22rem] md:max-lg:max-w-[12rem]">
										<a
											href="/analysis/studies/{study.id}"
											class="block truncate font-medium hover:text-primary hover:underline"
										>
											{study.name}
										</a>
										<span class="block truncate text-xs text-muted-foreground">
											{study.research_questions.length
												? `${study.research_questions.length} research question${study.research_questions.length === 1 ? '' : 's'}`
												: study.description || 'No research question'}
										</span>
									</td>
									<td data-label="Status">
										<span class="flex flex-wrap items-center gap-1">
											<StatusBadge status={study.status} />
											{#if study.launch?.pending}<StatusBadge status={study.launch.status} />{/if}
										</span>
									</td>
									<td data-label="Targets" class="md:text-right tabular-nums">
										{#if study.preflight_summary?.targets === null || study.preflight_summary?.targets === undefined}
											<NaValue reason="No preflight yet: targets are counted by the preflight." />
										{:else}
											<span class="font-medium">{study.preflight_summary.eligible ?? '—'}</span>
											<span class="text-muted-foreground">/ {study.preflight_summary.targets}</span>
										{/if}
									</td>
									<td data-label="Profile" class="text-sm md:max-lg:hidden">
										{#if study.profile}
											<a
												class="hover:text-primary hover:underline"
												href="/analysis/profiles/{study.profile.id}">{study.profile.name}</a
											>
											<span class="text-xs text-muted-foreground">v{study.profile.version}</span>
										{:else}
											<span class="text-muted-foreground">none</span>
										{/if}
									</td>
									<td data-label="Runs">
										{#if p.denominator === 0}
											<span class="text-xs text-muted-foreground"
												>{study.status === 'draft' || study.status === 'locked'
													? 'not launched'
													: 'no runs'}</span
											>
										{:else}
											<div class="flex items-center gap-2">
												<div
													class="flex h-2 w-24 shrink-0 overflow-hidden rounded-full bg-muted"
													role="img"
													aria-label="{p.terminal} of {p.denominator} runs finished"
												>
													{#each SEGMENTS as seg (seg.key)}
														{#if p[seg.key] > 0}
															<span
																class="h-full"
																style="width: {(p[seg.key] / p.denominator) *
																	100}%; background: {seg.color}"
																title="{seg.label}: {p[seg.key]}"
															></span>
														{/if}
													{/each}
												</div>
												<span class="whitespace-nowrap text-xs tabular-nums text-muted-foreground"
													>{p.terminal}/{p.denominator}</span
												>
											</div>
										{/if}
									</td>
									<td data-label="Complete" class="md:text-right tabular-nums">
										{#if p.terminal === 0}
											<NaValue reason="No finished run yet." />
										{:else}
											<Hint
												underline={false}
												text="Runs whose required tools all succeeded (status completed), of the planned runs. Partial and failed runs have missing outcomes."
											>
												<span>{fmtPct(p.completed / p.denominator, 0)}</span>
											</Hint>
										{/if}
									</td>
									<td data-label="Report" class="md:max-lg:hidden">
										{#if study.report_state?.status === 'ready'}
											<a
												href="/analysis/studies/{study.id}?tab=results"
												class="inline-flex items-center gap-1 text-xs text-success-fg hover:underline"
											>
												<FileCheck class="h-3.5 w-3.5" aria-hidden="true" />ready
											</a>
										{:else if study.report_state}
											<StatusBadge status={study.report_state.status} />
										{:else}
											<span class="text-xs text-muted-foreground">none</span>
										{/if}
									</td>
									<td
										data-label="Created"
										class="whitespace-nowrap text-xs text-muted-foreground md:max-lg:hidden"
										>{fmtDate(study.created_at)}</td
									>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
