<script lang="ts">
	/**
	 * /join#<token> — join someone's workspace as a co-author. The token is read
	 * from the fragment, kept in this browser (so sign-up, e-mail verification
	 * and login can come back here) and removed from the address bar at once.
	 * Nothing is joined until a signed-in person presses "Join workspace" on a
	 * screen that names the inviter by their verified e-mail.
	 */
	import { onMount, tick } from 'svelte';
	import { goto, replaceState } from '$app/navigation';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getWorkspace } from '$lib/stores/workspace.svelte';
	import { apiErrorStatus, formatApiError } from '$lib/api/core';
	import { acceptInvite, coauthorErrorCode, previewInvite, type InvitePreview } from '$lib/api/coauthors';
	import {
		clearInvite,
		inviteFromHash,
		isFramed,
		pendingInvite,
		rememberInvite,
	} from '$lib/coauthors/pending-invite';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import BrandMark from '$lib/components/BrandMark.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Users from '@lucide/svelte/icons/users';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Link2Off from '@lucide/svelte/icons/link-2-off';
	import Clock from '@lucide/svelte/icons/clock';
	import Lock from '@lucide/svelte/icons/lock';
	import UserCheck from '@lucide/svelte/icons/user-check';

	type Phase = 'starting' | 'framed' | 'loading' | 'invalid' | 'throttled' | 'evaluator' | 'error' | 'ready';

	const auth = getAuth();
	const workspace = getWorkspace();
	const LOGIN = '/auth/login?next=/join';

	let phase = $state<Phase>('starting');
	let token = $state<string | null>(null);
	let preview = $state<InvitePreview | null>(null);
	let loadError = $state('');
	let acceptError = $state('');
	let accepting = $state(false);
	let opening = $state(false);
	let previewedFor = '';
	let seq = 0;

	const inviter = $derived(preview ? preview.inviter_name || preview.inviter_email : '');
	const hasName = $derived(!!preview?.inviter_name);
	// While a usable invite is on screen, the app shell would bring the person straight
	// back here: leaving through the home links means "not now".
	const holdsInvite = $derived(phase === 'ready' && preview?.state === 'valid');

	/** Take the token from the fragment (then drop it from the address bar), else the one kept earlier. */
	async function readToken() {
		const hash = location.hash;
		const fromLink = inviteFromHash(hash);
		if (fromLink) rememberInvite(fromLink);
		if (hash) {
			await tick();
			replaceState('/join', {});
		}
		if (hash && !fromLink) {
			// An incomplete or mangled link: say so rather than falling back to an older invite.
			token = null;
			phase = 'invalid';
			return;
		}
		token = pendingInvite();
		previewedFor = '';
		phase = token ? 'loading' : 'invalid';
	}

	async function runPreview() {
		if (!token) return;
		const mine = ++seq;
		phase = 'loading';
		loadError = '';
		try {
			const next = await previewInvite(token);
			if (mine !== seq) return;
			preview = next;
			phase = 'ready';
			if (next.state !== 'valid') clearInvite();
		} catch (e) {
			if (mine !== seq) return;
			preview = null;
			const status = apiErrorStatus(e);
			const code = coauthorErrorCode(e);
			if (status === 404) {
				clearInvite();
				phase = 'invalid';
			} else if (status === 429) {
				phase = 'throttled';
			} else if (status === 403 && code === 'evaluator_only') {
				phase = 'evaluator';
			} else {
				loadError = formatApiError(e, 'Could not check this invite link.');
				phase = 'error';
			}
		}
	}

	// Preview once the session is known, and again if it changes (signed in elsewhere).
	$effect(() => {
		if (auth.isLoading || !token || phase === 'framed' || phase === 'starting') return;
		const key = `${token}|${auth.isAuthenticated ? auth.user?.email : ''}`;
		if (key === previewedFor) return;
		previewedFor = key;
		void runPreview();
	});

	onMount(() => {
		if (isFramed()) {
			phase = 'framed';
			return;
		}
		void readToken();
		const onHashChange = () => {
			if (location.hash) void readToken();
		};
		window.addEventListener('hashchange', onHashChange);
		return () => window.removeEventListener('hashchange', onHashChange);
	});

	async function join() {
		if (!token) return;
		accepting = true;
		acceptError = '';
		try {
			await acceptInvite(token);
			clearInvite();
			// A full reload is the cache reset; the banner there confirms the join.
			window.location.assign('/');
		} catch (e) {
			accepting = false;
			const status = apiErrorStatus(e);
			const code = coauthorErrorCode(e);
			if (status === 403 && (code === 'evaluator_denied' || code === 'evaluator_only')) {
				clearInvite();
				phase = 'evaluator';
			} else if (status === 404 || code === 'own_invite') {
				// The link changed since the preview (used, revoked, expired): show what it is now.
				void runPreview();
			} else {
				acceptError = formatApiError(e, 'Could not join this workspace.');
			}
		}
	}

	function notNow() {
		clearInvite();
		void goto('/');
	}

	function leaveHome(event: MouseEvent) {
		if (!holdsInvite) return;
		event.preventDefault();
		notNow();
	}

	async function openWorkspace() {
		if (!preview?.coauthorship_id) return;
		opening = true;
		try {
			await workspace.switchTo(preview.coauthorship_id);
		} catch (e) {
			opening = false;
			acceptError = formatApiError(e, 'Could not open this workspace.');
		}
	}
