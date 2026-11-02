<script lang="ts">
	// One human-evaluation task: header (status, lifecycle actions, preview,
	// duplicate, delete) and seven tabs — Overview, Design, Apps, Participants,
	// Responses, Results, Export. `?tab=` keeps the tab in the URL (+page.ts).
	// Each tab loads its own data; the page owns the TaskDetail and hands tabs
	// `onchange` (replace it) / `onreload` (quiet refetch, e.g. after counts change).
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Tabs from '$lib/components/ui/tabs';
	import { scrollHint } from '$lib/utils/scroll-hint';
	import { formatApiError } from '$lib/api/core';
	import {
		deleteTask,
		duplicateTask,
		getEnvironment,
		getTask,
		humanEvalErrorBody,
		startPreview,
		transitionTask,
		type HumanEvalEnvironment,
		type TaskAction,
		type TaskDetail,
	} from '$lib/api/human-eval';
	import {
		MODE_LABELS,
		TASK_STATUS_HELP,
		TASK_TABS,
		absoluteUrl,
		codeText,
		orderedActions,
		primaryAction,
		taskActionConfirm,
		taskActionLabel,
		type ConfirmCopy,
		type TaskTab,
	} from '$lib/components/human-eval/format';
	import OverviewTab from '$lib/components/human-eval/detail/OverviewTab.svelte';
	import DesignTab from '$lib/components/human-eval/detail/DesignTab.svelte';
	import AppsTab from '$lib/components/human-eval/detail/AppsTab.svelte';
	import ParticipantsTab from '$lib/components/human-eval/detail/ParticipantsTab.svelte';
	import ResponsesTab from '$lib/components/human-eval/detail/ResponsesTab.svelte';
	import ResultsTab from '$lib/components/human-eval/detail/ResultsTab.svelte';
	import ExportTab from '$lib/components/human-eval/detail/ExportTab.svelte';
	import Archive from '@lucide/svelte/icons/archive';
	import ClipboardCheck from '@lucide/svelte/icons/clipboard-check';
	import Copy from '@lucide/svelte/icons/copy';
	import Eye from '@lucide/svelte/icons/eye';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Pause from '@lucide/svelte/icons/pause';
	import Play from '@lucide/svelte/icons/play';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Square from '@lucide/svelte/icons/square';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import type { Component } from 'svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const taskId = $derived(data.id);

	let task = $state<TaskDetail | null>(null);
	let environment = $state<HumanEvalEnvironment | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let notFound = $state(false);

	let loadSeq = 0;
	async function load(id: string, quiet = false) {
		const mine = ++loadSeq;
		if (!quiet) loading = true;
		try {
			const detail = await getTask(id);
			if (mine !== loadSeq) return;
			task = detail;
			loadError = '';
			notFound = false;
		} catch (e) {
			if (mine !== loadSeq) return;
			const status = (e as { httpStatus?: number })?.httpStatus;
			if (status === 404) notFound = true;
			else if (!quiet || !task) loadError = formatApiError(e, 'Could not load the task.');
			else toast.error(formatApiError(e, 'Could not refresh the task.'));
		} finally {
			if (mine === loadSeq) loading = false;
		}
	}

	// Reload when navigating between tasks (the component is reused).
	$effect(() => {
		const id = taskId;
		untrack(() => {
			task = null;
			void load(id);
		});
	});

	onMount(() => {
		getEnvironment()
			.then((env) => (environment = env))
			.catch(() => {});
	});

	function replaceTask(next: TaskDetail) {
		task = next;
	}
	function reload() {
		void load(taskId, true);
	}

	// ── tabs ────────────────────────────────────────────────────────
	const DEFAULT_TAB: TaskTab = 'overview';
	const isDraft = $derived(task?.status === 'draft');
	/** A draft has no assignments yet (preview starts after opening). */
	const disabledTabs = $derived(new Set<TaskTab>(isDraft ? ['responses', 'results', 'export'] : []));
	const tab = $derived<TaskTab>(data.tab ?? DEFAULT_TAB);

	function setTab(next: string) {
		const url = new URL(page.url);
		if (next === DEFAULT_TAB) url.searchParams.delete('tab');
		else url.searchParams.set('tab', next);
		// Sub-state of the previous tab (filters, open sheet) doesn't carry over.
		for (const key of ['assignment', 'view', 'participant', 'app']) url.searchParams.delete(key);
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const tabCount = $derived.by((): Partial<Record<TaskTab, number>> => {
		if (!task) return {};
		return {
			apps: task.counts.apps,
			participants: task.counts.participants,
			responses: task.counts.assignments_submitted,
		};
	});

	// ── lifecycle actions ───────────────────────────────────────────
	type Pending = TaskAction | 'delete' | 'duplicate' | 'preview' | null;
	let pending = $state<Pending>(null);
	let confirmOpen = $state(false);
	let confirmAction = $state<TaskAction | 'delete' | null>(null);
	let confirmCopy = $state<ConfirmCopy>({ title: '', description: '', label: '' });

	const ACTION_ICONS: Record<TaskAction, Component> = {
		open: Play,
		pause: Pause,
		close: Square,
		reopen: RotateCcw,
		archive: Archive,
	};

	const actions = $derived(task ? orderedActions(task.allowed_actions, task.status) : []);
	const primary = $derived(task ? primaryAction(task.allowed_actions, task.status) : null);
	const blockers = $derived(task?.open_blockers ?? []);

	function errorText(e: unknown, fallback: string): string {
		const body = humanEvalErrorBody(e);
		if (body) {
			const details = body.errors.map((x) => (x.path ? `${x.path}: ${x.message}` : x.message));
			return [body.detail, ...details].filter(Boolean).join(' — ');
		}
		return formatApiError(e, fallback);
	}

	/** Unsaved Design-tab edits a first open would discard (describeFreezeLosses text; '' = none). */
	let unsavedFreezeEdits = $state('');

	function ask(action: TaskAction) {
		if (!task) return;
		const copy = taskActionConfirm(action, task, { unsavedFreezeEdits });
		if (!copy) {
			void run(action);
			return;
		}
		confirmAction = action;
		confirmCopy = copy;
		confirmOpen = true;
	}

	function askDelete() {
		if (!task) return;
		confirmAction = 'delete';
		confirmCopy = {
			title: 'Delete this task?',
			description: `“${task.title}” and all of its data are deleted: participants, invites, responses, observations, screenshots, finding verdicts and HTTP logs. Apps the task started are stopped. This cannot be undone.`,
			label: 'Delete task',
			destructive: true,
		};
		confirmOpen = true;
	}

	const DONE: Record<TaskAction, string> = {
		open: 'Task opened',
		pause: 'Task paused',
		close: 'Task closed',
		reopen: 'Task reopened',
		archive: 'Task archived',
	};

	async function run(action: TaskAction) {
		if (!task) return;
		const wasFrozen = !!task.frozen_at;
		pending = action;
		try {
			const next = await transitionTask(task.id, action);
			task = next;
			confirmOpen = false;
			toast.success(action === 'open' && !wasFrozen ? 'Task opened — design frozen' : DONE[action]);
		} catch (e) {
			confirmOpen = false;
			toast.error(errorText(e, `Could not ${action} the task.`));
			void load(taskId, true);
		} finally {
			pending = null;
		}
	}

	async function onConfirm() {
		const action = confirmAction;
		if (!task || !action) return;
		if (action !== 'delete') return run(action);
		pending = 'delete';
		try {
			await deleteTask(task.id);
			confirmOpen = false;
			toast.success('Task deleted');
			await goto('/human-eval');
		} catch (e) {
			confirmOpen = false;
			toast.error(errorText(e, 'Could not delete the task.'));
		} finally {
			pending = null;
		}
	}

	async function duplicate() {
		if (!task) return;
		pending = 'duplicate';
		try {
			const copy = await duplicateTask(task.id);
			toast.success('Duplicated as a new draft', {
				action: { label: 'Open', onClick: () => goto(`/human-eval/${copy.id}`) },
			});
		} catch (e) {
			toast.error(errorText(e, 'Could not duplicate the task.'));
		} finally {
			pending = null;
		}
	}

	/** Opens the portal as the researcher's own preview participant (never counted in results). */
	async function preview() {
		if (!task) return;
		pending = 'preview';
		// Open the tab synchronously (popup blockers), then point it at the portal.
		const win = window.open('about:blank', '_blank');
		try {
			const session = await startPreview(task.id);
			const url = absoluteUrl(window.location.origin, session.portal_path);
			if (win) {
				win.opener = null;
				win.location.href = url;
			} else {
				window.open(url, '_blank', 'noopener');
			}
		} catch (e) {
			win?.close();
			toast.error(errorText(e, 'Could not start the preview.'));
		} finally {
			pending = null;
		}
	}
</script>

{#snippet headerActions()}
	{#if task}
		<Button variant="ghost" size="sm" onclick={askDelete} disabled={pending !== null} title="Delete this task and its data">
			<Trash2 /><span class="max-sm:sr-only">Delete</span>
		</Button>
		<Button variant="outline" size="sm" onclick={duplicate} disabled={pending !== null} title="A new draft with the same design, settings and apps">
			{#if pending === 'duplicate'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Copy />{/if}Duplicate
		</Button>
		{#if !isDraft}
			<Button
				variant="outline"
				size="sm"
				onclick={preview}
				disabled={pending !== null || task.status === 'archived'}
				title="Open the participant portal as yourself; preview data never enters results or exports"
			>
				{#if pending === 'preview'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Eye />{/if}Preview
			</Button>
		{/if}
		{#each actions as action (action)}
			{@const Icon = ACTION_ICONS[action]}
			{@const blocked = action === 'open' && task.status === 'draft' && blockers.length > 0}
			<Button
				size="sm"
				variant={action === primary ? 'default' : 'outline'}
				disabled={pending !== null || blocked}
				title={blocked ? `Can't open yet: ${blockers.map((b) => codeText(b)).join('; ')}` : undefined}
				onclick={() => ask(action)}
			>
				{#if pending === action}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Icon />{/if}
				{taskActionLabel(action, task.status)}
			</Button>
		{/each}
	{/if}
{/snippet}

{#snippet meta()}
	{#if task}
		<span title={TASK_STATUS_HELP[task.status]}><StatusBadge status={task.status} /></span>
		<span class="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">{MODE_LABELS[task.mode]}</span>
	{/if}
{/snippet}

{#snippet nav()}
	<div
		class="scroll-hint no-scrollbar min-w-0 flex-1 overflow-x-auto"
		{@attach scrollHint({ edges: 'both', focusable: false, follow: '[role="tab"][data-state="active"]' })}
	>
		<Tabs.List aria-label="Task sections" class="w-max min-w-full overflow-x-visible">
			{#each TASK_TABS as section (section.value)}
				<Tabs.Trigger value={section.value} disabled={disabledTabs.has(section.value)} class="gap-1.5">
					{section.label}
					{#if tabCount[section.value] != null}
						<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{tabCount[section.value]}</span>
					{/if}
				</Tabs.Trigger>
			{/each}
		</Tabs.List>
	</div>
{/snippet}

<Tabs.Root value={tab} onValueChange={(v) => setTab(v)} class="space-y-4">
	{#if task}
		<PageHeader
			title={task.title}
			description={task.description || undefined}
			back={{ href: '/human-eval', label: 'Human eval' }}
			{meta}
			actions={headerActions}
			{nav}
		>
			<div class="header-stats">
				<span class="header-stat">
					<b>{task.counts.assignments_submitted.toLocaleString()}{task.counts.target_ratings != null ? `/${task.counts.target_ratings.toLocaleString()}` : ''}</b>
					ratings submitted
				</span>
				<span class="header-stat"><b>{task.counts.participants}</b> participants</span>
				<span class="header-stat"><b>{task.counts.active_apps}</b> active apps</span>
				{#if task.counts.items}<span class="header-stat"><b>{task.counts.items}</b> items</span>{/if}
				{#if task.experiment}
					<span class="header-stat">from <a class="settings-link" href="/experiments/{task.experiment.id}">{task.experiment.name}</a></span>
				{/if}
			</div>
		</PageHeader>
	{:else}
		<PageHeader title="Human evaluation task" back={{ href: '/human-eval', label: 'Human eval' }} />
	{/if}

	{#if loading && !task}
		<LoadingState label="loading task" />
	{:else if notFound}
		<EmptyState icon={ClipboardCheck} title="Task not found" description="It was deleted, or it belongs to someone else.">
			{#snippet action()}<Button size="sm" href="/human-eval">All tasks</Button>{/snippet}
		</EmptyState>
	{:else if loadError}
		<ErrorAlert message={loadError} onretry={() => load(taskId)} />
	{:else if task}
		<Tabs.Content value="overview" class="pt-0">
			{#if tab === 'overview'}
				<OverviewTab {task} {environment} />
			{/if}
		</Tabs.Content>
		<Tabs.Content value="design" class="pt-0">
			{#if tab === 'design'}
				<DesignTab {task} {environment} onchange={replaceTask} onunsaved={(text) => (unsavedFreezeEdits = text)} />
			{/if}
		</Tabs.Content>
		<Tabs.Content value="apps" class="pt-0">
			{#if tab === 'apps'}
				<AppsTab {task} {environment} onreload={reload} />
			{/if}
		</Tabs.Content>
		<Tabs.Content value="participants" class="pt-0">
			{#if tab === 'participants'}
				<ParticipantsTab {task} onreload={reload} />
			{/if}
		</Tabs.Content>
		<Tabs.Content value="responses" class="pt-0">
			{#if tab === 'responses'}
				{#if isDraft}
					<EmptyState title="No responses before the task opens" description="Open the task to let participants join; their work appears here." />
				{:else}
					<ResponsesTab {task} />
				{/if}
			{/if}
		</Tabs.Content>
		<Tabs.Content value="results" class="pt-0">
			{#if tab === 'results'}
				{#if isDraft}
					<EmptyState title="No results before the task opens" description="Results are computed from submitted assignments." />
				{:else}
					<ResultsTab {task} />
				{/if}
			{/if}
		</Tabs.Content>
		<Tabs.Content value="export" class="pt-0">
			{#if tab === 'export'}
				{#if isDraft}
					<EmptyState title="Nothing to export yet" description="Exports are available once the task has opened." />
				{:else}
					<ExportTab {task} />
				{/if}
			{/if}
		</Tabs.Content>
	{/if}
</Tabs.Root>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmCopy.title}
	description={confirmCopy.description}
	confirmLabel={confirmCopy.label}
	destructive={confirmCopy.destructive}
	busy={pending !== null}
	{onConfirm}
/>
