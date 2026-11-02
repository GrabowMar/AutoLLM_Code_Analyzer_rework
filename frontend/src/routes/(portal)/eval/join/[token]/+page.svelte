<script lang="ts">
	// Join through an invite link: task title / description, availability,
	// consent (markdown + checkbox), Join → the resume code shown once (large,
	// copy / save, "I have written it down" before continuing) → the task page.
	// A browser that already joined this task gets "Continue" instead.
	import { untrack } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { apiErrorStatus } from '$lib/api/core';
	import { getJoinInfo, joinTask } from '$lib/api/human-eval-portal';
	import type { JoinInfo, JoinResult } from '$lib/api/types/human-eval';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import CodeDisplay from '$lib/components/eval-portal/CodeDisplay.svelte';
	import ConsentPanel from '$lib/components/eval-portal/ConsentPanel.svelte';
	import { portalTitle } from '$lib/components/eval-portal/portal-title.svelte';
	import { getPortalSession } from '$lib/components/eval-portal/portal-session.svelte';
	import {
		PORTAL_HOME,
		availabilityNotice,
		errorCode,
		formatAccessCode,
		portalErrorMessage,
		taskPath,
	} from '$lib/components/eval-portal/portal';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const session = getPortalSession();

	let info = $state<JoinInfo | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let notFound = $state(false);
	let loadedToken: string | null = null;
	let loadSeq = 0;

	let agreed = $state(false);
	let consentError = $state('');
	let joining = $state(false);
	let joinError = $state('');
	let result = $state<JoinResult | null>(null);
	let savedCode = $state(false);
	let continuing = false;

	const notice = $derived(info ? availabilityNotice(info.availability) : null);
	const consentNeeded = $derived(!!info?.consent.enabled);
	const portalUrl = $derived(typeof location === 'undefined' ? PORTAL_HOME : `${location.origin}${PORTAL_HOME}`);

	async function load() {
		const mine = ++loadSeq;
		loading = true;
		loadError = '';
		notFound = false;
		try {
			const next = await getJoinInfo(data.token);
			if (mine !== loadSeq) return;
			info = next;
			portalTitle.current = next.task_title;
		} catch (e) {
			if (mine !== loadSeq) return;
			info = null;
			portalTitle.current = '';
			if (apiErrorStatus(e) === 404) notFound = true;
			else loadError = portalErrorMessage(e, 'Could not load this invitation.');
		} finally {
			if (mine === loadSeq) loading = false;
		}
	}

	$effect(() => {
		const token = data.token;
		untrack(() => {
			if (token === loadedToken) return;
			loadedToken = token;
			result = null;
			savedCode = false;
			agreed = false;
			void load();
		});
	});

	async function join() {
		if (!info) return;
		consentError = '';
		joinError = '';
		if (consentNeeded && !agreed) {
			consentError = 'Tick the box to give your consent before joining.';
			return;
		}
		joining = true;
		try {
			result = await joinTask(data.token, { consent: consentNeeded ? agreed : false });
			void session.refresh();
		} catch (e) {
			const status = apiErrorStatus(e);
			if (status === 409 && errorCode(e) === 'already_joined') {
				await load();
				return;
			}
			joinError = portalErrorMessage(e, 'Could not join this evaluation.');
			// Availability may have changed (closed, full, expired).
			if (status === 409 || status === 404) void load();
		} finally {
			joining = false;
		}
	}

	async function continueToTask() {
		if (!result || !savedCode) return;
		continuing = true;
		await goto(taskPath(result.task_id));
	}

	// The code is shown once: warn before leaving without confirming it was saved.
	beforeNavigate((navigation) => {
		if (!result || savedCode || continuing) return;
		if (navigation.willUnload) {
			navigation.cancel();
			return;
		}
		if (!confirm('Leave without saving your access code? You need it to continue later.')) {
			navigation.cancel();
		}
	});

	const codeFile = $derived(
		result && info
			? {
					filename: 'llm-lab-access-code.txt',
					text: [
						`Evaluation: ${info.task_title}`,
						`Participant: ${result.participant_label}`,
						`Access code: ${formatAccessCode(result.access_code)}`,
						``,
						`To continue later, open ${portalUrl} and enter the access code.`,
						``,
					].join('\n'),
				}
			: null
	);
</script>

