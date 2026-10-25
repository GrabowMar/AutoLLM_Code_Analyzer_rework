<script lang="ts">
	import { onMount } from 'svelte';
	import { listEndpoints, endpointChannel } from '$lib/api/inference';
	import type { EndpointSummary } from '$lib/api/types/inference';
	import { getCapacity, CAPACITY_CHANNEL } from '$lib/api/execution';
	import type { Capacity } from '$lib/api/types/execution';
	import type { CapacityEvent } from '$lib/api/types/events';
	import { getMe } from '$lib/api/users';
	import { formatApiError } from '$lib/api/core';
	import { subscribe } from '$lib/api/sse';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Button } from '$lib/components/ui/button';
	import { CapacityCard, EndpointCard, applyEndpointEvent } from '$lib/components/inference';
	import Plus from '@lucide/svelte/icons/plus';
	import Server from '@lucide/svelte/icons/server';

	const POLL_MS = 30_000;
	const MAX_CHANNELS = 20;

	let endpoints = $state<EndpointSummary[] | null>(null);
	let capacity = $state<Capacity | null>(null);
	let error = $state('');
	let isStaff = $state(false);
	let now = $state(Date.now());

	const local = $derived((endpoints ?? []).filter((e) => !e.is_system));
	const system = $derived((endpoints ?? []).filter((e) => e.is_system));
	const ordered = $derived([...local, ...system]);
	// One SSE connection for every visible endpoint (≤ 20 channels) plus capacity.
	const channelKey = $derived(
		[CAPACITY_CHANNEL, ...ordered.slice(0, MAX_CHANNELS - 1).map((e) => endpointChannel(e.id))].join(',')
	);

	async function loadEndpoints() {
		try {
			endpoints = await listEndpoints({ includeSystem: true });
			error = '';
		} catch (e) {
			// A failed silent refresh keeps the previous render.
			if (endpoints === null) error = formatApiError(e, 'Could not load endpoints.');
		}
	}

	async function loadCapacity() {
		try {
			capacity = await getCapacity();
		} catch {
			// The card keeps its last value; the next poll or event retries.
		}
	}

	function refreshAll() {
		void loadEndpoints();
		void loadCapacity();
	}

	onMount(() => {
		refreshAll();
		getMe()
			.then((me) => (isStaff = me.is_staff ?? false))
			.catch(() => (isStaff = false));
		const poll = setInterval(refreshAll, POLL_MS);
		const clock = setInterval(() => (now = Date.now()), 5_000);
		const onVisible = () => {
			if (document.visibilityState === 'visible') refreshAll();
		};
		document.addEventListener('visibilitychange', onVisible);
		return () => {
			clearInterval(poll);
			clearInterval(clock);
			document.removeEventListener('visibilitychange', onVisible);
		};
	});

	$effect(() => {
		const channels = channelKey.split(',');
		return subscribe(channels, (event) => {
			const channel = String(event.data.channel ?? '');
			if (event.type === 'capacity') {
				const data = event.data as unknown as CapacityEvent;
				if (capacity) {
					capacity = {
						...capacity,
						lanes: data.lanes ?? capacity.lanes,
						executor: { ...capacity.executor, ...data.executor },
						limits: { ...capacity.limits, warnings: data.warnings ?? capacity.limits.warnings },
					};
				}
				return;
			}
			const match = /^endpoint:(\d+)/.exec(channel);
			if (!match || !endpoints) return;
			const id = Number(match[1]);
			if (event.type === 'session') {
				// Sessions changed: refetch for hostnames, counts and status.
				void loadEndpoints();
				return;
			}
			endpoints = endpoints.map((e) => (e.id === id ? applyEndpointEvent(e, event.data) : e));
		});
	});
</script>

<div class="space-y-6">
	<PageHeader title="Endpoints" description="Where models run: OpenRouter and your own Ollama / OpenAI-compatible servers.">
		{#snippet actions()}
			<Button href="/endpoints/new" size="sm"><Plus />Add endpoint</Button>
		{/snippet}
	</PageHeader>

	<CapacityCard {capacity} {isStaff} {now} onchanged={loadCapacity} />

	{#if error}
		<ErrorAlert message={error} onretry={loadEndpoints} />
	{:else if endpoints === null}
		<LoadingState label="loading endpoints" />
	{:else}
		{#if local.length === 0}
			<EmptyState
				icon={Server}
				title="No local endpoints yet"
				description="Run open models on your own GPU machine: add an Ollama or OpenAI-compatible server and connect it with the relay."
			>
				{#snippet action()}
					<Button href="/endpoints/new" size="sm"><Plus />Add endpoint</Button>
				{/snippet}
			</EmptyState>
		{/if}
		{#if ordered.length > 0}
			<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
				{#each ordered as endpoint (endpoint.id)}
					<EndpointCard {endpoint} {now} onchanged={loadEndpoints} />
				{/each}
			</div>
		{/if}
	{/if}
</div>