</script>

<svelte:head>
	<title>Join a workspace · LLM Lab</title>
	<meta name="referrer" content="no-referrer" />
</svelte:head>

{#snippet stateCard(Icon: typeof Users, title: string, body: string, tone: 'neutral' | 'warning' = 'neutral')}
	<Card.Header class="gap-3">
		<span
			class="flex size-9 items-center justify-center rounded-md {tone === 'warning'
				? 'bg-warning/15 text-warning-fg'
				: 'bg-muted text-muted-foreground'}"
			aria-hidden="true"
		>
			<Icon class="size-4.5" />
		</span>
		<Card.Title><h1 class="text-xl">{title}</h1></Card.Title>
		<Card.Description class="text-sm">{body}</Card.Description>
	</Card.Header>
{/snippet}

<div class="lab-canvas flex min-h-dvh flex-col items-center justify-center px-4 py-8 sm:p-8">
	<div class="w-full max-w-md space-y-6">
		<a href="/" class="mx-auto flex w-fit" aria-label="LLM Lab home" onclick={leaveHome}>
			<BrandMark tagline size="lg" />
		</a>

		<Card.Root class="gap-5 py-6">
			{#if phase === 'framed'}
				{@render stateCard(Lock, 'Open this link in its own tab', 'For your safety, invite links do not work inside another page.', 'warning')}
			{:else if phase === 'starting' || phase === 'loading' || (phase === 'ready' && !preview)}
				<LoadingState label="checking invite" class="py-10" />
			{:else if phase === 'invalid'}
				{@render stateCard(
					Link2Off,
					'This invite link is not valid',
					'It may have expired or already been used. Ask the person who sent it for a new link.',
				)}
			{:else if phase === 'throttled' && auth.isAuthenticated}
				{@render stateCard(Clock, 'Too many attempts', 'Try again in a few minutes.', 'warning')}
				<Card.Footer class="justify-end">
					<Button size="sm" variant="outline" onclick={() => void runPreview()}>Try again</Button>
				</Card.Footer>
			{:else if phase === 'throttled'}
				{@render stateCard(Lock, 'Log in to continue', 'You need to be signed in to open this invite.')}
				<Card.Footer class="justify-end">
					<Button href={LOGIN} size="sm">Log in</Button>
				</Card.Footer>
			{:else if phase === 'evaluator'}
				{@render stateCard(TriangleAlert, 'Not available', 'Evaluator accounts cannot join a workspace.', 'warning')}
				<Card.Footer class="justify-end">
					<Button href="/eval" size="sm" variant="outline">Go to evaluations</Button>
				</Card.Footer>
			{:else if phase === 'error'}
				<Card.Content>
					<ErrorAlert message={loadError} onretry={() => void runPreview()} />
				</Card.Content>
			{:else if preview && preview.state === 'own_invite'}
				{@render stateCard(Users, 'This is your own invite link', 'Send it to the person you want to work with.')}
				<Card.Footer class="justify-end">
					<Button href="/users/settings#coauthors" size="sm" variant="outline">Manage co-authors</Button>
				</Card.Footer>
			{:else if preview && preview.state === 'already_member'}
				{@render stateCard(UserCheck, 'You already have access', `You can already work in ${inviter}'s workspace.`)}
				{#if acceptError}<Card.Content><ErrorAlert message={acceptError} /></Card.Content>{/if}
				<Card.Footer class="justify-end">
					<Button size="sm" onclick={openWorkspace} disabled={opening} aria-busy={opening}>
						{#if opening}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
						Open workspace
					</Button>
				</Card.Footer>
			{:else if preview && !auth.isAuthenticated}
				<!-- Valid link, signed out -->
				<Card.Header class="gap-1.5">
					<p class="page-eyebrow">Invitation</p>
					<Card.Title><h1 class="text-xl break-words">Join {inviter}'s workspace</h1></Card.Title>
				</Card.Header>
				<Card.Content class="space-y-3 text-sm">
					<p class="text-muted-foreground">
						{#if hasName}
							<span class="font-medium text-foreground">{preview.inviter_name}</span>
							(<span class="font-mono text-[13px] text-foreground">{preview.inviter_email}</span>)
						{:else}
							<span class="font-mono text-[13px] text-foreground">{preview.inviter_email}</span>
						{/if}
						invited you to work with them in LLM Lab as a co-author. {preview.can_sign_up
							? 'Create an account or log in to continue.'
							: 'Log in to continue.'}
					</p>
					{#if !preview.can_sign_up}
						<p class="text-muted-foreground">New accounts are closed on this site. Ask an administrator for one.</p>
					{/if}
				</Card.Content>
				<Card.Footer class="flex-col items-stretch gap-3 sm:items-end">
					{#if preview.can_sign_up}
						<Button href="/auth/signup" size="sm" class="w-full sm:w-auto">Create account</Button>
						<p class="text-center text-sm text-muted-foreground sm:text-right">
							Already have an account?
							<a href={LOGIN} class="font-medium text-primary underline-offset-4 hover:underline">Log in</a>
						</p>
					{:else}
						<Button href={LOGIN} size="sm" class="w-full sm:w-auto">Log in</Button>
					{/if}
				</Card.Footer>
			{:else if preview}
				<!-- Valid link, signed in: the explicit confirm. -->
				<Card.Header class="gap-1.5">
					<p class="page-eyebrow">Invitation</p>
					<Card.Title><h1 class="text-xl break-words">Join {inviter}'s workspace?</h1></Card.Title>
				</Card.Header>
				<Card.Content class="space-y-4 text-sm">
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 rounded-md border border-border bg-muted/40 px-3 py-2.5">
						<dt class="text-muted-foreground">Their account</dt>
						<dd class="min-w-0 font-mono text-[13px] break-all" data-testid="inviter-email">{preview.inviter_email}</dd>
						<dt class="text-muted-foreground">Signed in as</dt>
						<dd class="min-w-0 font-mono text-[13px] break-all" data-testid="signed-in-as">{auth.user?.email}</dd>
					</dl>
					<ul class="list-disc space-y-1.5 pl-5 text-muted-foreground marker:text-muted-foreground/60">
						<li>
							You will see and can change everything in their workspace: experiments, applications, analyses, reports,
							models, keys and budgets.
						</li>
						<li>
							What you do there is saved as their work, and they can see what you changed. Keys and data you add there
							belong to their workspace, not to your account.
						</li>
						<li>You cannot change their password or sign-in settings, or invite other people.</li>
						<li>They can remove you at any time, and you can leave at any time.</li>
						<li class="font-medium text-foreground">
							Only join if you know this person. A bar at the top always shows when you are working in their
							workspace.
						</li>
					</ul>
					{#if acceptError}<ErrorAlert message={acceptError} />{/if}
				</Card.Content>
				<Card.Footer class="flex-col-reverse gap-2 sm:flex-row sm:justify-end">
					<Button size="sm" variant="outline" class="w-full sm:w-auto" onclick={notNow} disabled={accepting}>Not now</Button>
					<Button size="sm" class="w-full sm:w-auto" onclick={join} disabled={accepting} aria-busy={accepting}>
						{#if accepting}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
						Join workspace
					</Button>
				</Card.Footer>
			{/if}
		</Card.Root>

		<div class="flex items-center justify-between">
			<a href="/" class="back-link sm:text-sm" onclick={leaveHome}>
				<ArrowLeft class="size-3 sm:size-3.5" aria-hidden="true" />
				Back to home
			</a>
			<ThemeToggle />
		</div>
	</div>
</div>
