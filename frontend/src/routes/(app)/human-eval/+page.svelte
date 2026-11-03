<script lang="ts">
	/**
	 * Human evaluation tasks: the researcher's manual testing tasks with
	 * status filter, server-side search (URL-synced `?status=&q=&page=`), apps,
	 * participants and data-collection progress (submitted ratings / target).
	 */
	import { onDestroy, untrack } from 'svelte';
	import { replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import Plus from '@lucide/svelte/icons/plus';
	import ClipboardCheck from '@lucide/svelte/icons/clipboard-check';
	import Search from '@lucide/svelte/icons/search';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listTasks, type TaskCounts, type TaskListItem, type TaskStatus } from '$lib/api/human-eval';
	import { formatDate } from '$lib/utils/formatters';

	const STATUSES: TaskStatus[] = ['draft', 'open', 'paused', 'closed', 'archived'];
	const PER_PAGE = 25;

	// ── URL state ─────────────────────────────────────────────────────
	const q = page.url.searchParams;
	const initialStatus = q.get('status') ?? '';
	let status = $state<TaskStatus | ''>(STATUSES.includes(initialStatus as TaskStatus) ? (initialStatus as TaskStatus) : '');
	let search = $state(q.get('q') ?? '');
	let searchText = $state(q.get('q') ?? '');
	let pageNo = $state(Number(q.get('page')) || 1);

	function writeUrl() {
		const url = new URL(page.url);
		const p = url.searchParams;
		const set = (k: string, v: string) => (v ? p.set(k, v) : p.delete(k));
		set('status', status);
		set('q', search);
		set('page', pageNo > 1 ? String(pageNo) : '');
		if (url.search === page.url.search) return;
		try {
			replaceState(url, page.state);
		} catch {
			// Before the router is ready: the next change writes it.
		}
	}

	// ── data ──────────────────────────────────────────────────────────
	let tasks = $state<TaskListItem[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let everLoaded = $state(false);
	let ctl: AbortController | null = null;

	async function fetchTasks() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		try {
			const data = await listTasks(
				{ status: status || undefined, search: search || undefined, page: pageNo, per_page: PER_PAGE },
				{ signal: mine.signal }
			);
			tasks = data.items;
			total = data.total;
			pages = data.pages;
			pageNo = data.page || pageNo;
			error = '';
			everLoaded = true;
		} catch (e) {
			if (isAbortError(e) && mine.signal.aborted) return;
			error = formatApiError(e, 'Could not load evaluation tasks.');
		} finally {
			if (ctl === mine) loading = false;
		}
	}

	const queryKey = $derived(JSON.stringify([status, search, pageNo]));
	$effect(() => {
		void queryKey;
		untrack(() => {
			writeUrl();
			void fetchTasks();
		});
	});

	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(value: string) {
		searchText = value;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			search = value.trim();
			pageNo = 1;
		}, 300);
	}

	onDestroy(() => {
		clearTimeout(searchTimer);
		ctl?.abort();
	});

	// ── filters ───────────────────────────────────────────────────────
	const statusItems = [
		{ value: '', label: 'All' },
		{ value: 'draft', label: 'Draft' },
		{ value: 'open', label: 'Open' },
		{ value: 'paused', label: 'Paused' },
		{ value: 'closed', label: 'Closed' },
		{ value: 'archived', label: 'Archived' },
	];

	function setStatus(v: string) {
		status = STATUSES.includes(v as TaskStatus) ? (v as TaskStatus) : '';
		pageNo = 1;
	}

	const anyFilter = $derived(!!(status || search));
	function clearAll() {
		status = '';
		search = '';
		searchText = '';
		pageNo = 1;
	}

	// ── cells ─────────────────────────────────────────────────────────
	function progress(c: TaskCounts): { pct: number | null; text: string; sub: string } {
		const target = c.target_ratings;
		const sub = [
			c.assignments_in_progress ? `${c.assignments_in_progress} in progress` : '',
			c.assignments_skipped ? `${c.assignments_skipped} skipped` : '',
		]
			.filter(Boolean)
			.join(' · ');
		if (target == null || target <= 0) {
			return { pct: null, text: c.assignments_submitted ? `${c.assignments_submitted} submitted` : '—', sub };
		}
		return {
			pct: Math.min(1, c.assignments_submitted / target),
			text: `${c.assignments_submitted} / ${target}`,
			sub,
		};
	}

	function participantsSub(c: TaskCounts): string {
		return [
			c.participants_active ? `${c.participants_active} active` : '',
			c.participants_completed ? `${c.participants_completed} done` : '',
		]
			.filter(Boolean)
			.join(' · ');
	}

	const modeLabel = (m: string) => (m === 'pairwise' ? 'Pairwise' : 'Single app');

	const resultsText = $derived(
		total > 0 ? `${(pageNo - 1) * PER_PAGE + 1}–${Math.min(pageNo * PER_PAGE, total)} of ${total} tasks` : ''
	);
