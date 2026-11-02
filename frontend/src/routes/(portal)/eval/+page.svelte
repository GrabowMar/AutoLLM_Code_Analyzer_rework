<script lang="ts">
	// Portal home: the evaluations this browser / account takes part in (cookie
	// participation plus account and preview participations from me/), an
	// "Enter access code" form (resume) and a sign-in link for evaluator accounts.
	import { onMount } from 'svelte';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import LogIn from '@lucide/svelte/icons/log-in';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import AccessCodeForm from '$lib/components/eval-portal/AccessCodeForm.svelte';
	import ProgressBar from '$lib/components/eval-portal/ProgressBar.svelte';
	import { portalTitle } from '$lib/components/eval-portal/portal-title.svelte';
	import { getPortalSession } from '$lib/components/eval-portal/portal-session.svelte';
	import {
		PORTAL_TONES,
		participantStatusText,
		participationFraction,
		participationProgressText,
		taskPath,
	} from '$lib/components/eval-portal/portal';
	import type { PortalParticipation } from '$lib/api/types/human-eval';

	const session = getPortalSession();

	onMount(() => {
		portalTitle.current = '';
		void session.refresh();
	});

	const me = $derived(session.me);
	const participations = $derived(me?.participations ?? []);
	const current = $derived(participations.filter((p) => p.participant_status !== 'withdrawn'));
	const withdrawn = $derived(participations.filter((p) => p.participant_status === 'withdrawn'));

	function actionLabel(p: PortalParticipation): string {
		if (p.participant_status === 'completed') return 'View';
		if (p.task_status === 'closed' || p.task_status === 'archived') return 'View';
		if (p.participant_status === 'invited' && p.submitted === 0) return 'Start';
		return 'Continue';
	}
</script>

<div class="mx-auto w-full max-w-3xl space-y-8 py-2 sm:py-4">
	<header class="space-y-1.5">
		<p class="page-eyebrow">Evaluation portal</p>
		<h1>Your evaluations</h1>
		<p class="max-w-prose text-sm text-muted-foreground">
			Continue an evaluation you have joined, or enter the access code you were given or wrote down when you
			joined.
		</p>
	</header>

	{#if session.loading && !me}
		<LoadingState label="checking your session" class="py-8" />
	{:else if session.error && !me}
		<ErrorAlert message={session.error} onretry={() => void session.refresh()} />
	{/if}

	{#if current.length > 0}
		<section class="space-y-3" aria-labelledby="portal-participations">
			<SectionHeading id="portal-participations" title="Joined" size="sm" count={current.length} />
			<ul class="grid gap-3">
				{#each current as p (`${p.kind}:${p.task_id}`)}
					{@const fraction = participationFraction(p)}
					<li>
						<Card.Root class="gap-0 py-0">
							<div class="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:gap-4">
								<div class="min-w-0 flex-1 space-y-2">
									<div class="flex flex-wrap items-center gap-2">
										<h2 class="min-w-0 truncate text-base sm:text-lg">
											<a
												href={taskPath(p.task_id)}
												class="rounded hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
												>{p.task_title}</a
											>
										</h2>
										{#if p.kind === 'preview'}
											<Badge variant="outline">Researcher preview</Badge>
										{/if}
									</div>
									<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
										<span class="font-mono font-medium text-foreground">{p.participant_label}</span>
										<StatusBadge
											status={p.participant_status}
											tones={PORTAL_TONES}
											label={participantStatusText(p.participant_status)}
										/>
										{#if p.task_status !== 'open'}
											<StatusBadge status={p.task_status} tones={PORTAL_TONES} label={`Evaluation ${p.task_status}`} />
										{/if}
										<span>{participationProgressText(p)}</span>
									</div>
									<ProgressBar
										{fraction}
										label="Progress in {p.task_title}"
										valueText={participationProgressText(p)}
										class="max-w-sm"
									/>
								</div>
								<Button
									href={taskPath(p.task_id)}
									size="sm"
									variant={actionLabel(p) === 'View' ? 'outline' : 'default'}
									class="self-start sm:self-center"
								>
									{actionLabel(p)}
									<ArrowRight aria-hidden="true" />
									<span class="sr-only">{p.task_title}</span>
								</Button>
							</div>
						</Card.Root>
					</li>
				{/each}
			</ul>
		</section>
	{:else if me && !session.loading}
		<p class="text-sm text-muted-foreground">
			{#if me.user}
				No evaluation is linked to {me.user.email} yet. Use an invitation link or an access code from the
				researcher.
			{:else}
				This browser has not joined an evaluation yet. Open the invitation link you were sent, or enter an access
				code below.
			{/if}
		</p>
	{/if}

	<section aria-labelledby="portal-enter-code">
		<Card.Root>
			<Card.Header>
				<Card.Title>
					<h2 id="portal-enter-code" class="flex items-center gap-2 text-[17px] sm:text-[17px]">
						<KeyRound class="size-4 text-muted-foreground" aria-hidden="true" />
						Enter access code
					</h2>
				</Card.Title>
				<Card.Description>
					Your session ends when you close the browser. Come back here with your code to continue where you left
					off — on this or any other device.
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<AccessCodeForm autofocus={!!me && current.length === 0 && !me.user} />
			</Card.Content>
		</Card.Root>
	</section>

	{#if me && !me.user}
		<section aria-labelledby="portal-account" class="space-y-2">
			<SectionHeading id="portal-account" title="Evaluator account" size="sm" />
			<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
				<p class="max-w-prose text-sm text-muted-foreground">
					If the researcher created an account for you, sign in with your email and password; your evaluations
					then appear on this page.
				</p>
				<Button href="/auth/login?next=/eval" variant="outline" size="sm" class="self-start sm:self-center">
					<LogIn aria-hidden="true" />
					Sign in with an account
				</Button>
			</div>
		</section>
	{:else if me?.user}
		<p class="text-xs text-muted-foreground">
			Signed in as <span class="font-medium text-foreground">{me.user.email}</span>.
		</p>
	{/if}

	{#if withdrawn.length > 0}
		<section class="space-y-2" aria-labelledby="portal-withdrawn">
			<SectionHeading id="portal-withdrawn" title="Withdrawn" size="sm" count={withdrawn.length} />
			<ul class="space-y-1 text-sm text-muted-foreground">
				{#each withdrawn as p (`${p.kind}:${p.task_id}`)}
					<li>
						<span class="text-foreground">{p.task_title}</span>
						<span class="font-mono text-xs"> · {p.participant_label}</span>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
</div>
