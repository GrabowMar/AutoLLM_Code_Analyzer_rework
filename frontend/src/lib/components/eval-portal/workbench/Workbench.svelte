<script lang="ts">
	/**
	 * Workbench — the participant's evaluation of one assignment
	 * (/eval/t/[taskId]/a/[assignmentId]). Desktop: the app viewer on the left
	 * (AppPane) and the block stepper on the right, with an adjustable split;
	 * phones: stacked, apps open in a new tab.
	 *
	 * Live behaviour (not in read-only mode):
	 * - a heartbeat every 30 s while the page is open, visible or not, with the
	 *   visible time since the last beat and the slots on screen / opened in
	 *   another tab (keeps their apps from idling out);
	 * - apps that are not running are started once (ensure) when the page
	 *   opens; while the tab is visible, apps on their way (queued, building,
	 *   starting, stopping) are polled every 3 s. A stopped (idle) app is only
	 *   started again by the participant (Start), so a forgotten tab neither
	 *   restarts apps nor keeps its assignment alive (R3.11);
	 * - answers autosave per block 800 ms after the last change, finding
	 *   verdicts and notes through a second saver; everything pending of both is
	 *   written before submit and skip, when the tab is hidden and on leave
	 *   (with keepalive when the page is going away);
	 * - Submit maps the server's `incomplete` errors onto the blocks.
	 * A 409 `read_only` from any call switches the page to read-only. Nothing
	 * starts once the component is gone (leaving during the first load).
	 *
	 * Props: `taskId`, `assignmentId` (route params).
	 */
	import { onDestroy, onMount } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { MediaQuery } from 'svelte/reactivity';
	import { toast } from 'svelte-sonner';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Info from '@lucide/svelte/icons/info';
	import {
		ensureApp,
		getPortalAssignment,
		saveResponse,
		sendHeartbeat,
		setFindingVerdict,
		skipAssignment,
		submitAssignment,
	} from '$lib/api/human-eval-portal';
	import { apiErrorStatus, formatApiError } from '$lib/api/core';
	import type {
		AssignmentStatus,
		BlockAnswers,
		BugsAnswers,
		FindingVerdictValue,
		HostingInfo,
		Observation,
		PairwiseAnswers,
		PortalAssignment,
		QuestionnaireAnswers,
		RatingAnswers,
		RequirementsAnswers,
		ScenariosAnswers,
		SecurityAnswers,
		SkipReason,
		SubmitResult,
	} from '$lib/api/types/human-eval';
	import { portalTitle } from '$lib/components/eval-portal/portal-title.svelte';
	import Markdown from '$lib/components/eval-portal/Markdown.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import AppPane, { type PaneView } from './AppPane.svelte';
	import BlockCard from './BlockCard.svelte';
	import BugsForm from './BugsForm.svelte';
	import PairwiseForm from './PairwiseForm.svelte';
	import QuestionnaireForm from './QuestionnaireForm.svelte';
	import RatingForm from './RatingForm.svelte';
	import RequirementsForm from './RequirementsForm.svelte';
	import ScenariosForm from './ScenariosForm.svelte';
	import SecurityForm from './SecurityForm.svelte';
	import SkipDialog from './SkipDialog.svelte';
	import SplitHandle from './SplitHandle.svelte';
	import StepNav from './StepNav.svelte';
	import SubmitBar from './SubmitBar.svelte';
	import { BlockAutosave, worstSaveState } from './autosave.svelte';
	import {
		blockCompleteness,
		emptyAnswers,
		errorsByBlock,
		questionnaireQuestions,
		requirementItems,
	} from './completeness';
	import { HEARTBEAT_INTERVAL_MS, VisibleTimeMeter, heartbeatPayload, viewingSlots } from './heartbeat';
	import { HOSTING_POLL_MS, frameDecision, shouldPollHosting, slotsToPoll } from './hosting';
	import {
		STATUS_TEXT,
		afterSubmitText,
		errorCode,
		errorItems,
		isReadOnlyError,
		orderedApps,
		readOnlyText,
		readStorage,
		sendMaybeKeepalive,
		taskHref,
		workbenchError,
		workbenchHref,
		writeStorage,
		type Slot,
	} from './workbench';

	interface Props {
		taskId: string;
		assignmentId: string;
	}

	let { taskId, assignmentId }: Props = $props();

	type Verdicts = Record<string, { verdict: FindingVerdictValue; note: string }>;

	const SPLIT_KEY = 'heval.workbench.split';
	const BLOCK_LABELS: Record<string, string> = {
		rating: 'Ratings',
		requirements: 'Requirements',
		security: 'Security testing',
		scenarios: 'Usability tasks',
		questionnaire: 'Questionnaire',
		bugs: 'Bugs',
		pairwise: 'Comparison',
	};
	const ASSIGNMENT_TONES = { assigned: 'neutral', expired: 'warning', skipped: 'neutral', submitted: 'success' } as const;

	const desktopQuery = new MediaQuery('min-width: 1024px');
	const desktop = $derived(desktopQuery.current);
	const pageOrigin = typeof location === 'undefined' ? '' : location.origin;

	let assignment = $state.raw<PortalAssignment | null>(null);
	let status = $state<AssignmentStatus>('assigned');
	let loading = $state(true);
	let loadError = $state('');
	let loadStatus = $state<number | null>(null);
	let answers = $state.raw<Record<string, BlockAnswers>>({});
	let observations = $state.raw<Observation[]>([]);
	let verdicts = $state.raw<Verdicts>({});
	let hosting = $state.raw<Record<string, HostingInfo>>({});
	let starting = $state.raw<Record<string, boolean>>({});
	let externalSlots = $state.raw<Slot[]>([]);
	let serverErrors = $state.raw<Record<string, string[]>>({});
	let generalErrors = $state.raw<string[]>([]);
	let active = $state(0);
	let localReadOnly = $state(false);
	let sessionEnded = $state(false);
	let submitting = $state(false);
	let skipOpen = $state(false);
	let skipping = $state(false);
	let initialSavedAt = $state<string | null>(null);

	let focusSlot = $state<Slot>('single');
	let sideBySide = $state(false);
	let view = $state<PaneView>('app');
	let consoleOpen = $state(false);
	let split = $state(readSplit());
	let dragging = $state(false);
	let splitBox = $state<HTMLElement | null>(null);
	let stepperTop = $state<HTMLElement | null>(null);
	let stepperBody = $state<HTMLElement | null>(null);
	let sourceTarget = $state.raw<{ path: string; line: number | null; seq: number } | null>(null);
	let sourceSeq = 0;

	/** The component is gone: nothing may start (timers, title) after this. */
	let destroyed = false;
	const loadAbort = new AbortController();
	/** The page is being hidden or closed: saves go out with keepalive. */
	let leaving = false;

	// Answers autosave per block (800 ms debounce); see autosave.svelte.ts.

	const autosave = new BlockAutosave<BlockAnswers>(
		(blockId, value) => sendMaybeKeepalive(leaving, (options) => saveResponse(assignmentId, blockId, value, options)),
		{
			onError: (_blockId, error) => {
				if (isReadOnlyError(error)) void becomeReadOnly();
				else if (apiErrorStatus(error) === 401) endSession();
			},
		}
	);

	// Finding verdicts and notes (SecurityForm → FindingReview), keyed by finding id.
	const verdictSaver = new BlockAutosave<Verdicts[string]>(
		(findingId, entry) =>
			sendMaybeKeepalive(leaving, (options) => setFindingVerdict(assignmentId, findingId, entry, options)),
		{
			onError: (_findingId, error) => {
				if (isReadOnlyError(error)) void becomeReadOnly();
				else if (apiErrorStatus(error) === 401) endSession();
				else toast.error(workbenchError(error, 'Could not save your verdict.'));
			},
		}
	);

	/** Write everything pending of both savers; true when nothing failed. */
	async function flushAll(): Promise<boolean> {
		const [answersSaved, verdictsSaved] = await Promise.all([autosave.flush(), verdictSaver.flush()]);
		return answersSaved && verdictsSaved;
	}

	function stopSaving() {
		autosave.stop();
		verdictSaver.stop();
	}

	const readOnly = $derived(!assignment || assignment.read_only || localReadOnly);
	const apps = $derived(assignment ? orderedApps(assignment.apps) : []);
	const slots = $derived(apps.map((a) => a.slot));
	const slotLabels = $derived(Object.fromEntries(apps.map((a) => [a.slot, a.label])));
	const blocks = $derived(assignment?.blocks ?? []);
	const contextBlocks = $derived(assignment?.context?.blocks ?? {});
	const taskLink = $derived(taskHref(assignment?.task.id ?? taskId));
	const decisions = $derived(
		Object.fromEntries(apps.map((a) => [a.slot, frameDecision(hosting[a.slot]?.url ?? null, pageOrigin)]))
	);
	const statuses = $derived(
		blocks.map((block) =>
			blockCompleteness({
				block,
				answers: answers[block.id],
				context: contextBlocks[block.id],
				slots,
				slotLabels,
				observations,
				findingVerdicts: verdicts,
			})
		)
	);
	const completeCount = $derived(statuses.filter((s) => s.complete).length);
	const steps = $derived(
		blocks.map((b, i) => ({
			id: b.id,
			title: b.title || BLOCK_LABELS[b.type] || b.type,
			complete: statuses[i]?.complete ?? false,
			hasErrors: (serverErrors[b.id]?.length ?? 0) > 0,
		}))
	);
	const activeIndex = $derived(Math.max(0, Math.min(active, blocks.length - 1)));
	/** Slots whose app is framed on screen right now. */
	const framedSlots = $derived.by((): Slot[] => {
		if (!desktop || view !== 'app') return [];
		const shown = apps.length > 1 && sideBySide ? slots : [focusSlot];
		return shown.filter((s) => hosting[s]?.state === 'running' && decisions[s]?.canFrame);
	});
	const viewing = $derived(viewingSlots(framedSlots, externalSlots));
	const savedAt = $derived(
		Math.max(autosave.lastSavedAt ?? 0, verdictSaver.lastSavedAt ?? 0) || initialSavedAt
	);
	const saveState = $derived(worstSaveState(autosave.overall, verdictSaver.overall));

	function setAnswers(blockId: string, next: BlockAnswers) {
		if (readOnly) return;
		answers = { ...answers, [blockId]: next };
		if (serverErrors[blockId]) {
			const rest = { ...serverErrors };
			delete rest[blockId];
			serverErrors = rest;
		}
		autosave.schedule(blockId, next);
	}

	// --- loading ---------------------------------------------------------------------

	function readSplit(): number {
		const raw = Number(readStorage(SPLIT_KEY));
		return Number.isFinite(raw) && raw >= 25 && raw <= 75 ? raw : 58;
	}

	function applyAssignment(a: PortalAssignment) {
		if (destroyed) return;
		assignment = a;
		status = a.status;
		portalTitle.current = a.task.title;
		const next: Record<string, BlockAnswers> = {};
		let newest: string | null = null;
		for (const block of a.blocks) {
			const saved = a.responses?.[block.id];
			next[block.id] = saved?.answers ?? emptyAnswers(block);
			if (saved?.updated_at && (!newest || saved.updated_at > newest)) newest = saved.updated_at;
		}
		answers = next;
		initialSavedAt = newest;
		observations = a.observations ?? [];
		verdicts = a.finding_verdicts ?? {};
		hosting = Object.fromEntries(a.apps.map((app) => [app.slot, app.hosting]));
		if (!a.apps.some((app) => app.slot === focusSlot)) focusSlot = orderedApps(a.apps)[0]?.slot ?? 'single';
		if (a.read_only || localReadOnly) {
			stopSaving();
			stopLive();
		} else if (!sessionEnded) {
			startLive();
		}
	}

	async function load() {
		loading = true;
		loadError = '';
		try {
			const a = await getPortalAssignment(assignmentId, { signal: loadAbort.signal });
			if (destroyed) return;
			applyAssignment(a);
		} catch (e) {
			if (destroyed) return;
			loadStatus = apiErrorStatus(e);
			loadError =
				loadStatus === 404
					? 'This assignment does not exist or belongs to another participant session.'
					: workbenchError(e, 'Could not load this assignment.');
		} finally {
			loading = false;
		}
	}

	async function becomeReadOnly() {
		if (localReadOnly || destroyed) return;
		localReadOnly = true;
		stopSaving();
		stopLive();
		toast.info('This assignment can no longer be changed.');
		try {
			const a = await getPortalAssignment(assignmentId, { signal: loadAbort.signal });
			if (!destroyed) applyAssignment(a);
		} catch {
			// Keep what is on screen, read-only.
		}
	}

	// --- live: heartbeat + hosting -------------------------------------------------

	let live = false;
	let meter: VisibleTimeMeter | null = null;
	let beatTimer: ReturnType<typeof setInterval> | null = null;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	const ensuring = new Set<string>();

	function isVisible(): boolean {
		return typeof document === 'undefined' || document.visibilityState === 'visible';
	}

	function startLive() {
		if (live || destroyed) return;
		live = true;
		meter = new VisibleTimeMeter(Date.now(), isVisible());
		void beat();
		beatTimer = setInterval(() => void beat(), HEARTBEAT_INTERVAL_MS);
		// One-shot on open: starts an idle app (or performs a pending fresh-copy reset).
		for (const app of apps) {
			if (shouldPollHosting(hosting[app.slot]?.state)) void ensure(app.slot, true);
		}
		schedulePoll();
	}

	/** The participant session is gone (401): stop beating and polling. */
	function endSession() {
		sessionEnded = true;
		stopLive();
	}

	function stopLive() {
		live = false;
		if (beatTimer) clearInterval(beatTimer);
		if (pollTimer) clearTimeout(pollTimer);
		beatTimer = null;
		pollTimer = null;
	}

	function setHosting(slot: string, info: HostingInfo) {
		const before = hosting[slot]?.state;
		hosting = { ...hosting, [slot]: info };
		// Announce a start only when the app is not already in view (the frame appears by itself).
		const inView = desktop && view === 'app';
		if (!inView && info.state === 'running' && before && before !== 'running' && shouldPollHosting(before)) {
			const label = slotLabels[slot] ?? 'The app';
			toast.success(desktop ? `${label} is ready` : `${label} is ready — open it in a new tab`);
		}
	}

	async function beat() {
		if (!live || !meter) return;
		const payload = heartbeatPayload(meter, Date.now(), isVisible(), viewing);
		try {
			const result = await sendHeartbeat(assignmentId, payload);
			if (!live) return;
			for (const app of result.apps ?? []) setHosting(app.slot, app.hosting);
			status = result.status;
			if (result.status !== 'assigned' && result.status !== 'in_progress') void becomeReadOnly();
		} catch (e) {
			meter?.restore(payload.active_seconds);
			if (isReadOnlyError(e)) void becomeReadOnly();
			else if (apiErrorStatus(e) === 401) endSession();
		}
	}

	async function ensure(slot: Slot, auto = false) {
		if (readOnly || ensuring.has(slot)) return;
		ensuring.add(slot);
		if (!auto) starting = { ...starting, [slot]: true };
		try {
			const info = await ensureApp(assignmentId, slot);
			if (live) setHosting(slot, info);
		} catch (e) {
			if (isReadOnlyError(e)) void becomeReadOnly();
			else if (apiErrorStatus(e) === 401) endSession();
			else if (!auto) toast.error(workbenchError(e, 'Could not start the app.'));
		} finally {
			ensuring.delete(slot);
			if (!auto) starting = { ...starting, [slot]: false };
		}
	}

	/** Poll apps on their way (never idle ones, never in a hidden tab); see slotsToPoll. */
	function schedulePoll(delay = HOSTING_POLL_MS) {
		if (pollTimer) clearTimeout(pollTimer);
		if (!live) return;
		pollTimer = setTimeout(async () => {
			const waiting = slotsToPoll(slots, hosting, isVisible());
			await Promise.all(waiting.map((slot) => ensure(slot, true)));
			schedulePoll();
		}, delay);
	}

	function openedExternally(slot: Slot) {
		externalSlots = viewingSlots(externalSlots, [slot]);
	}

	// --- page lifecycle ---------------------------------------------------------------

	function onVisibility() {
		const visible = isVisible();
		meter?.setVisible(visible, Date.now());
		leaving = !visible;
		if (!visible) void flushAll();
		else if (live) schedulePoll(0); // catch up on what changed while hidden
	}

	function onBeforeUnload(event: BeforeUnloadEvent) {
		if (readOnly || (!autosave.busy && !verdictSaver.busy)) return;
		leaving = true;
		void flushAll();
		event.preventDefault();
		event.returnValue = '';
	}

	/** Browsers that skip beforeunload (phones) still fire pagehide. */
	function onPageHide() {
		leaving = true;
		if (!readOnly) void flushAll();
	}

	beforeNavigate(() => {
		if (!readOnly) void flushAll();
	});

	onMount(() => {
		void load();
	});

	onDestroy(() => {
		destroyed = true;
		loadAbort.abort();
		stopLive();
		void flushAll();
	});

	// --- stepper ------------------------------------------------------------------------

	function select(index: number) {
		active = Math.max(0, Math.min(index, blocks.length - 1));
		if (desktop) stepperBody?.scrollTo({ top: 0 });
		else stepperTop?.scrollIntoView({ block: 'start' });
	}

	function openSource(slot: Slot, path: string, line: number | null) {
		focusSlot = slot;
		view = 'source';
		sourceTarget = { path, line, seq: ++sourceSeq };
	}

	function canOpenSource(slot: Slot): boolean {
		return apps.find((a) => a.slot === slot)?.has_source ?? false;
	}

	function onObservationSaved(obs: Observation) {
		const exists = observations.some((o) => o.id === obs.id);
		observations = exists ? observations.map((o) => (o.id === obs.id ? obs : o)) : [...observations, obs];
		if (obs.kind === 'bug') {
			// A bug report contradicts "I found no bugs" for that app.
			for (const block of blocks) {
				if (block.type !== 'bugs') continue;
				const current = answers[block.id] as BugsAnswers | undefined;
				if (current?.none_found?.[obs.app_slot]) {
					setAnswers(block.id, { ...current, none_found: { ...current.none_found, [obs.app_slot]: false } });
				}
			}
		}
	}

	function onObservationDeleted(id: string) {
		observations = observations.filter((o) => o.id !== id);
	}

	function onVerdict(findingId: string, entry: Verdicts[string]) {
		verdicts = { ...verdicts, [findingId]: entry };
	}

	// --- submit / skip ------------------------------------------------------------------

	async function afterDone(result: SubmitResult, verb: string) {
		stopSaving();
		stopLive();
		const tid = assignment?.task.id ?? taskId;
		const nextId = result.next?.assignment_id;
		if (nextId && nextId !== assignmentId) {
			toast.success(`${verb}. Here is the next app.`, {
				description: 'To stop here instead, go back to the study page and finish.',
			});
			await goto(workbenchHref(tid, nextId));
			return;
		}
		const text = afterSubmitText(result.next?.reason);
		toast.success(text ? `${verb}. ${text}` : `${verb}.`);
		await goto(taskHref(tid));
	}

	async function submit() {
		if (readOnly || submitting) return;
		submitting = true;
		try {
			if (!(await flushAll())) {
				toast.error('Some answers could not be saved. Retry saving, then submit.');
				return;
			}
			const result = await submitAssignment(assignmentId);
			serverErrors = {};
			generalErrors = [];
			await afterDone(result, 'Submitted');
		} catch (e) {
			if (errorCode(e) === 'incomplete') {
				const mapped = errorsByBlock(errorItems(e), blocks.map((b) => b.id));
				serverErrors = mapped.byBlock;
				generalErrors = mapped.general;
				const first = blocks.findIndex((b) => (mapped.byBlock[b.id]?.length ?? 0) > 0);
				if (first >= 0) select(first);
				toast.error(formatApiError(e, 'Some sections are not complete yet.'));
			} else if (isReadOnlyError(e)) {
				void becomeReadOnly();
			} else {
				toast.error(workbenchError(e, 'Could not submit.'));
			}
		} finally {
			submitting = false;
		}
	}

	async function skip(reason: SkipReason, note: string) {
		if (readOnly || skipping) return;
		skipping = true;
		try {
			await flushAll();
			const result = await skipAssignment(assignmentId, { reason, note });
			skipOpen = false;
			await afterDone(result, 'Skipped');
		} catch (e) {
			if (isReadOnlyError(e)) {
				skipOpen = false;
				void becomeReadOnly();
			} else {
				toast.error(workbenchError(e, 'Could not skip this app.'));
			}
		} finally {
			skipping = false;
		}
	}
