<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getOpenRouterCredentialStatus,
		type OpenRouterCredentialStatus,
	} from '$lib/api/credentials';
	import Check from '@lucide/svelte/icons/check';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import Key from '@lucide/svelte/icons/key';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	let status = $state<OpenRouterCredentialStatus | null>(null);
	let loading = $state(true);

	onMount(async () => {
		try {
			status = await getOpenRouterCredentialStatus();
		} catch {
			status = null;
		} finally {
			loading = false;
		}
	});

	const isValid = $derived(
		status?.configured &&
			(status.last_validation_status === 'valid' || !status.last_validation_status),
	);
</script>

{#if loading}
	<div class="flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm text-muted-foreground">
		<LoaderCircle class="h-4 w-4 animate-spin shrink-0" />
		Checking OpenRouter API key…
	</div>
{:else if !status?.configured && !status?.global_fallback_available}
	<div class="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm">
		<div class="flex items-start gap-2">
			<Key class="h-4 w-4 shrink-0 text-amber-500 mt-0.5" />
			<div>
				<p class="font-medium text-amber-600 dark:text-amber-400">OpenRouter API key required</p>
				<p class="mt-1 text-muted-foreground">
					Generation uses your personal key from Settings. Add one before running jobs.
				</p>
				<a
					href="/users/settings#credentials"
					class="mt-2 inline-block text-xs font-medium text-primary hover:underline"
				>
					Go to API Credentials →
				</a>
			</div>
		</div>
	</div>
{:else if status?.last_validation_status === 'invalid'}
	<div class="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm">
		<div class="flex items-start gap-2">
			<AlertTriangle class="h-4 w-4 shrink-0 text-red-400 mt-0.5" />
			<div>
				<p class="font-medium text-red-400">OpenRouter key invalid</p>
				{#if status.last_validation_message}
					<p class="mt-1 text-xs text-muted-foreground">{status.last_validation_message}</p>
				{/if}
				<a href="/users/settings#credentials" class="mt-2 inline-block text-xs font-medium text-primary hover:underline">
					Update key in Settings →
				</a>
			</div>
		</div>
	</div>
{:else if isValid}
	<div class="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-2.5 text-sm">
		<div class="flex flex-wrap items-center gap-2">
			<Check class="h-4 w-4 text-emerald-500 shrink-0" />
			<span class="text-muted-foreground">
				Using stored OpenRouter key
				{#if status?.key_prefix}
					<code class="ml-1 rounded bg-muted px-1.5 py-0.5 text-xs">{status.key_prefix}…</code>
				{/if}
			</span>
			{#if status?.using_global_fallback}
				<span class="text-xs text-amber-600">(deployment fallback)</span>
			{/if}
		</div>
	</div>
{/if}
