<script lang="ts">
	import { goto } from '$app/navigation';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import { getWorkspace } from '$lib/stores/workspace.svelte';
	import { pendingInvite } from '$lib/coauthors/pending-invite';
	import AppSidebar from '$lib/components/AppSidebar.svelte';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import WorkspaceBanner from '$lib/components/coauthors/WorkspaceBanner.svelte';
	import SpendChip from '$lib/components/layout/SpendChip.svelte';
	import AppFooter from '$lib/components/AppFooter.svelte';
	import { Button } from '$lib/components/ui/button';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { getMe } from '$lib/api/users';
	import { onMount, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';

	let { children } = $props();
	const auth = getAuth();
	const prefs = getPreferences();
	const workspace = getWorkspace();
	let loadingTooLong = $state(false);

	// Restricted evaluator accounts only use the evaluation portal: check the
	// role once per signed-in user and send evaluators to /eval without
	// rendering the shell (its widgets call APIs evaluators may not use).
	let roleCheckedFor = $state<string | null>(null);
	let isEvaluator = $state(false);
	let roleCheckingFor: string | null = null;
	const userKey = $derived(auth.user?.email ?? '');
	const roleKnown = $derived(auth.isAuthenticated && roleCheckedFor === userKey);
	// The co-author context loads alongside the role check, so the shell renders
	// once, already knowing whose workspace it shows (no banner popping in later).
	const waiting = $derived(
		auth.isLoading || (auth.isAuthenticated && (!roleKnown || isEvaluator || !workspace.loaded)),
	);
	const actingAs = $derived(workspace.actingAs);
	let invitePending = false;

	$effect(() => {
		if (auth.isLoading || !auth.isAuthenticated) return;
		const key = userKey;
		untrack(() => {
			if (roleCheckedFor === key || roleCheckingFor === key) return;
			roleCheckingFor = key;
			// Never throws; an evaluator's 403 leaves the safe empty state. Reset first:
			// the store outlives this layout, e.g. across a sign-out and sign-in.
			workspace.reset();
			void workspace.load().then(() => {
				if (workspace.consumeEnded()) {
					toast.info('You no longer have access to that workspace. You are back in your own account.');
				}
			});
			getMe()
				.then((me) => {
					if (roleCheckingFor !== key) return;
					isEvaluator = me.is_evaluator === true;
					roleCheckedFor = key;
					if (isEvaluator) void goto('/eval', { replace: true });
					else continueInvite();
				})
				.catch(() => {
					if (roleCheckingFor !== key) return;
					// Fail open to the researcher shell: the server still scopes evaluator accounts.
					isEvaluator = false;
					roleCheckedFor = key;
					continueInvite();
				});
		});
	});

	/** An invite opened before signing up / logging in: back to its confirm screen, once per mount. */
	function continueInvite() {
		if (invitePending || !pendingInvite()) return;
		invitePending = true;
		void goto('/join');
	}

	// Desktop sidebar widths — kept in sync with AppSidebar widths.
	// Returns "0px" on mobile via media query (handled by responsive padding below).
	let sidebarOffset = $derived(prefs.sidebarCollapsed ? '3.5rem' : '15rem');

	onMount(() => {
		const timer = setTimeout(() => {
			if (waiting) {
				loadingTooLong = true;
			}
		}, 5000);
		// The workspace is chosen per session: follow a switch made in another tab.
		const stopWatching = workspace.watchOtherTabs();
		return () => {
			clearTimeout(timer);
			stopWatching();
		};
	});

	function forceReload() {
		window.location.href = window.location.pathname;
	}

	$effect(() => {
		if (!auth.isLoading && !auth.isAuthenticated) {
			goto('/auth/login');
		}
	});
</script>

{#if waiting}
	<div class="flex min-h-dvh items-center justify-center bg-background">
		{#if loadingTooLong}
			<div class="flex flex-col items-center gap-4 text-center">
				<p class="text-muted-foreground">Loading is taking longer than expected.</p>
				<Button size="sm" onclick={forceReload}>Reload page</Button>
			</div>
		{:else}
			<LoaderCircle class="size-7 animate-spin text-primary" aria-label="Loading" />
		{/if}
	</div>
{:else if auth.isAuthenticated}
	<div
		class="lab-canvas flex min-h-screen"
		data-density={prefs.compactTables ? 'compact' : 'comfortable'}
		data-workspace-banner={actingAs ? '' : undefined}
		style:--app-sidebar-offset={sidebarOffset}
		style:--workspace-banner-h={actingAs ? '2.5rem' : undefined}
	>
		<AppSidebar />
		<div
			class="flex flex-1 flex-col min-w-0 transition-[padding] duration-200 ease-out motion-reduce:transition-none md:pl-[var(--app-sidebar-offset)]"
		>
			<AppHeader {auth}>
				{#snippet actions()}
					<!-- Header spend chip (WP-15), left of the theme toggle and account menu. -->
					<div class="hidden sm:flex"><SpendChip /></div>
				{/snippet}
			</AppHeader>
			{#if actingAs}
				<!-- In flow under the fixed app bar: it supplies the 3rem header offset itself. -->
				<WorkspaceBanner workspace={actingAs} />
				<div class="flex min-h-[calc(100dvh-8.5rem)] flex-1 flex-col p-3 sm:p-4 md:p-5 min-w-0 overflow-x-clip">
					{@render children()}
				</div>
			{:else}
				<div class="flex min-h-[calc(100dvh-3rem)] flex-1 flex-col p-3 sm:p-4 md:p-5 min-w-0 overflow-x-clip pt-[calc(3rem+0.75rem)] sm:pt-[calc(3rem+1rem)] md:pt-[calc(3rem+1.25rem)]">
					{@render children()}
				</div>
			{/if}
			<AppFooter />
		</div>
	</div>
{/if}

<style>
	/*
	 * While the workspace banner shows, page-level sticky elements (section rails,
	 * chip bars, previews) sit below it instead of sliding under it. `top` only
	 * applies while an element is sticky, so the breakpoint variants are matched
	 * inside the same breakpoint.
	 */
	[data-workspace-banner] :global(.sticky.top-12) {
		top: calc(3rem + var(--workspace-banner-h));
	}
	[data-workspace-banner] :global(.sticky.top-16) {
		top: calc(4rem + var(--workspace-banner-h));
	}
	[data-workspace-banner] :global(.sticky.top-20) {
		top: calc(5rem + var(--workspace-banner-h));
	}
	[data-workspace-banner] :global(.scroll-mt-28) {
		scroll-margin-top: calc(7rem + var(--workspace-banner-h));
	}
	@media (min-width: 64rem) {
		[data-workspace-banner] :global([class~='lg:sticky'][class~='lg:top-16']) {
			top: calc(4rem + var(--workspace-banner-h));
		}
		[data-workspace-banner] :global([class~='lg:sticky'][class~='lg:top-[4.25rem]']) {
			top: calc(4.25rem + var(--workspace-banner-h));
		}
		[data-workspace-banner] :global([class~='lg:sticky'][class~='lg:top-[4.5rem]']) {
			top: calc(4.5rem + var(--workspace-banner-h));
		}
		[data-workspace-banner] :global([class~='lg:scroll-mt-20']) {
			scroll-margin-top: calc(5rem + var(--workspace-banner-h));
		}
	}
	@media (min-width: 80rem) {
		[data-workspace-banner] :global([class~='xl:sticky'][class~='xl:top-16']) {
			top: calc(4rem + var(--workspace-banner-h));
		}
		[data-workspace-banner] :global([class~='xl:sticky'][class~='xl:top-20']) {
			top: calc(5rem + var(--workspace-banner-h));
		}
		[data-workspace-banner] :global([class~='xl:sticky'][class~='xl:top-[4.25rem]']) {
			top: calc(4.25rem + var(--workspace-banner-h));
		}
	}
	@container composer (min-width: 56rem) {
		[data-workspace-banner] :global([class~='@4xl/composer:sticky'][class~='@4xl/composer:top-[4.5rem]']) {
			top: calc(4.5rem + var(--workspace-banner-h));
		}
	}
</style>
