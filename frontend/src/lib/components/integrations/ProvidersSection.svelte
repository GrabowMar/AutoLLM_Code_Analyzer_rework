<script lang="ts">
	/**
	 * ProvidersSection — one-click cloud LLM providers (connected / available lists). Connecting
	 * creates a direct inference endpoint (staff-only: `can_connect`).
	 */
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import SettingsBlock from '$lib/components/users-settings/SettingsBlock.svelte';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { formatApiError } from '$lib/api/core';
	import { listProviders, type ProviderConnectResult, type ProviderPreset } from '$lib/api/integrations';
	import ProviderConnectDialog from './ProviderConnectDialog.svelte';
	import { useSectionStatus } from '$lib/components/users-settings/section-status.svelte';
	import Cloud from '@lucide/svelte/icons/cloud';
	import Plus from '@lucide/svelte/icons/plus';
	import Lock from '@lucide/svelte/icons/lock';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	let providers = $state<ProviderPreset[]>([]);
	let loading = $state(true);
	let error = $state('');
	let dialogOpen = $state(false);
	let selected = $state<ProviderPreset | null>(null);

	const connectedProviders = $derived(providers.filter((p) => p.connected_endpoints.length > 0));
	const availableProviders = $derived(providers.filter((p) => p.connected_endpoints.length === 0));

	const report = useSectionStatus('providers');
	$effect(() => {
		const n = providers.filter((p) => p.connected_endpoints.length > 0).length;
		report(n ? { tone: 'success', label: `${n} connected` } : null);
	});

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

{#if error}
	<ErrorAlert message={error} onretry={() => load()} />
{:else if loading}
	<LoadingState label="loading providers" />
{:else if providers.length === 0}
	<EmptyState icon={Cloud} title="No providers available" description="This deployment has no provider presets." />
{:else}
	{#if connectedProviders.length}
		<SettingsBlock title="Connected">
			{@render rows(connectedProviders)}
		</SettingsBlock>
	{/if}
	<SettingsBlock
		title={connectedProviders.length ? 'Available' : 'Providers'}
		description="Paste a key and LLM Lab creates an endpoint and imports the provider's chat models."
	>
		{@render rows(availableProviders)}
	</SettingsBlock>
{/if}

{#snippet rows(list: ProviderPreset[])}
	{#each list as p (p.id)}
		{@const isConnected = p.connected_endpoints.length > 0}
		<div class="settings-row flex items-start gap-3.5">
			<span
				class="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-muted/50 text-sm font-semibold"
				aria-hidden="true"
			>
				{p.name.charAt(0)}
			</span>
			<div class="min-w-0 flex-1 space-y-1">
				<p class="text-sm font-medium">{p.name}</p>
				<p class="text-[13px] leading-5 text-muted-foreground">{p.description}</p>
				{#if isConnected}
					<ul class="space-y-1 pt-1">
						{#each p.connected_endpoints as ep (ep.id)}
							<li class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px]">
								<span class="status-dot size-1.5 {ep.enabled && !ep.last_error ? 'text-success' : ep.last_error ? 'text-destructive' : 'text-muted-foreground'}" aria-hidden="true"></span>
								<a href="/endpoints/{ep.id}" class="font-medium underline-offset-2 hover:underline">{ep.name}</a>
								<span class="text-muted-foreground tabular-nums">{ep.model_count} models</span>
								{#if !ep.enabled}<span class="text-muted-foreground">· disabled</span>{/if}
								{#if ep.last_error}
									<span class="w-full truncate pl-3.5 text-xs text-danger-fg" title={ep.last_error}>{ep.last_error}</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
				<p class="flex flex-wrap gap-x-3 gap-y-0.5 pt-0.5 text-xs">
					{#if p.keys_url}
						<a href={p.keys_url} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 settings-link">
							Get a key<ExternalLink class="size-3" aria-hidden="true" />
						</a>
					{/if}
					{#if p.docs_url}
						<a
							href={p.docs_url}
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-1 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
						>
							Docs<ExternalLink class="size-3" aria-hidden="true" />
						</a>
					{/if}
				</p>
			</div>
			<div class="shrink-0">
				{#if p.can_connect}
					<Button size="sm" variant="outline" onclick={() => openConnect(p)}>
						<Plus />{isConnected ? 'Add another' : 'Connect'}
					</Button>
				{:else}
					<span class="inline-flex items-center gap-1 text-xs text-muted-foreground" title="Direct endpoints are created by staff">
						<Lock class="size-3.5" aria-hidden="true" />Staff only
					</span>
				{/if}
			</div>
		</div>
	{/each}
{/snippet}

<ProviderConnectDialog bind:open={dialogOpen} provider={selected} onconnected={connected} />
