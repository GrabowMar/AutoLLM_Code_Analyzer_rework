<script lang="ts">
	/**
	 * ResponsesTab — the collected data, three views (`?view=`):
	 * - Assignments (default): filter by participant (searched on the server:
	 *   ParticipantFilter), app and status; each row opens the
	 *   AssignmentDetailSheet (`?assignment=<id>` deep-links it).
	 * - Observations: every bug / vulnerability / note across the task
	 *   (tester's own severity), with screenshots; filter by kind, app, participant.
	 * - HTTP log: every HTTP-console request; filter by participant and app.
	 *
	 * Props: `task`.
	 */
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Segmented } from '$lib/components/ui/segmented';
	import { TableToolbar, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		listAssignments,
		listHttpLog,
		listObservations,
		listTaskApps,
		type AssignmentStatus,
		type AssignmentSummary,
		type HttpExchangeRecord,
		type ObservationKind,
		type ResearcherObservation,
		type TaskDetail,
	} from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { formatSeconds, plural, skipReasonLabel } from '../format';
	import AssignmentDetailSheet from './AssignmentDetailSheet.svelte';
	import FlagList from './FlagList.svelte';
	import HttpExchangeList from './HttpExchangeList.svelte';
	import ObservationCard from './ObservationCard.svelte';
	import ParticipantFilter from './ParticipantFilter.svelte';
	import ClipboardCheck from '@lucide/svelte/icons/clipboard-check';
	import Globe from '@lucide/svelte/icons/globe';
	import MessageSquare from '@lucide/svelte/icons/message-square';

	let { task }: { task: TaskDetail } = $props();

	type View = 'assignments' | 'observations' | 'http';
	const params = $derived(page.url.searchParams);
	const view = $derived<View>(
		params.get('view') === 'observations' ? 'observations' : params.get('view') === 'http' ? 'http' : 'assignments'
	);
	const hasConsole = $derived(task.design.blocks.some((b) => b.type === 'security' && b.http_console));

	function setParams(next: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(next)) {
			if (value == null || value === '') url.searchParams.delete(key);
			else url.searchParams.set(key, value);
		}
		try {
			goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
		} catch {
			// Router not ready yet.
		}
	}

	let viewValue = $state(untrack(() => view as string));
	$effect(() => {
		viewValue = view;
	});

	// ── the sheet (URL-synced) ──────────────────────────────────────
	let sheetId = $state<string | null>(untrack(() => page.url.searchParams.get('assignment')));
	$effect(() => {
		const fromUrl = params.get('assignment');
		untrack(() => {
			if (fromUrl !== sheetId) sheetId = fromUrl;
		});
	});
	$effect(() => {
		const id = sheetId;
		untrack(() => {
			if ((params.get('assignment') ?? null) !== id) setParams({ assignment: id });
		});
	});
	function openAssignment(id: string) {
		sheetId = id;
	}

	// ── filter options (apps; participants are searched by ParticipantFilter) ──
	let appItems = $state<{ value: string; label: string; description?: string }[]>([]);
	onMount(() => {
		listTaskApps(task.id)
			.then((apps) => {
				appItems = apps.map((a) => ({ value: a.id, label: a.label, description: a.model_name || undefined }));
			})
			.catch(() => {});
	});

	let participantId = $state(untrack(() => page.url.searchParams.get('participant') ?? ''));
	let appId = $state(untrack(() => page.url.searchParams.get('app') ?? ''));
	let status = $state('');
	let obsKind = $state('');

	// ── assignments ─────────────────────────────────────────────────
	let aPage = $state(1);
	let assignments = $state.raw<AssignmentSummary[]>([]);
	let aTotal = $state(0);
	let aPages = $state(1);
	let aLoading = $state(true);
	let aError = $state('');
	let aCtl: AbortController | null = null;

	async function loadAssignments(quiet = false) {
		aCtl?.abort();
		const mine = new AbortController();
		aCtl = mine;
		if (!quiet) aLoading = true;
		try {
			const res = await listAssignments(
				task.id,
				{
					participant_id: participantId || undefined,
					app_id: appId || undefined,
					status: (status || undefined) as AssignmentStatus | undefined,
					page: aPage,
					per_page: 50,
				},
				{ signal: mine.signal }
			);
			assignments = res.items;
			aTotal = res.total;
			aPages = res.pages;
			aError = '';
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			aError = formatApiError(e, 'Could not load assignments.');
		} finally {
			if (aCtl === mine) aLoading = false;
		}
	}

	// ── observations ────────────────────────────────────────────────
	let oPage = $state(1);
	let observations = $state.raw<ResearcherObservation[]>([]);
	let oTotal = $state(0);
	let oPages = $state(1);
	let oLoading = $state(true);
	let oError = $state('');
	let oCtl: AbortController | null = null;

	async function loadObservations() {
		oCtl?.abort();
		const mine = new AbortController();
		oCtl = mine;
		oLoading = true;
		try {
			const res = await listObservations(
				task.id,
				{
					kind: (obsKind || undefined) as ObservationKind | undefined,
					app_id: appId || undefined,
					participant_id: participantId || undefined,
					page: oPage,
					per_page: 25,
				},
				{ signal: mine.signal }
			);
			observations = res.items;
			oTotal = res.total;
			oPages = res.pages;
			oError = '';
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			oError = formatApiError(e, 'Could not load observations.');
		} finally {
			if (oCtl === mine) oLoading = false;
		}
	}

	// ── HTTP log ────────────────────────────────────────────────────
	let hPage = $state(1);
	let exchanges = $state.raw<HttpExchangeRecord[]>([]);
	let hTotal = $state(0);
	let hPages = $state(1);
	let hLoading = $state(true);
	let hError = $state('');
	let hCtl: AbortController | null = null;

	async function loadHttp() {
		hCtl?.abort();
		const mine = new AbortController();
		hCtl = mine;
		hLoading = true;
		try {
			const res = await listHttpLog(
				task.id,
				{ participant_id: participantId || undefined, app_id: appId || undefined, page: hPage, per_page: 50 },
				{ signal: mine.signal }
			);
			exchanges = res.items;
			hTotal = res.total;
			hPages = res.pages;
			hError = '';
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			hError = formatApiError(e, 'Could not load the HTTP log.');
		} finally {
			if (hCtl === mine) hLoading = false;
		}
	}

	const aKey = $derived(JSON.stringify([task.id, participantId, appId, status, aPage]));
	const oKey = $derived(JSON.stringify([task.id, participantId, appId, obsKind, oPage]));
	const hKey = $derived(JSON.stringify([task.id, participantId, appId, hPage]));
	$effect(() => {
		if (view !== 'assignments') return;
		void aKey;
		untrack(() => void loadAssignments(assignments.length > 0));
	});
	$effect(() => {
		if (view !== 'observations') return;
		void oKey;
		untrack(() => void loadObservations());
	});
	$effect(() => {
		if (view !== 'http') return;
		void hKey;
		untrack(() => void loadHttp());
	});

	function resetPages() {
		aPage = 1;
		oPage = 1;
		hPage = 1;
	}

	function onSummaryChanged(summary: AssignmentSummary) {
		assignments = assignments.map((a) => (a.id === summary.id ? { ...a, ...summary } : a));
	}

	const STATUS_ITEMS = [
		{ value: 'assigned', label: 'Assigned' },
		{ value: 'in_progress', label: 'In progress' },
		{ value: 'submitted', label: 'Submitted' },
		{ value: 'skipped', label: 'Skipped' },
		{ value: 'expired', label: 'Expired' },
	];
	const KIND_ITEMS = [
		{ value: 'bug', label: 'Bugs' },
		{ value: 'vulnerability', label: 'Vulnerabilities' },
		{ value: 'note', label: 'Notes' },
	];
	const VIEW_ITEMS = $derived([
		{ value: 'assignments', label: 'Assignments', icon: ClipboardCheck },
		{ value: 'observations', label: 'Observations', icon: MessageSquare },
		{ value: 'http', label: 'HTTP log', icon: Globe, disabled: !hasConsole, title: hasConsole ? undefined : 'No security block with the HTTP console' },
	]);
	const filtered = $derived(!!(participantId || appId || status));
	/** The filtered participant's label from the rows already loaded (a deep link names only the id). */
	const participantLabel = $derived(
		participantId
			? (assignments.find((a) => a.participant.id === participantId)?.participant.label ??
					observations.find((o) => o.participant.id === participantId)?.participant.label ??
					'')
			: ''
	);
	const checklistLabel = (id: string) => {
		for (const b of task.design.blocks) {
			if (b.type === 'security') {
				const item = b.checklist.find((c) => c.id === id);
				if (item) return item.label;
			}
		}
		return id;
	};
