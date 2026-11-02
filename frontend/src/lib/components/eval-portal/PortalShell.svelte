<script lang="ts">
	/**
	 * PortalShell — the participant portal's frame (no app sidebar): a sticky top
	 * bar with the brand mark (home = /eval), the current task title
	 * (`portalTitle`), who is using the portal (participant label of a link / code
	 * session, the account's email), "Leave" (link / code sessions: ends the
	 * cookie session after a confirmation) or "Sign out" (accounts: ends the
	 * cookie session and the account session), and the theme toggle; then a wide
	 * main column on the graph-paper canvas. Loads the portal session on mount.
	 *
	 * Props: `children`.
	 */
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import LogOut from '@lucide/svelte/icons/log-out';
	import UserRound from '@lucide/svelte/icons/user-round';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import BrandMark from '$lib/components/BrandMark.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { Button } from '$lib/components/ui/button';
	import { portalTitle } from './portal-title.svelte';
	import { getPortalSession } from './portal-session.svelte';
	import { PORTAL_HOME, portalErrorMessage } from './portal';

	let { children }: { children: Snippet } = $props();

	const session = getPortalSession();
	let confirmLeave = $state(false);
	let busy = $state(false);

	const me = $derived(session.me);
	const isAccount = $derived(!!me?.user);
	const isResearcher = $derived(!!me?.user && !me.user.is_evaluator);
	const documentTitle = $derived(
		portalTitle.current ? `${portalTitle.current} · Evaluation · LLM Lab` : 'Evaluation · LLM Lab'
	);

	onMount(() => {
		void session.refresh();
	});

	async function signOut() {
		busy = true;
		try {
			await session.signOut();
			toast.success(isAccount ? 'Signed out' : 'You left this session. Use your access code to come back.');
		} catch (e) {
			toast.error(portalErrorMessage(e, 'Could not sign out completely. Close the browser to end the session.'));
		} finally {
			busy = false;
			confirmLeave = false;
		}
		await goto(PORTAL_HOME);
	}
</script>

<svelte:head>
	<title>{documentTitle}</title>
</svelte:head>

<div class="lab-canvas flex min-h-dvh flex-col">
	<a
		href="#portal-main"
		class="sr-only z-50 rounded-md bg-card px-3 py-2 text-sm font-medium shadow focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
	>
		Skip to content
	</a>
	<header class="sticky top-0 z-30 border-b border-border bg-background">
		<div class="mx-auto flex h-12 w-full max-w-[1600px] items-center gap-2 px-3 sm:gap-3 sm:px-4 md:px-5">
			<a
				href={PORTAL_HOME}
				class="flex shrink-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
				aria-label="Evaluation portal home"
			>
				<BrandMark compact />
				<span class="hidden font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground sm:inline">
					Evaluation
				</span>
			</a>
			{#if portalTitle.current}
				<span class="hidden h-5 w-px shrink-0 bg-border sm:block" aria-hidden="true"></span>
				<p class="min-w-0 truncate font-display text-[15px] font-semibold" title={portalTitle.current}>
					{portalTitle.current}
				</p>
			{/if}

			<div class="ml-auto flex shrink-0 items-center gap-1 sm:gap-1.5">
				{#if me?.participant || me?.user}
					<span
						class="hidden min-w-0 items-center gap-1.5 rounded-full border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground md:inline-flex"
						title={me.user ? `Signed in as ${me.user.email}` : 'Participant session in this browser'}
					>
						<UserRound class="size-3.5 shrink-0" aria-hidden="true" />
						{#if me.participant}
							<span class="font-mono font-medium text-foreground">{me.participant.label}</span>
						{/if}
						{#if me.participant && me.user}<span aria-hidden="true">·</span>{/if}
						{#if me.user}
							<span class="max-w-[16rem] truncate">{me.user.email}</span>
						{/if}
					</span>
				{/if}
				{#if isResearcher}
					<Button variant="ghost" size="sm" href="/" class="h-8 gap-1.5 px-2 text-xs" title="Open LLM Lab">
						<LayoutDashboard class="size-3.5" aria-hidden="true" />
						<span class="hidden lg:inline">LLM Lab</span>
						<span class="sr-only lg:hidden">Open LLM Lab</span>
					</Button>
				{/if}
				{#if isAccount}
					<Button
						variant="ghost"
						size="sm"
						class="h-8 gap-1.5 px-2 text-xs"
						disabled={busy}
						aria-busy={busy}
						onclick={signOut}
					>
						<LogOut class="size-3.5" aria-hidden="true" />
						Sign out
					</Button>
				{:else if me?.participant}
					<Button
						variant="ghost"
						size="sm"
						class="h-8 gap-1.5 px-2 text-xs"
						disabled={busy}
						onclick={() => (confirmLeave = true)}
					>
						<LogOut class="size-3.5" aria-hidden="true" />
						Leave
					</Button>
				{/if}
				<ThemeToggle />
			</div>
		</div>
	</header>

	<main id="portal-main" class="mx-auto w-full min-w-0 max-w-[1600px] flex-1 p-3 sm:p-4 md:p-5">
		{@render children()}
	</main>
</div>

<ConfirmDialog
	bind:open={confirmLeave}
	title="Leave this session?"
	description={`This browser stops being signed in as ${me?.participant?.label ?? 'this participant'}. Your saved work is kept; you need your access code to continue later.`}
	confirmLabel="Leave"
	{busy}
	onConfirm={signOut}
/>
