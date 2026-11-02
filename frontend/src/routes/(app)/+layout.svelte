<script lang="ts">
	import { goto } from '$app/navigation';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import AppSidebar from '$lib/components/AppSidebar.svelte';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import AppFooter from '$lib/components/AppFooter.svelte';
	import { Button } from '$lib/components/ui/button';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { getMe } from '$lib/api/users';
	import { onMount, untrack } from 'svelte';

	let { children } = $props();
	const auth = getAuth();
	const prefs = getPreferences();
	let loadingTooLong = $state(false);

	// Restricted evaluator accounts only use the evaluation portal: check the
	// role once per signed-in user and send evaluators to /eval without
	// rendering the shell (its widgets call APIs evaluators may not use).
	let roleCheckedFor = $state<string | null>(null);
	let isEvaluator = $state(false);
	let roleCheckingFor: string | null = null;
	const userKey = $derived(auth.user?.email ?? '');
	const roleKnown = $derived(auth.isAuthenticated && roleCheckedFor === userKey);
	const waiting = $derived(auth.isLoading || (auth.isAuthenticated && (!roleKnown || isEvaluator)));

	$effect(() => {
		if (auth.isLoading || !auth.isAuthenticated) return;
		const key = userKey;
		untrack(() => {
			if (roleCheckedFor === key || roleCheckingFor === key) return;
			roleCheckingFor = key;
			getMe()
				.then((me) => {
					if (roleCheckingFor !== key) return;
					isEvaluator = me.is_evaluator === true;
					roleCheckedFor = key;
					if (isEvaluator) void goto('/eval', { replaceState: true });
				})
				.catch(() => {
					if (roleCheckingFor !== key) return;
					// Fail open to the researcher shell: the server still scopes evaluator accounts.
					isEvaluator = false;
					roleCheckedFor = key;
				});
		});
	});

	// Desktop sidebar widths — kept in sync with AppSidebar widths.
	// Returns "0px" on mobile via media query (handled by responsive padding below).
	let sidebarOffset = $derived(prefs.sidebarCollapsed ? '3.5rem' : '15rem');

	onMount(() => {
		const timer = setTimeout(() => {
			if (waiting) {
				loadingTooLong = true;
			}
		}, 5000);
		return () => clearTimeout(timer);
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
		style:--app-sidebar-offset={sidebarOffset}
	>
		<AppSidebar />
		<div
			class="flex flex-1 flex-col min-w-0 transition-[padding] duration-200 ease-out motion-reduce:transition-none md:pl-[var(--app-sidebar-offset)]"
		>
			<AppHeader {auth} />
			<div class="flex min-h-[calc(100dvh-3rem)] flex-1 flex-col p-3 sm:p-4 md:p-5 min-w-0 overflow-x-clip pt-[calc(3rem+0.75rem)] sm:pt-[calc(3rem+1rem)] md:pt-[calc(3rem+1.25rem)]">
				{@render children()}
			</div>
			<AppFooter />
		</div>
	</div>
{/if}
