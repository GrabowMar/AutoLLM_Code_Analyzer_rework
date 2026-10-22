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
	<div class="flex items-center gap-2 rounded-md border border-border px-4 py-2.5 text-sm text-muted-foreground" role="status">
		<LoaderCircle class="h-4 w-4 animate-spin shrink-0" />
		Checking OpenRouter API key…
	</div>
{:else if !status?.configured && !status?.global_fallback_available}
	<div class="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm">
		<div class="flex items-start gap-2">
			<Key class="h-4 w-4 shrink-0 text-warning-fg mt-0.5" aria-hidden="true" />
			<div>
				<p class="font-medium text-warning-fg">OpenRouter API key required</p>
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
	<div class="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm">
		<div class="flex items-start gap-2">
			<AlertTriangle class="h-4 w-4 shrink-0 text-danger-fg mt-0.5" aria-hidden="true" />
			<div>
				<p class="font-medium text-danger-fg">OpenRouter key invalid</p>
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
	<div class="rounded-md border border-success/30 bg-success/10 px-4 py-2.5 text-sm">
		<div class="flex flex-wrap items-center gap-2">
			<Check class="h-4 w-4 text-success-fg shrink-0" aria-hidden="true" />
			<span class="text-muted-foreground">
				Using stored OpenRouter key
				{#if status?.key_prefix}
					<code class="ml-1 rounded bg-muted px-1.5 py-0.5 text-xs">{status.key_prefix}…</code>
				{/if}
			</span>
			{#if status?.using_global_fallback}
				<span class="text-xs text-warning-fg">(deployment fallback)</span>
			{/if}
		</div>
	</div>
{/if}