</script>

<svelte:window onbeforeunload={onBeforeUnload} onpagehide={onPageHide} />
<svelte:document onvisibilitychange={onVisibility} />

{#if loading && !assignment}
	<LoadingState label="loading the app to evaluate" />
{:else if !assignment}
	<div class="mx-auto max-w-lg space-y-3 py-10">
		<ErrorAlert
			title={loadStatus === 404 ? 'Assignment not found' : undefined}
			message={loadError || 'Could not load this assignment.'}
			onretry={loadStatus === 404 || loadStatus === 401 ? undefined : load}
		/>
		<div class="flex flex-wrap gap-2">
			{#if loadStatus === 401}
				<Button size="sm" href="/eval">Enter your access code</Button>
			{:else}
				<Button size="sm" variant="outline" href={taskHref(taskId)}>
					<ArrowLeft />
					Back to the study
				</Button>
			{/if}
		</div>
	</div>
{:else}
	<div
		bind:this={splitBox}
		class={cn(desktop ? 'flex h-[calc(100dvh-5.5rem)] min-h-[34rem]' : 'space-y-3', dragging && 'select-none')}
	>
		<div class={desktop ? 'min-h-0 min-w-0 shrink-0' : ''} style={desktop ? `width: ${split}%` : undefined}>
			<AppPane
				assignmentId={assignment.id}
				{apps}
				{hosting}
				{starting}
				{decisions}
				{desktop}
				{readOnly}
				consoleEnabled={assignment.http_console}
				{dragging}
				{sourceTarget}
				bind:focusSlot
				bind:sideBySide
				bind:view
				bind:consoleOpen
				onstart={(slot) => ensure(slot)}
				onopen={openedExternally}
				onreadonly={becomeReadOnly}
			/>
		</div>

		{#if desktop}
			<SplitHandle bind:value={split} container={splitBox} bind:dragging onchange={(v) => writeStorage(SPLIT_KEY, String(v))} />
		{/if}

		<section
			bind:this={stepperTop}
			class={cn(
				'flex min-w-0 flex-col rounded-md border border-border bg-background shadow-[var(--shadow-card)]',
				desktop ? 'min-h-0 flex-1 overflow-hidden' : ''
			)}
			aria-label="Evaluation form"
		>
			<header class="space-y-2 border-b border-border bg-card px-3 py-2.5">
				<div class="flex flex-wrap items-center gap-2">
					<Button variant="ghost" size="sm" href={taskLink} class="-ml-1 h-7 px-2 text-xs">
						<ArrowLeft class="size-3.5" />
						Study page
					</Button>
					<span class="font-mono text-[11px] text-muted-foreground tabular-nums">Item {assignment.position}</span>
					<StatusBadge status={status} tones={ASSIGNMENT_TONES} label={STATUS_TEXT[status] ?? status} />
				</div>
				{#if blocks.length > 0}
					<StepNav {steps} active={activeIndex} onselect={select} />
				{/if}
			</header>

			<div bind:this={stepperBody} class={cn('space-y-3 p-3', desktop && 'min-h-0 flex-1 overflow-y-auto')}>
				{#if sessionEnded}
					<div role="alert" class="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning-fg">
						<span>Your session has ended, so changes are not being saved.</span>
						<Button size="sm" variant="outline" href="/eval">Enter your access code</Button>
					</div>
				{/if}
				{#if readOnly}
					<div class="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface-2 px-3 py-2 text-sm">
						<span class="flex items-start gap-2">
							<Info class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
							{readOnlyText(status)}
						</span>
						<Button size="sm" variant="outline" href={taskLink}>Back to the study</Button>
					</div>
				{/if}
				{#if generalErrors.length > 0}
					<ErrorAlert title="Not submitted" message={generalErrors.join(' ')} />
				{/if}
				{#if assignment.task.instructions.trim()}
					<details class="rounded-md border border-border bg-card">
						<summary class="cursor-pointer px-3 py-2 text-sm font-medium">Study instructions</summary>
						<div class="border-t border-border px-3 py-3">
							<Markdown source={assignment.task.instructions} />
						</div>
					</details>
				{/if}

				{#if blocks.length === 0}
					<EmptyState title="Nothing to fill in" description="This study has no questions for this app. You can submit it." />
				{/if}
				{#each blocks as block, i (block.id)}
					{@const blockStatus = statuses[i]}
					{@const blockAnswers = answers[block.id] ?? emptyAnswers(block)}
					{@const context = contextBlocks[block.id]}
					<div hidden={i !== activeIndex}>
						<BlockCard
							number={i + 1}
							title={steps[i].title}
							instructions={block.instructions}
							status={blockStatus}
							serverErrors={serverErrors[block.id]}
							{readOnly}
						>
							{#if block.type === 'rating'}
								<RatingForm {block} answers={blockAnswers as RatingAnswers} {readOnly} onchange={(next) => setAnswers(block.id, next)} />
							{:else if block.type === 'requirements'}
								<RequirementsForm
									{block}
									answers={blockAnswers as RequirementsAnswers}
									items={requirementItems(context, slots)}
									{readOnly}
									onchange={(next) => setAnswers(block.id, next)}
								/>
							{:else if block.type === 'security'}
								<SecurityForm
									assignmentId={assignment.id}
									{block}
									answers={blockAnswers as SecurityAnswers}
									{context}
									{apps}
									{observations}
									{verdicts}
									{verdictSaver}
									{readOnly}
									consoleAvailable={block.http_console && assignment.http_console}
									onchange={(next) => setAnswers(block.id, next)}
									onobservationsaved={onObservationSaved}
									onobservationdeleted={onObservationDeleted}
									onverdict={onVerdict}
									{canOpenSource}
									onopensource={openSource}
								/>
							{:else if block.type === 'scenarios'}
								<ScenariosForm {block} answers={blockAnswers as ScenariosAnswers} {readOnly} onchange={(next) => setAnswers(block.id, next)} />
							{:else if block.type === 'questionnaire'}
								<QuestionnaireForm
									{block}
									answers={blockAnswers as QuestionnaireAnswers}
									questions={questionnaireQuestions(block, context)}
									{readOnly}
									onchange={(next) => setAnswers(block.id, next)}
								/>
							{:else if block.type === 'bugs'}
								<BugsForm
									assignmentId={assignment.id}
									{block}
									answers={blockAnswers as BugsAnswers}
									{apps}
									{observations}
									{readOnly}
									onchange={(next) => setAnswers(block.id, next)}
									onobservationsaved={onObservationSaved}
									onobservationdeleted={onObservationDeleted}
								/>
							{:else if block.type === 'pairwise'}
								<PairwiseForm {block} answers={blockAnswers as PairwiseAnswers} {apps} {readOnly} onchange={(next) => setAnswers(block.id, next)} />
							{/if}
						</BlockCard>
					</div>
				{/each}
			</div>

			<div class={cn(!desktop && 'sticky bottom-0 z-20')}>
				<SubmitBar
					index={activeIndex}
					count={Math.max(blocks.length, 1)}
					{saveState}
					{savedAt}
					{completeCount}
					{readOnly}
					allowSkip={assignment.allow_skip}
					submitting={submitting || skipping}
					onprev={() => select(activeIndex - 1)}
					onnext={() => select(activeIndex + 1)}
					onretry={() => void flushAll()}
					onskip={() => (skipOpen = true)}
					onsubmit={submit}
				/>
			</div>
		</section>
	</div>

	{#if assignment.allow_skip && !readOnly}
		<SkipDialog bind:open={skipOpen} {apps} {hosting} busy={skipping} onconfirm={skip} />
	{/if}
{/if}
