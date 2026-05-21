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

	onMount(async () => {
		await ensureCsrfCookie();
		auth.checkSession();
		// Signal to the hydration watchdog that JS loaded successfully
		(window as any).__sveltekit_hydrated = true;
	});
</script>

<svelte:head>
	<title>LLM Lab</title>
	<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
	<meta http-equiv="Pragma" content="no-cache" />
	<meta http-equiv="Expires" content="0" />
</svelte:head>

<ModeWatcher defaultMode="dark" />

<div class="min-h-screen">
	{@render children()}
</div>

<Toaster />
<CookieBanner />
