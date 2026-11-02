<script lang="ts">
	// A participant's task page: instructions, consent (if not yet given), the
	// "about you" pre-questionnaire, progress with "Start next app" / "Continue
	// current app" (→ workbench), Finish (post-questionnaire, then the completion
	// code) and Withdraw (optionally deleting everything given). Researcher
	// previews get a banner and no withdraw. Sessions that ended (401) or tasks the
	// caller is not part of (404) get the access-code form. Finish is open while an
	// app is assigned: the server releases it when nothing was saved on it, and
	// says so (409 assignment_open) when something was.
	import { onMount, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Eye from '@lucide/svelte/icons/eye';
	import Flag from '@lucide/svelte/icons/flag';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import LogIn from '@lucide/svelte/icons/log-in';
	import Play from '@lucide/svelte/icons/play';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { toast } from 'svelte-sonner';
	import { apiErrorStatus } from '$lib/api/core';
	import {
		finishTask,
		getPortalTask,
		giveConsent,
		requestNext,
		savePostAnswers,
		savePreAnswers,
		withdrawFromTask,
	} from '$lib/api/human-eval-portal';
	import type { NextReason, PortalTask, QuestionAnswer } from '$lib/api/types/human-eval';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import AccessCodeForm from '$lib/components/eval-portal/AccessCodeForm.svelte';
	import CodeDisplay from '$lib/components/eval-portal/CodeDisplay.svelte';
	import ConsentPanel from '$lib/components/eval-portal/ConsentPanel.svelte';
	import Markdown from '$lib/components/eval-portal/Markdown.svelte';
	import ProgressBar from '$lib/components/eval-portal/ProgressBar.svelte';
	import QuestionList from '$lib/components/eval-portal/QuestionList.svelte';
	import WithdrawDialog from '$lib/components/eval-portal/WithdrawDialog.svelte';
	import { portalTitle } from '$lib/components/eval-portal/portal-title.svelte';
	import { getPortalSession } from '$lib/components/eval-portal/portal-session.svelte';
	import {
		PORTAL_HOME,
		PORTAL_TONES,
		blockTypeText,
		errorCode,
		finishConfirmText,
		finishGate,
		formatDateTime,
		nextReasonNotice,
		participantStatusText,
		portalErrorMessage,
		preStepDone,
		progressNumbers,
		researcherTaskPath,
		taskPath,
		workbenchPath,
		type Notice,
	} from '$lib/components/eval-portal/portal';
	import {
		answerTally,
		cleanAnswers,
		errorList,
		mapQuestionErrors,
		validateQuestions,
	} from '$lib/components/eval-portal/questions';
	import { clearConsoleStorage, isReadOnlyError } from '$lib/components/eval-portal/workbench/workbench';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const session = getPortalSession();
	const taskId = $derived(data.taskId);

	// --- loading -------------------------------------------------------------------
	let task = $state<PortalTask | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let loadStatus = $state<number | null>(null);
	let loadedId: string | null = null;
	let loadSeq = 0;

	// --- step state --------------------------------------------------------------------
	let consentAgreed = $state(false);
	let consentBusy = $state(false);
	let consentError = $state('');

	let preAnswers = $state<Record<string, QuestionAnswer>>({});
	let preDirty = $state(false);
	let preEditing = $state(false);
	let preErrors = $state<Record<string, string>>({});
	let preOtherErrors = $state<string[]>([]);
	let preBusy = $state(false);
	/** Saved during this visit (an all-optional questionnaire left blank is stored as {}). */
	let preSubmitted = $state(false);
	/** The server refused a change: an assignment exists (an expired one counts too). */
	let preLocked = $state(false);

	let nextBusy = $state(false);
	let nextError = $state('');
	let nextNotice = $state<Notice | null>(null);
	let lastReason = $state<NextReason | null>(null);

	let finishOpen = $state(false);
	let confirmFinish = $state(false);
	let postAnswers = $state<Record<string, QuestionAnswer>>({});
	let postDirty = $state(false);
	let postErrors = $state<Record<string, string>>({});
	let postOtherErrors = $state<string[]>([]);
	let finishBusy = $state(false);
	let finishError = $state('');
	/** Finish was refused because the open app has saved work (409 assignment_open). */
	let finishBlocked = $state(false);

	let withdrawOpen = $state(false);
	let withdrawBusy = $state(false);
	let withdrawError = $state('');
	let withdrawn = $state<{ deleteData: boolean; title: string } | null>(null);

	function applyTask(next: PortalTask) {
		task = next;
		portalTitle.current = next.title;
		if (!preDirty) preAnswers = { ...next.pre_answers };
		if (!postDirty) postAnswers = { ...next.post_answers };
	}

	async function load(quiet = false) {
		const id = taskId;
		const mine = ++loadSeq;
		if (!quiet) loading = true;
		try {
			const next = await getPortalTask(id);
			if (mine !== loadSeq) return;
			applyTask(next);
			loadError = '';
			loadStatus = null;
		} catch (e) {
			if (mine !== loadSeq) return;
			// A failed background refresh keeps the page as it is.
			if (quiet && task && apiErrorStatus(e) !== 401) return;
			task = null;
			loadStatus = apiErrorStatus(e);
			loadError = portalErrorMessage(e, 'Could not load this evaluation.');
			portalTitle.current = '';
		} finally {
			if (mine === loadSeq) loading = false;
		}
	}

	$effect(() => {
		const id = taskId;
		untrack(() => {
			if (id === loadedId) return;
			loadedId = id;
			task = null;
			withdrawn = null;
			preDirty = false;
			postDirty = false;
			preEditing = false;
			preSubmitted = false;
			preLocked = false;
			finishOpen = false;
			finishBlocked = false;
			nextNotice = null;
			lastReason = null;
			void load();
		});
	});

	onMount(() => {
		// Progress changes in the workbench (possibly another tab): refresh on return.
		const onVisible = () => {
			if (document.visibilityState === 'visible' && task && !withdrawn) void load(true);
		};
		document.addEventListener('visibilitychange', onVisible);
		return () => document.removeEventListener('visibilitychange', onVisible);
	});

	// --- derived ----------------------------------------------------------------------
	const participant = $derived(task?.participant ?? null);
	const isPreview = $derived(participant?.kind === 'preview');
	const isCompleted = $derived(participant?.status === 'completed');
	const isWithdrawn = $derived(participant?.status === 'withdrawn');
	const consentPending = $derived(!!task?.consent.enabled && !participant?.consent_at);
	const preQuestions = $derived(task?.pre_questions ?? []);
	const postQuestions = $derived(task?.post_questions ?? []);
	const progress = $derived(task ? progressNumbers(task.progress, !!task.current_assignment_id) : null);
	const started = $derived(!!task && (task.progress.submitted + task.progress.skipped > 0 || !!task.current_assignment_id));
	const preDone = $derived(
		preStepDone({ questions: preQuestions, answers: task?.pre_answers, started, savedNow: preSubmitted })
	);
	const showPreForm = $derived(preQuestions.length > 0 && (!preDone || preEditing));
	const canEditPre = $derived(!started && !isCompleted && !preLocked && !task?.pre_answers_locked);
	const hasCurrent = $derived(!!task?.current_assignment_id);
	const canStart = $derived(!!task && task.accepting && !consentPending && preDone && !isCompleted && !isWithdrawn);
	const workLeft = $derived(
		!!task && task.progress.remaining_available > 0 && (task.progress.limit == null || (progress?.done ?? 0) < task.progress.limit)
	);
	const finishProminent = $derived(lastReason === 'done' || lastReason === 'limit_reached' || (!!task && started && !workLeft));
	const preTally = $derived(answerTally(preQuestions, preAnswers));
	const finishState = $derived(
		finishGate({ stepsPending: consentPending || !preDone, hasCurrent, prominent: finishProminent })
	);

	// Steps are numbered from what is shown.
	const steps = $derived.by(() => {
		const out: string[] = [];
		if (task?.instructions.trim()) out.push('instructions');
		if (task?.consent.enabled) out.push('consent');
		if (preQuestions.length) out.push('about');
		out.push('work', 'finish');
		return out;
	});
	function stepNo(id: string): string {
		const i = steps.indexOf(id);
		return i >= 0 ? String(i + 1) : '';
	}

	function focusFirstError(container: string, ids: string[]) {
		void tick().then(() => {
			const root = document.getElementById(container);
			for (const id of ids) {
				const el = root?.querySelector<HTMLElement>(
					`[data-question-id="${CSS.escape(id)}"] :is(input:not([disabled]), textarea, button[role="checkbox"])`
				);
				if (el) {
					el.focus();
					el.scrollIntoView({ block: 'center', behavior: 'smooth' });
					return;
				}
			}
		});
	}

	function withoutKey(map: Record<string, string>, key: string): Record<string, string> {
		const copy = { ...map };
		delete copy[key];
		return copy;
	}

	function scrollToStep(id: string) {
		void tick().then(() => {
			const el = document.getElementById(id);
			el?.scrollIntoView({ block: 'start', behavior: 'smooth' });
		});
	}

	// --- actions ----------------------------------------------------------------------
	async function submitConsent() {
		if (!task) return;
		consentError = '';
		if (!consentAgreed) {
			consentError = 'Tick the box to give your consent.';
			return;
		}
		consentBusy = true;
		try {
			applyTask(await giveConsent(task.id));
			toast.success('Consent recorded');
		} catch (e) {
			consentError = portalErrorMessage(e, 'Could not record your consent.');
		} finally {
			consentBusy = false;
		}
	}

	async function submitPre(event: SubmitEvent) {
		event.preventDefault();
		if (!task) return;
		preOtherErrors = [];
		const errors = validateQuestions(preQuestions, preAnswers);
		preErrors = errors;
		const invalid = preQuestions.map((q) => q.id).filter((id) => id in errors);
		if (invalid.length) {
			focusFirstError('task-about', invalid);
			return;
		}
		preBusy = true;
		try {
			const next = await savePreAnswers(task.id, cleanAnswers(preQuestions, preAnswers));
			preDirty = false;
			preEditing = false;
			preSubmitted = true;
			applyTask(next);
			toast.success('Answers saved');
			scrollToStep('task-work');
		} catch (e) {
			if (isReadOnlyError(e)) {
				// Locked server-side (an assignment exists, e.g. one that expired): keep what is stored.
				preLocked = true;
				preEditing = false;
				preDirty = false;
				preErrors = {};
				preAnswers = { ...(task?.pre_answers ?? {}) };
				toast.info(portalErrorMessage(e, 'Your answers can no longer be changed.'));
				return;
			}
			const mapped = mapQuestionErrors(errorList(e), preQuestions);
			preErrors = mapped.byQuestion;
			preOtherErrors = mapped.other.length || Object.keys(mapped.byQuestion).length ? mapped.other : [portalErrorMessage(e, 'Could not save your answers.')];
			focusFirstError('task-about', Object.keys(mapped.byQuestion));
		} finally {
			preBusy = false;
		}
	}

	async function startNext() {
		if (!task) return;
		nextBusy = true;
		nextError = '';
		nextNotice = null;
		try {
			const result = await requestNext(task.id);
			lastReason = result.reason;
			if (result.assignment_id) {
				await goto(workbenchPath(task.id, result.assignment_id));
				return;
			}
			nextNotice = nextReasonNotice(result.reason);
			if (result.reason === 'consent_required') scrollToStep('task-consent');
			else if (result.reason === 'pre_questions_required') scrollToStep('task-about');
			else if (result.reason === 'done' || result.reason === 'limit_reached') scrollToStep('task-finish');
			await load(true);
		} catch (e) {
			nextError = portalErrorMessage(e, 'Could not get the next app.');
		} finally {
			nextBusy = false;
		}
	}

	function continueCurrent() {
		if (task?.current_assignment_id) void goto(workbenchPath(task.id, task.current_assignment_id));
	}

	function beginFinish() {
		finishError = '';
		finishBlocked = false;
		if (postQuestions.length) {
			finishOpen = true;
			scrollToStep('task-finish');
		} else {
			confirmFinish = true;
		}
	}

	async function doFinish() {
		if (!task) return;
		finishBusy = true;
		finishError = '';
		finishBlocked = false;
		try {
			const next = await finishTask(task.id);
			confirmFinish = false;
			finishOpen = false;
			applyTask(next);
			void session.refresh();
			window.scrollTo({ top: 0, behavior: 'smooth' });
		} catch (e) {
			// 409 assignment_open: the open app has saved work; the server says what to do.
			finishBlocked = errorCode(e) === 'assignment_open';
			finishError = portalErrorMessage(e, 'Could not finish the evaluation.');
			confirmFinish = false;
			void load(true);
		} finally {
			finishBusy = false;
		}
	}

	async function submitPostAndFinish(event: SubmitEvent) {
		event.preventDefault();
		if (!task) return;
		postOtherErrors = [];
		finishError = '';
		const errors = validateQuestions(postQuestions, postAnswers);
		postErrors = errors;
		const invalid = postQuestions.map((q) => q.id).filter((id) => id in errors);
		if (invalid.length) {
			focusFirstError('task-finish', invalid);
			return;
		}
		finishBusy = true;
		try {
			const saved = await savePostAnswers(task.id, cleanAnswers(postQuestions, postAnswers));
			postDirty = false;
			applyTask(saved);
		} catch (e) {
			const mapped = mapQuestionErrors(errorList(e), postQuestions);
			postErrors = mapped.byQuestion;
			postOtherErrors = mapped.other.length || Object.keys(mapped.byQuestion).length ? mapped.other : [portalErrorMessage(e, 'Could not save your answers.')];
			focusFirstError('task-finish', Object.keys(mapped.byQuestion));
			finishBusy = false;
			return;
		}
		finishBusy = false;
		await doFinish();
	}

	async function doWithdraw(deleteData: boolean) {
		if (!task) return;
		withdrawBusy = true;
		withdrawError = '';
		const title = task.title;
		const id = task.id;
		try {
			await withdrawFromTask(id, { delete_data: deleteData });
			// The session is over: nothing of it stays in this browser (HTTP console history, headers).
			clearConsoleStorage();
			withdrawOpen = false;
			withdrawn = { deleteData, title };
			task = null;
			void session.forgetTask(id);
		} catch (e) {
			withdrawError = portalErrorMessage(e, 'Could not withdraw. Try again.');
		} finally {
			withdrawBusy = false;
		}
	}

	async function onResumed(result: { task_id: string }) {
		if (result.task_id === taskId) await load();
		else await goto(taskPath(result.task_id));
	}
</script>

{#snippet sessionHelp(title: string, description: string)}
	<div class="mx-auto w-full max-w-2xl space-y-6 py-2 sm:py-4">
		<EmptyState icon={TriangleAlert} {title} {description} />
		<Card.Root>
			<Card.Header>
				<Card.Title><h2 class="text-[17px] sm:text-[17px]">Enter your access code</h2></Card.Title>
				<Card.Description>Link and code sessions end when the browser closes.</Card.Description>
			</Card.Header>
			<Card.Content>
				<AccessCodeForm onresumed={onResumed} />
			</Card.Content>
		</Card.Root>
		{#if !session.me?.user}
			<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
				<p class="text-sm text-muted-foreground">Have an evaluator account?</p>
				<Button href={`/auth/login?next=${encodeURIComponent(`/eval/t/${taskId}`)}`} variant="outline" size="sm" class="self-start">
					<LogIn aria-hidden="true" />
					Sign in
				</Button>
			</div>
		{/if}
	</div>
{/snippet}

{#if withdrawn}
	<div class="mx-auto w-full max-w-2xl py-2 sm:py-4">
		<EmptyState
			icon={CircleCheck}
			title="You have withdrawn from “{withdrawn.title}”"
			description={withdrawn.deleteData
				? 'Everything you gave in this study was deleted. Thank you for your time.'
				: 'You no longer take part. Answers you had already submitted stay in the study’s pseudonymous data. Thank you for your time.'}
		>
			{#snippet action()}
				<Button href={PORTAL_HOME} size="sm" variant="outline">Back to the portal</Button>
			{/snippet}
		</EmptyState>
	</div>
{:else if loading && !task}
	<LoadingState label="loading evaluation" />
{:else if !task && loadStatus === 401}
	{@render sessionHelp('Your session has ended', 'Enter your access code (or sign in) to continue where you left off. Your saved work is kept.')}
{:else if !task && loadStatus === 404}
	{@render sessionHelp(
		'This evaluation is not available',
		'This browser or account does not take part in it. If you joined with a link or a code, enter your access code.'
	)}
{:else if !task}
	<div class="mx-auto w-full max-w-2xl py-2 sm:py-4">
		<ErrorAlert message={loadError || 'Could not load this evaluation.'} onretry={() => void load()} />
	</div>
{:else}
	{@const p = task.participant}
	<div class="mx-auto w-full max-w-5xl space-y-6 py-1 sm:py-2">
		<header class="space-y-2">
			<p class="page-eyebrow">Evaluation</p>
			<h1 class="break-words">{task.title}</h1>
			{#if task.description.trim()}
				<p class="max-w-prose whitespace-pre-line text-sm text-muted-foreground">{task.description}</p>
			{/if}
			<div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
				<span>
					Participant <span class="font-mono font-medium text-foreground">{p.label}</span>
				</span>
				<StatusBadge status={p.status} tones={PORTAL_TONES} label={participantStatusText(p.status)} />
				{#if task.status !== 'open'}
					<StatusBadge status={task.status} tones={PORTAL_TONES} label={`Evaluation ${task.status}`} />
				{/if}
			</div>
		</header>

		{#if isPreview}
			<div class="flex flex-col gap-2 rounded-md border border-info/40 bg-info/10 p-3 text-sm text-info-fg sm:flex-row sm:items-center sm:justify-between">
				<p class="flex items-start gap-2">
					<Eye class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
					<span>Researcher preview: you see what participants see. Your answers never enter the results or exports.</span>
				</p>
				<Button href={researcherTaskPath(task.id)} size="sm" variant="outline" class="self-start sm:self-center">
					Back to the task
				</Button>
			</div>
		{/if}

		{#if isCompleted}
			<!-- Completed: the completion code. -->
			<Card.Root class="gap-5 py-6">
				<Card.Header class="space-y-1.5">
					<Card.Title><h2 class="flex items-center gap-2"><CircleCheck class="size-5 text-success-fg" aria-hidden="true" />Thank you — you have finished</h2></Card.Title>
					<Card.Description class="text-sm">
						You evaluated {task.progress.submitted}
						{task.progress.submitted === 1 ? 'app' : 'apps'}{task.progress.skipped
							? ` and skipped ${task.progress.skipped}`
							: ''}. Your answers are saved; you can close this page.
					</Card.Description>
				</Card.Header>
				{#if p.completion_code}
					<Card.Content class="space-y-3">
						<CodeDisplay code={p.completion_code} label="Your completion code" />
						<p class="text-sm text-muted-foreground">
							If the researcher asked for a completion code (for example for course credit), give them this code.
							It is also shown here whenever you come back with your access code.
						</p>
					</Card.Content>
				{/if}
			</Card.Root>
		{:else if isWithdrawn}
			<EmptyState
				title="You have withdrawn from this evaluation"
				description="You no longer take part, so no apps can be started."
			/>
		{:else}
			{#if !task.accepting}
				<div class="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-fg" role="status">
					<TriangleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
					<p>
						{#if task.status === 'paused'}
							The evaluation is paused, so no apps can be started right now. Your saved work is kept — come back later.
						{:else if task.status === 'closed' || task.status === 'archived'}
							The evaluation has closed. Your saved work is kept.
						{:else}
							The evaluation is not accepting work right now (outside its opening hours). Your saved work is kept.
						{/if}
					</p>
				</div>
			{/if}

			<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start">
				<div class="min-w-0 space-y-8">
					{#if task.instructions.trim()}
						<section class="space-y-3" aria-labelledby="task-instructions-h" id="task-instructions">
							<SectionHeading id="task-instructions-h" title="Instructions" number={stepNo('instructions')} size="sm" />
							{#if started}
								<details class="group rounded-md border border-border bg-card">
									<summary class="cursor-pointer select-none px-4 py-2.5 text-sm font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
										Show the instructions again
									</summary>
									<div class="border-t border-border px-4 py-3">
										<Markdown source={task.instructions} />
									</div>
								</details>
							{:else}
								<Card.Root class="py-0">
									<div class="px-4 py-4 sm:px-5">
										<Markdown source={task.instructions} />
									</div>
								</Card.Root>
							{/if}
							{#if task.blocks.length && !started}
								<div class="space-y-1.5">
									<p class="section-label">For each app you will</p>
									<ol class="list-decimal space-y-0.5 pl-5 text-sm">
										{#each task.blocks as block (block.id)}
											<li>
												<span class="font-medium">{block.title}</span>
												<span class="text-muted-foreground"> · {blockTypeText(block.type)}</span>
											</li>
										{/each}
									</ol>
								</div>
							{/if}
						</section>
					{/if}

					{#if task.consent.enabled}
						<section class="space-y-3" aria-labelledby="task-consent-h" id="task-consent">
							<SectionHeading id="task-consent-h" title="Consent" number={stepNo('consent')} size="sm" />
							{#if consentPending}
								<ConsentPanel text={task.consent.text} bind:agreed={consentAgreed} error={consentError} />
								<div class="flex justify-end">
									<Button onclick={submitConsent} disabled={consentBusy} aria-busy={consentBusy}>
										{#if consentBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
										Give consent
									</Button>
								</div>
							{:else}
								<p class="flex items-center gap-1.5 text-sm text-success-fg">
									<CircleCheck class="size-4 shrink-0" aria-hidden="true" />
									{participant?.consent_at ? `Consent given on ${formatDateTime(participant.consent_at)}.` : 'Consent given.'}
								</p>
								<details class="rounded-md border border-border bg-card">
									<summary class="cursor-pointer select-none px-4 py-2.5 text-sm font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
										Read the study information again
									</summary>
									<div class="border-t border-border px-4 py-3">
										<Markdown source={task.consent.text} />
									</div>
								</details>
							{/if}
						</section>
					{/if}

					{#if preQuestions.length}
						<section class="space-y-3" aria-labelledby="task-about-h" id="task-about">
							<SectionHeading
								id="task-about-h"
								title="About you"
								number={stepNo('about')}
								size="sm"
								count={showPreForm ? `${preTally.answered}/${preTally.total}` : undefined}
							/>
							{#if showPreForm}
								{#if consentPending}
									<p class="text-sm text-muted-foreground">Give your consent above first.</p>
								{:else}
									<form class="space-y-5" onsubmit={submitPre} novalidate>
										<p class="text-sm text-muted-foreground">
											A few questions before you start. They help interpret the results and are stored with your
											pseudonymous participant number.
										</p>
										<Card.Root class="py-0">
											<div class="px-4 py-5 sm:px-5">
												<QuestionList
													questions={preQuestions}
													bind:answers={preAnswers}
													errors={preErrors}
													onchange={(id) => {
														preDirty = true;
														if (preErrors[id]) preErrors = withoutKey(preErrors, id);
													}}
												/>
											</div>
										</Card.Root>
										{#each preOtherErrors as message (message)}
											<ErrorAlert {message} />
										{/each}
										<div class="flex flex-wrap justify-end gap-2">
											{#if preEditing}
												<Button
													variant="outline"
													onclick={() => {
														preEditing = false;
														preDirty = false;
														preErrors = {};
														preAnswers = { ...(task?.pre_answers ?? {}) };
													}}
												>
													Cancel
												</Button>
											{/if}
											<Button type="submit" disabled={preBusy} aria-busy={preBusy}>
												{#if preBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
												{preEditing ? 'Save answers' : 'Save and continue'}
											</Button>
										</div>
									</form>
								{/if}
							{:else}
								<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
									<p class="flex items-center gap-1.5 text-sm text-success-fg">
										<CircleCheck class="size-4 shrink-0" aria-hidden="true" />
										Answered
									</p>
									{#if canEditPre}
										<Button variant="ghost" size="sm" onclick={() => (preEditing = true)}>Change answers</Button>
									{:else if preLocked}
										<p class="text-xs text-muted-foreground">Locked: you already started evaluating.</p>
									{/if}
								</div>
								{#if !canEditPre}
									<details class="rounded-md border border-border bg-card">
										<summary class="cursor-pointer select-none px-4 py-2.5 text-sm font-medium hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
											Your answers
										</summary>
										<div class="border-t border-border px-4 py-4">
											<QuestionList questions={preQuestions} answers={{ ...task.pre_answers }} readOnly />
										</div>
									</details>
								{/if}
							{/if}
						</section>
					{/if}

					<section class="space-y-3" aria-labelledby="task-work-h" id="task-work">
						<SectionHeading id="task-work-h" title="Evaluate apps" number={stepNo('work')} size="sm" />
						<Card.Root class="gap-4 py-4">
							<Card.Content class="space-y-4">
								{#if progress}
									<div class="space-y-1.5">
										<p class="text-sm">
											<span class="font-mono font-medium tabular-nums">{progress.submitted}</span>
											{progress.total != null && progress.total > 0
												? `of ${progress.total} ${progress.total === 1 ? 'app' : 'apps'} evaluated`
												: `${progress.submitted === 1 ? 'app' : 'apps'} evaluated`}
											{#if progress.skipped}
												<span class="text-muted-foreground"> · {progress.skipped} skipped</span>
											{/if}
										</p>
										<ProgressBar
											fraction={progress.fraction}
											label="Your progress"
											valueText={progress.total != null ? `${progress.done} of ${progress.total}` : `${progress.done} done`}
										/>
										{#if task.progress.limit != null}
											<p class="text-xs text-muted-foreground">
												You evaluate at most {task.progress.limit} {task.progress.limit === 1 ? 'app' : 'apps'}.
											</p>
										{/if}
									</div>
								{/if}

								{#if nextNotice}
									<div class="rounded-md border border-border bg-surface-1 p-3 text-sm" role="status">
										<p class="font-medium">{nextNotice.title}</p>
										<p class="mt-0.5 text-muted-foreground">{nextNotice.description}</p>
									</div>
								{/if}
								{#if nextError}
									<ErrorAlert message={nextError} />
								{/if}

								<div class="flex flex-col gap-2 sm:flex-row sm:items-center">
									{#if hasCurrent}
										<Button onclick={continueCurrent}>
											<Play aria-hidden="true" />
											Continue current app
										</Button>
										<p class="text-xs text-muted-foreground">Submit or skip it to get the next one, or finish below to stop here.</p>
									{:else}
										<Button onclick={startNext} disabled={!canStart || nextBusy} aria-busy={nextBusy}>
											{#if nextBusy}
												<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
											{:else}
												<Play aria-hidden="true" />
											{/if}
											{started ? 'Start next app' : 'Start the first app'}
										</Button>
										{#if !canStart}
											<p class="text-xs text-muted-foreground">
												{#if !task.accepting}
													Not accepting work right now.
												{:else if consentPending}
													Give your consent first.
												{:else if !preDone}
													Answer the questions about you first.
												{/if}
											</p>
										{/if}
									{/if}
								</div>
								{#if task.allow_skip}
									<p class="text-xs text-muted-foreground">
										If an app does not load or you cannot evaluate it, you can skip it from the evaluation screen.
									</p>
								{/if}
							</Card.Content>
						</Card.Root>
					</section>

					<section class="space-y-3" aria-labelledby="task-finish-h" id="task-finish">
						<SectionHeading id="task-finish-h" title="Finish" number={stepNo('finish')} size="sm" />
						{#if finishOpen && postQuestions.length}
							<form class="space-y-5" onsubmit={submitPostAndFinish} novalidate>
								<p class="text-sm text-muted-foreground">A few last questions, then you get your completion code.</p>
								{#if hasCurrent}
									<p class="text-sm text-muted-foreground">{finishState.hint}</p>
								{/if}
								<Card.Root class="py-0">
									<div class="px-4 py-5 sm:px-5">
										<QuestionList
											questions={postQuestions}
											bind:answers={postAnswers}
											errors={postErrors}
											onchange={(id) => {
												postDirty = true;
												if (postErrors[id]) postErrors = withoutKey(postErrors, id);
											}}
										/>
									</div>
								</Card.Root>
								{#each postOtherErrors as message (message)}
									<ErrorAlert {message} />
								{/each}
								{#if finishError}
									<ErrorAlert message={finishError} />
									{#if finishBlocked && hasCurrent}
										<Button variant="outline" size="sm" onclick={continueCurrent}>
											<Play aria-hidden="true" />
											Continue current app
										</Button>
									{/if}
								{/if}
								<div class="flex flex-wrap justify-end gap-2">
									<Button variant="outline" onclick={() => (finishOpen = false)} disabled={finishBusy}>Not yet</Button>
									<Button type="submit" disabled={finishBusy} aria-busy={finishBusy}>
										{#if finishBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
										Submit answers and finish
									</Button>
								</div>
							</form>
						{:else}
							<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
								<p class="max-w-prose text-sm text-muted-foreground">{finishState.hint}</p>
								<Button
									variant={finishProminent ? 'default' : 'outline'}
									onclick={beginFinish}
									disabled={finishState.disabled || finishBusy}
									class="self-start sm:self-center"
								>
									<Flag aria-hidden="true" />
									Finish the evaluation
								</Button>
							</div>
							{#if finishError}
								<ErrorAlert message={finishError} />
								{#if finishBlocked && hasCurrent}
									<Button variant="outline" size="sm" onclick={continueCurrent}>
										<Play aria-hidden="true" />
										Continue current app
									</Button>
								{/if}
							{/if}
						{/if}
					</section>
				</div>

				<aside class="space-y-3 lg:sticky lg:top-16" aria-label="Your participation">
					<Card.Root class="gap-3 py-4">
						<Card.Content class="space-y-3 text-sm">
							<div>
								<p class="section-label">Participant</p>
								<p class="font-mono text-base font-medium">{p.label}</p>
							</div>
							{#if progress}
								<div>
									<p class="section-label">Progress</p>
									<p>
										<span class="font-mono tabular-nums">{progress.done}</span>
										{progress.total != null ? `of ${progress.total}` : ''} done
									</p>
								</div>
							{/if}
							{#if p.kind === 'invite' || p.kind === 'code'}
								<p class="text-xs text-muted-foreground">
									This session ends when you close the browser. Come back to
									<a href={PORTAL_HOME} class="text-primary hover:underline">the portal</a> with your access code to
									continue.
								</p>
							{/if}
						</Card.Content>
					</Card.Root>
					{#if !isPreview}
						<Button
							variant="ghost"
							size="sm"
							class="w-full justify-start text-muted-foreground hover:text-danger-fg"
							onclick={() => {
								withdrawError = '';
								withdrawOpen = true;
							}}
						>
							Withdraw from this study
						</Button>
					{/if}
				</aside>
			</div>
		{/if}

		{#if isCompleted && !isPreview}
			<div class="flex justify-end">
				<Button
					variant="ghost"
					size="sm"
					class="text-muted-foreground hover:text-danger-fg"
					onclick={() => {
						withdrawError = '';
						withdrawOpen = true;
					}}
				>
					Withdraw from this study
				</Button>
			</div>
		{/if}
	</div>
{/if}

<ConfirmDialog
	bind:open={confirmFinish}
	title="Finish the evaluation?"
	description={finishConfirmText({ started, workLeft, hasCurrent })}
	confirmLabel="Finish"
	busy={finishBusy}
	onConfirm={doFinish}
/>

<WithdrawDialog bind:open={withdrawOpen} busy={withdrawBusy} error={withdrawError} onConfirm={doWithdraw} />