</script>

{#snippet progressCell(c: TaskCounts)}
	{@const p = progress(c)}
	<div class="flex min-w-28 flex-col gap-1">
		<span class="font-mono text-xs tabular-nums {p.pct == null ? 'text-muted-foreground' : ''}">{p.text}</span>
		{#if p.pct != null}
			<span
				class="block h-1 w-full overflow-hidden rounded-full bg-muted"
				role="meter"
				aria-label="Submitted ratings"
				aria-valuemin={0}
				aria-valuemax={c.target_ratings ?? 0}
				aria-valuenow={c.assignments_submitted}
			>
				<span class="block h-full rounded-full bg-muted-foreground/45" style:width="{Math.round(p.pct * 100)}%"></span>
			</span>
		{/if}
		{#if p.sub}<span class="cell-sub text-[11px] text-muted-foreground">{p.sub}</span>{/if}
	</div>
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="Human evaluation"
		description="Manual testing tasks: participants grade generated apps on the evaluation portal, blinded to model and condition."
	>
		{#snippet actions()}
			<Button size="sm" href="/human-eval/new"><Plus />New task</Button>
		{/snippet}
		{#snippet help()}
			<div class="space-y-2 text-sm">
				<p>A <b>task</b> is one study: a pool of generated apps, a design (rubric ratings, requirement checklists, security testing, usability scenarios, questionnaires, bug logs or A/B preferences) and the participants who grade them.</p>
				<p>Drafts can change freely. Opening a task freezes its design and analysis plan; results stay provisional until it is closed.</p>
			</div>
		{/snippet}
		{#if everLoaded}
			<div class="header-stats">
				<span class="header-stat"><b>{total}</b>{status ? `${status} ` : ''}task{total === 1 ? '' : 's'}</span>
			</div>
		{/if}
	</PageHeader>

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="flex flex-wrap items-center gap-2 border-b border-border p-2" role="search" aria-label="Filter evaluation tasks">
			<div class="relative min-w-52 flex-1">
				<Search class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
				<Input
					type="search"
					value={searchText}
					oninput={(e) => onSearch((e.currentTarget as HTMLInputElement).value)}
					placeholder="Search title or description…"
					aria-label="Search evaluation tasks"
					class="h-9 pl-8"
				/>
			</div>
			<div class="max-w-full overflow-x-auto">
				<Segmented size="sm" aria-label="Status" value={status} onValueChange={setStatus} items={statusItems} />
			</div>
		</div>

		<TableToolbar selectedCount={0} noun="task">
			{#snippet summary()}
				{resultsText || (loading ? 'Loading…' : 'No tasks')}
				{#if anyFilter}<button type="button" class="ml-2 cursor-pointer text-muted-foreground hover:text-foreground" onclick={clearAll}>Clear filters</button>{/if}
			{/snippet}
		</TableToolbar>

		{#if error}
			<div class="p-3"><ErrorAlert message={error} onretry={() => fetchTasks()} /></div>
		{:else if loading && tasks.length === 0}
			<LoadingState label="loading evaluation tasks" />
		{:else if tasks.length === 0}
			{#if anyFilter}
				<EmptyState compact icon={ClipboardCheck} title="No tasks match these filters">
					{#snippet action()}<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>{/snippet}
				</EmptyState>
			{:else}
				<EmptyState
					icon={ClipboardCheck}
					title="No evaluation tasks yet"
					description="Start from a preset — app grading, requirements audit, security assessment, usability test or A/B comparison — and pick the generated apps to evaluate."
				>
					{#snippet action()}<Button size="sm" href="/human-eval/new"><Plus />New task</Button>{/snippet}
				</EmptyState>
			{/if}
		{:else}
			<div class="hidden md:block">
				<div class="table-frame transition-opacity {loading ? 'opacity-60' : ''}" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th>Task</th>
								<th>Mode</th>
								<th>Status</th>
								<th class="num">Apps</th>
								<th class="num">Participants</th>
								<th title="Submitted ratings / target (balanced: items × ratings per item; every item: items × participants)">Progress</th>
								<th>Updated</th>
								<th><span class="sr-only">Open</span></th>
							</tr>
						</thead>
						<tbody>
							{#each tasks as task (task.id)}
								{@const href = `/human-eval/${task.id}`}
								<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the title link is the keyboard path) -->
								<tr class="row-link align-top" onclick={rowLink(href)}>
									<td class="max-w-0 min-w-[16rem] w-[34%]">
										<a {href} class="block truncate font-medium hover:text-primary" title={task.title}>{task.title}</a>
										<span class="cell-sub block truncate text-xs text-muted-foreground">
											{task.study ? task.study.name : 'No linked study'}
											{#if task.opens_at || task.closes_at}
												· {task.opens_at ? `from ${formatDate(task.opens_at)}` : ''}{task.opens_at && task.closes_at ? ' ' : ''}{task.closes_at ? `until ${formatDate(task.closes_at)}` : ''}
											{/if}
										</span>
									</td>
									<td class="whitespace-nowrap text-sm">{modeLabel(task.mode)}</td>
									<td><StatusBadge status={task.status} /></td>
									<td class="num">
										{task.counts.active_apps}{#if task.counts.active_apps !== task.counts.apps}<span class="text-muted-foreground">/{task.counts.apps}</span>{/if}
										{#if task.counts.items && task.mode === 'pairwise'}<span class="cell-sub block text-[11px] text-muted-foreground">{task.counts.items} pairs</span>{/if}
									</td>
									<td class="num">
										{task.counts.participants}
										{#if participantsSub(task.counts)}<span class="cell-sub block text-[11px] text-muted-foreground">{participantsSub(task.counts)}</span>{/if}
									</td>
									<td>{@render progressCell(task.counts)}</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(task.updated_at)}</td>
									<td class="w-px whitespace-nowrap text-right">
										<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-foreground" {href} title="Open" aria-label="Open {task.title}">
											<ChevronRight class="size-4" />
										</Button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>

			<!-- Phones: one row per task -->
			<ul class="data-list rounded-none border-0 shadow-none md:hidden">
				{#each tasks as task (task.id)}
					{@const p = progress(task.counts)}
					<li>
						<a href="/human-eval/{task.id}" class="data-list-row block space-y-1.5">
							<span class="block truncate text-sm font-medium">{task.title}</span>
							<span class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
								<StatusBadge status={task.status} />
								<span>{modeLabel(task.mode)}</span>
								<span>· {task.counts.active_apps} apps</span>
								<span>· {task.counts.participants} participants</span>
							</span>
							<span class="block font-mono text-xs tabular-nums text-muted-foreground">
								{p.text}{p.pct != null ? ' ratings' : ''} · updated {formatDate(task.updated_at)}
							</span>
						</a>
					</li>
				{/each}
			</ul>
			<PaginationBar page={pageNo} {pages} onGoToPage={(p) => (pageNo = p)} class="border-t border-border" scrollToCard />
		{/if}
	</Card.Root>
</div>