<div class="mx-auto w-full max-w-2xl space-y-6 py-2 sm:py-4">
	{#if loading && !info}
		<LoadingState label="loading invitation" />
	{:else if notFound}
		<EmptyState
			icon={TriangleAlert}
			title="This invitation link is not valid"
			description="Check that you opened the complete link, or ask the researcher for a new one. If you already joined, enter your access code instead."
		>
			{#snippet action()}
				<Button href={PORTAL_HOME} size="sm" variant="outline">Enter an access code</Button>
			{/snippet}
		</EmptyState>
	{:else if loadError}
		<ErrorAlert message={loadError} onretry={() => void load()} />
	{:else if result && info}
		<!-- Joined: the resume code, shown once. -->
		<Card.Root class="gap-5 py-6">
			<Card.Header class="space-y-1.5">
				<p class="page-eyebrow">Save your access code</p>
				<Card.Title><h1 class="text-2xl">You have joined</h1></Card.Title>
				<Card.Description class="text-sm">
					You take part as <span class="font-mono font-medium text-foreground">{result.participant_label}</span>.
					This browser stays signed in only until you close it.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-5">
				<CodeDisplay code={result.access_code} label="Your access code" grouped download={codeFile} />
				<div class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-fg">
					<p class="font-medium">Write this code down now — it is shown only once.</p>
					<p class="mt-1">
						To continue later, on this or another device, open
						<span class="font-mono break-all">{portalUrl}</span> and enter the code. Without it your work cannot
						be resumed, and the researcher cannot look it up for you.
					</p>
				</div>
				<label class="flex cursor-pointer items-start gap-2.5 text-sm" for="code-saved">
					<Checkbox id="code-saved" class="mt-0.5" bind:checked={savedCode} />
					<span class="font-medium">I have written down or saved my access code.</span>
				</label>
			</Card.Content>
			<Card.Footer class="justify-end">
				<Button onclick={continueToTask} disabled={!savedCode}>
					Continue to the evaluation
					<ArrowRight aria-hidden="true" />
				</Button>
			</Card.Footer>
		</Card.Root>
	{:else if info}
		<header class="space-y-1.5">
			<p class="page-eyebrow">Invitation</p>
			<h1>{info.task_title}</h1>
			{#if info.task_description.trim()}
				<p class="whitespace-pre-line text-sm text-muted-foreground">{info.task_description}</p>
			{/if}
			{#if info.estimated_items != null && info.estimated_items > 0}
				<p class="text-sm text-muted-foreground">
					You will evaluate about <span class="font-mono font-medium text-foreground">{info.estimated_items}</span>
					{info.estimated_items === 1 ? 'app' : 'apps'}.
				</p>
			{/if}
		</header>

		{#if info.already_joined}
			<Card.Root>
				<Card.Header>
					<Card.Title><h2 class="text-[17px] sm:text-[17px]">You have already joined</h2></Card.Title>
					<Card.Description>
						This browser takes part as
						<span class="font-mono font-medium text-foreground">{info.already_joined.participant_label}</span>.
						Joining again would make you a second participant, so continue instead.
					</Card.Description>
				</Card.Header>
				<Card.Footer class="justify-end">
					<Button href={taskPath(info.already_joined.task_id)}>
						Continue
						<ArrowRight aria-hidden="true" />
					</Button>
				</Card.Footer>
			</Card.Root>
		{:else if notice}
			<EmptyState icon={TriangleAlert} title={notice.title} description={notice.description}>
				{#snippet action()}
					<Button href={PORTAL_HOME} size="sm" variant="outline">Go to the portal</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<form
				class="space-y-6"
				onsubmit={(e) => {
					e.preventDefault();
					void join();
				}}
			>
				{#if consentNeeded}
					<section class="space-y-3" aria-labelledby="join-consent">
						<SectionHeading id="join-consent" title="Study information and consent" number="1" size="sm" />
						<ConsentPanel text={info.consent.text} bind:agreed error={consentError} />
					</section>
				{/if}
				<section class="space-y-3" aria-labelledby="join-step">
					<SectionHeading
						id="join-step"
						title="Join"
						number={consentNeeded ? '2' : '1'}
						size="sm"
					/>
					<p class="text-sm text-muted-foreground">
						You get a personal access code next, so you can come back to your work later. You do not need an
						account.
					</p>
					{#if joinError}
						<ErrorAlert message={joinError} />
					{/if}
					<div class="flex justify-end">
						<Button type="submit" disabled={joining} aria-busy={joining}>
							{#if joining}
								<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
							{/if}
							{consentNeeded ? 'Agree and join' : 'Join the evaluation'}
						</Button>
					</div>
				</section>
			</form>
		{/if}
	{/if}
</div>
