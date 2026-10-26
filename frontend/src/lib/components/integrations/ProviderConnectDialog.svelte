<script lang="ts">
	/**
	 * ProviderConnectDialog — paste an API key, optionally test it, connect.
	 *
	 * Props: `open` (bindable), `provider` (the preset), `onconnected(result)`
	 * after a 201. The key only lives in this dialog's state and is cleared on
	 * close.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		connectProvider,
		testProviderKey,
		type ProviderConnectResult,
		type ProviderKeyTest,
		type ProviderPreset,
	} from '$lib/api/integrations';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	interface Props {
		open?: boolean;
		provider: ProviderPreset | null;
		onconnected?: (result: ProviderConnectResult) => void;
	}

	let { open = $bindable(false), provider, onconnected }: Props = $props();

	let apiKey = $state('');
	let name = $state('');
	let testing = $state(false);
	let connecting = $state(false);
	let test = $state<ProviderKeyTest | null>(null);
	let error = $state('');

	// Fresh form for every provider / every open.
	$effect(() => {
		if (open) {
			void provider?.id;
			apiKey = '';
			name = '';
			test = null;
			error = '';
		}
	});

	const busy = $derived(testing || connecting);

	async function runTest() {
		if (!provider || !apiKey.trim()) return;
		testing = true;
		error = '';
		test = null;
		try {
			test = await testProviderKey(provider.id, apiKey.trim());
		} catch (e) {
			error = formatApiError(e, 'Could not test the key.');
		} finally {
			testing = false;
		}
	}

	async function connect(event?: SubmitEvent) {
		event?.preventDefault();
		if (!provider || !apiKey.trim()) return;
		connecting = true;
		error = '';
		try {
			const result = await connectProvider(provider.id, {
				api_key: apiKey.trim(),
				...(name.trim() ? { name: name.trim() } : {}),
			});
			apiKey = '';
			open = false;
			onconnected?.(result);
		} catch (e) {
			error = formatApiError(e, 'Could not connect the provider.');
		} finally {
			connecting = false;
		}
	}
</script>

<Dialog.Root
	{open}
	onOpenChange={(next) => {
		if (!busy || next) open = next;
		if (!next) apiKey = '';
	}}
>
	<Dialog.Content class="max-w-lg">
		{#if provider}
			<Dialog.Header>
				<Dialog.Title>Connect {provider.name}</Dialog.Title>
				<Dialog.Description>
					Creates an endpoint for <span class="font-mono">{provider.base_url}</span> and imports its chat and code
					models. The key is encrypted at rest and never shown again.
				</Dialog.Description>
			</Dialog.Header>
			<form class="mt-4 space-y-4" onsubmit={connect}>
				<div class="space-y-1.5">
					<div class="flex items-center justify-between gap-2">
						<Label for="provider-key">API key</Label>
						{#if provider.keys_url}
							<a
								href={provider.keys_url}
								target="_blank"
								rel="noopener noreferrer"
								class="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
							>
								Get a key<ExternalLink class="size-3" aria-hidden="true" />
							</a>
						{/if}
					</div>
					<Input
						id="provider-key"
						type="password"
						bind:value={apiKey}
						autocomplete="off"
						spellcheck={false}
						disabled={busy}
						oninput={() => (test = null)}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="provider-name">Endpoint name <span class="text-muted-foreground">(optional)</span></Label>
					<Input id="provider-name" bind:value={name} maxlength={120} placeholder={provider.name} disabled={busy} />
				</div>

				{#if test}
					<div
						class="space-y-1 rounded-md border p-3 text-sm {test.ok
							? 'border-success/30 bg-success/10'
							: 'border-destructive/30 bg-destructive/10'}"
						role="status"
					>
						<p class="flex items-start gap-1.5 {test.ok ? 'text-success-fg' : 'text-danger-fg'}">
							{#if test.ok}<CircleCheck class="mt-0.5 size-4 shrink-0" />{:else}<CircleX class="mt-0.5 size-4 shrink-0" />{/if}
							<span>{test.message}</span>
						</p>
						{#if test.ok}
							<p class="text-xs text-muted-foreground">
								<span class="font-mono tabular-nums">{test.model_count}</span> chat/code models of
								<span class="font-mono tabular-nums">{test.total_models}</span> listed
							</p>
							{#if test.sample_model_ids.length > 0}
								<p class="flex flex-wrap gap-1">
									{#each test.sample_model_ids as id (id)}<code class="mono-tag">{id}</code>{/each}
								</p>
							{/if}
						{/if}
					</div>
				{/if}

				{#if error}<ErrorAlert message={error} />{/if}

				<p class="text-xs text-muted-foreground">
					Defaults: <span class="font-mono tabular-nums">{provider.max_concurrency}</span> concurrent requests,
					<span class="font-mono tabular-nums">{provider.request_timeout_s}</span> s timeout. Change them on the endpoint
					page afterwards.
				</p>

				<Dialog.Footer class="gap-2">
					<Button type="button" variant="outline" size="sm" onclick={runTest} disabled={busy || !apiKey.trim()}>
						{#if testing}<LoaderCircle class="animate-spin" />{/if}Test
					</Button>
					<Button type="submit" size="sm" disabled={busy || !apiKey.trim()} aria-busy={connecting}>
						{#if connecting}<LoaderCircle class="animate-spin" />{/if}Connect
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
