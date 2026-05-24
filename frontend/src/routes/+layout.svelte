<script lang="ts">
	import '../app.css';
	import { ensureCsrfCookie } from '$lib/api/core';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { Toaster } from '$lib/components/ui/sonner';
	import { ModeWatcher } from 'mode-watcher';
	import CookieBanner from '$lib/components/CookieBanner.svelte';
	import { onMount } from 'svelte';

	let { children } = $props();
	const auth = getAuth();

	onMount(() => {
		(window as typeof window & { __sveltekit_hydrated?: boolean }).__sveltekit_hydrated =
			true;

		void (async () => {
			try {
				await auth.checkSession();
			} catch (err) {
				if (import.meta.env.DEV) {
					console.warn('Session bootstrap failed:', err);
				}
			}
		})();
	});
</script>

<svelte:head>
	<title>LLM Lab</title>
</svelte:head>

<ModeWatcher defaultMode="dark" />

<div class="min-h-screen">
	{@render children()}
</div>

<Toaster />
<CookieBanner />