</script>

<div class="space-y-4">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<Segmented aria-label="Responses view" size="sm" items={VIEW_ITEMS} bind:value={viewValue} onValueChange={(v) => setParams({ view: v === 'assignments' ? null : v })} />
	</div>

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="flex flex-wrap items-center gap-2 border-b border-border p-2" role="search">
			<ParticipantFilter taskId={task.id} bind:value={participantId} fallbackLabel={participantLabel} onValueChange={resetPages} />
			<Select.Simple size="sm" class="w-40" aria-label="App" placeholder="Any app" clearLabel="Any app" items={appItems} bind:value={appId} onValueChange={resetPages} />
			{#if view === 'assignments'}
				<Select.Simple size="sm" class="w-36" aria-label="Status" placeholder="Any status" clearLabel="Any status" items={STATUS_ITEMS} bind:value={status} onValueChange={resetPages} />
			{:else if view === 'observations'}
				<Select.Simple size="sm" class="w-40" aria-label="Kind" placeholder="Any kind" clearLabel="Any kind" items={KIND_ITEMS} bind:value={obsKind} onValueChange={resetPages} />
			{/if}
		</div>

		{#if view === 'assignments'}
			<TableToolbar>
				{#snippet summary()}
					{plural(aTotal, 'assignment')}{filtered ? ' match' : ''} · click a row to see the answers
				{/snippet}
			</TableToolbar>
			{#if aError}
				<div class="p-3"><ErrorAlert message={aError} onretry={() => loadAssignments()} /></div>
			{:else if aLoading && assignments.length === 0}
				<LoadingState label="loading assignments" />
			{:else if assignments.length === 0}
				<EmptyState compact icon={ClipboardCheck} title={filtered ? 'No assignments match' : 'No assignments yet'} description={filtered ? 'Change the filters.' : 'Assignments appear when participants start items.'} />
			{:else}
				<div class="table-frame" {@attach tableFrame}>
					<table class="min-w-[62rem]">
						<thead>
							<tr>
								<th class="col-sticky">Participant</th>
								<th>{task.mode === 'pairwise' ? 'Apps (A · B as shown)' : 'App'}</th>
								<th>Status</th>
								<th class="num">#</th>
								<th>Submitted</th>
								<th class="num" title="Active (visible tab) / wall-clock time">Time</th>
								<th class="num" title="Observations / HTTP requests">Obs · HTTP</th>
								<th>Flags</th>
							</tr>
						</thead>
						<tbody>
							{#each assignments as a (a.id)}
								<tr
									class={cn('row-link', a.excluded && 'bg-muted/30')}
									data-selected={sheetId === a.id}
									onclick={(e) => {
										if ((e.target as HTMLElement | null)?.closest('a, button')) return;
										openAssignment(a.id);
									}}
								>
									<td class="col-sticky">
										<button type="button" class="cursor-pointer font-mono font-medium hover:text-primary" onclick={() => openAssignment(a.id)}>{a.participant.label}</button>
										{#if a.participant.kind === 'preview'}<span class="ml-1 text-[11px] text-muted-foreground">preview</span>{/if}
									</td>
									<td class="text-xs">
										{a.apps.map((x) => (x.slot === 'single' ? x.label : `${x.slot}: ${x.label}`)).join(' · ')}
									</td>
									<td>
										<StatusBadge status={a.status} />
										{#if a.excluded}<span class="ml-1 text-[11px] font-medium text-danger-fg" title={a.exclusion_reason}>excluded</span>{/if}
										{#if a.status === 'skipped' && a.skip_reason}<span class="cell-sub block text-[11px] text-muted-foreground">{skipReasonLabel(a.skip_reason)}</span>{/if}
									</td>
									<td class="num text-xs text-muted-foreground">{a.position}</td>
									<td class="whitespace-nowrap text-xs">{a.submitted_at ? formatDateTime(a.submitted_at) : '—'}</td>
									<td class="num text-xs">{formatSeconds(a.active_seconds)}<span class="cell-sub block text-muted-foreground">{formatSeconds(a.wall_seconds)} wall</span></td>
									<td class="num text-xs">{a.observation_count}<span class="text-muted-foreground"> · {a.http_request_count}</span></td>
									<td class="max-w-56"><FlagList flags={a.quality_flags} /></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<PaginationBar page={aPage} pages={aPages} onGoToPage={(n) => (aPage = n)} class="border-t border-border" scrollToCard />
			{/if}
		{:else if view === 'observations'}
			<TableToolbar density={false}>
				{#snippet summary()}{plural(oTotal, 'observation')} · severities are the testers’ own{/snippet}
			</TableToolbar>
			<div class="space-y-3 p-3">
				{#if oError}
					<ErrorAlert message={oError} onretry={loadObservations} />
				{:else if oLoading && observations.length === 0}
					<LoadingState label="loading observations" />
				{:else if observations.length === 0}
					<EmptyState compact icon={MessageSquare} title="No observations" description="Bug, vulnerability and note reports appear here." />
				{:else}
					{#each observations as o (o.id)}
						<ObservationCard observation={o} appLabel={o.app_label} who={o.participant.label} onopen={() => openAssignment(o.assignment_id)} {checklistLabel} />
					{/each}
				{/if}
			</div>
			{#if observations.length > 0}
				<PaginationBar page={oPage} pages={oPages} onGoToPage={(n) => (oPage = n)} class="border-t border-border" scrollToCard />
			{/if}
		{:else}
			<TableToolbar density={false}>
				{#snippet summary()}{plural(hTotal, 'request')} through the HTTP console · newest first{/snippet}
			</TableToolbar>
			<div class="p-3">
				{#if hError}
					<ErrorAlert message={hError} onretry={loadHttp} />
				{:else if hLoading && exchanges.length === 0}
					<LoadingState label="loading requests" />
				{:else if exchanges.length === 0}
					<EmptyState compact icon={Globe} title="No requests" description="Requests participants send through the HTTP console appear here." />
				{:else}
					<HttpExchangeList {exchanges} showWho />
				{/if}
			</div>
			{#if exchanges.length > 0}
				<PaginationBar page={hPage} pages={hPages} onGoToPage={(n) => (hPage = n)} class="border-t border-border" scrollToCard />
			{/if}
		{/if}
	</Card.Root>
</div>

<AssignmentDetailSheet {task} bind:assignmentId={sheetId} onchanged={onSummaryChanged} />
