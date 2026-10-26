<script lang="ts">
	/**
	 * ProvidersSection — one-click cloud LLM providers (card grid). Connecting
	 * creates a direct inference endpoint (staff-only: `can_connect`).
	 */
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { formatApiError } from '$lib/api/core';
	import { listProviders, type ProviderConnectResult, type ProviderPreset } from '$lib/api/integrations';
	import ProviderConnectDialog from './ProviderConnectDialog.svelte';
	import Cloud from '@lucide/svelte/icons/cloud';
	import Plus from '@lucide/svelte/icons/plus';
	import Lock from '@lucide/svelte/icons/lock';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	let providers = $state<ProviderPreset[]>([]);
	let loading = $state(true);
	let error = $state('');
	let dialogOpen = $state(false);
	let selected = $state<ProviderPreset | null>(null);

	async function load(silent = false) {
		if (!silent) loading = true;
		error = '';
		try {
			providers = await listProviders();
		} catch (e) {
			error = formatApiError(e, 'Could not load providers.');
		} finally {
			loading = false;
		}
	}

	function openConnect(p: ProviderPreset) {
		selected = p;
		dialogOpen = true;
	}

	function connected(result: ProviderConnectResult) {
		const hidden = result.hidden_count ? ` (${result.hidden_count} non-chat hidden)` : '';
		if (result.inventory_error) {
			toast.warning(`Connected ${result.name}, but the model list failed: ${result.inventory_error}`);
		} else {
			toast.success(`Connected ${result.name}: ${result.model_count} models${hidden}.`);
		}
		void load(true);
	}

	onMount(() => void load());
</script>

<div class="space-y-4">
	<p class="text-sm text-muted-foreground">
		Connect a cloud provider with its API key: LLM Lab creates an endpoint and imports its models, ready for
		generation and experiments.
	</p>

	{#if error}
		<ErrorAlert message={error} onretry={() => load()} />
	{:else if loading}
		<LoadingState label="loading providers" />
	{:else if providers.length === 0}
		<EmptyState icon={Cloud} title="No providers available" description="This deployment has no provider presets." />
	{:else}
		<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
			{#each providers as p (p.id)}
				<Card.Root class="flex flex-col">
					<Card.Header>
						<Card.Title class="flex items-center justify-between gap-2">
							<span>{p.name}</span>
							{#if p.connected_endpoints.length > 0}
								<StatusBadge status="connected" tone="success" />
							{/if}
						</Card.Title>
						<Card.Description>{p.description}</Card.Description>
					</Card.Header>
					<Card.Content class="flex flex-1 flex-col gap-3">
						<p class="truncate font-mono text-xs text-muted-foreground" title={p.base_url}>{p.base_url}</p>
						{#if p.connected_endpoints.length > 0}
							<ul class="space-y-1.5">
								{#each p.connected_endpoints as ep (ep.id)}
									<li class="flex flex-wrap items-center gap-2 text-sm">
										<a href="/endpoints/{ep.id}" class="font-medium underline-offset-2 hover:underline">{ep.name}</a>
										<span class="font-mono text-xs text-muted-foreground tabular-nums">{ep.model_count} models</span>
										{#if !ep.enabled}<StatusBadge status="disabled" tone="neutral" />{/if}
										{#if ep.last_error}
											<p class="w-full truncate text-xs text-danger-fg" title={ep.last_error}>{ep.last_error}</p>
										{/if}
									</li>
								{/each}
							</ul>
						{/if}
						<div class="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1">
							<div class="flex gap-3 text-xs">
								{#if p.keys_url}
									<a
										href={p.keys_url}
										target="_blank"
										rel="noopener noreferrer"
										class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
									>
										Get a key<ExternalLink class="size-3" aria-hidden="true" />
									</a>
								{/if}
								{#if p.docs_url}
									<a
										href={p.docs_url}
										target="_blank"
										rel="noopener noreferrer"
										class="inline-flex items-center gap-1 text-muted-foreground underline-offset-2 hover:underline"
									>
										Docs<ExternalLink class="size-3" aria-hidden="true" />
									</a>
								{/if}
							</div>
							{#if p.can_connect}
								<Button size="sm" variant={p.connected_endpoints.length ? 'outline' : 'default'} onclick={() => openConnect(p)}>
									<Plus />{p.connected_endpoints.length ? 'Add another' : 'Connect'}
								</Button>
							{:else}
								<span class="inline-flex items-center gap-1 text-xs text-muted-foreground" title="Direct endpoints are created by staff">
									<Lock class="size-3.5" aria-hidden="true" />Staff only
								</span>
							{/if}
						</div>
					</Card.Content>
				</Card.Root>
			{/each}
		</div>
	{/if}
</div>

<ProviderConnectDialog bind:open={dialogOpen} provider={selected} onconnected={connected} />
