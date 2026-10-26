<script lang="ts">
	/**
	 * WebhookDeliveries — the 50 most recent deliveries of one webhook.
	 * Props: `subscriptionId`, `refreshKey` (bump to reload, e.g. after a test).
	 */
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { formatApiError } from '$lib/api/core';
	import { listWebhookDeliveries, type WebhookDelivery } from '$lib/api/integrations';
	import type { Tone } from '$lib/constants/colors';
	import History from '@lucide/svelte/icons/history';

	interface Props {
		subscriptionId: number;
		refreshKey?: number;
	}

	let { subscriptionId, refreshKey = 0 }: Props = $props();

	const tones: Partial<Record<string, Tone>> = { sent: 'success', failed: 'danger', pending: 'info' };

	let deliveries = $state<WebhookDelivery[]>([]);
	let loaded = $state(false);
	let error = $state('');

	async function load(id: number) {
		error = '';
		try {
			deliveries = await listWebhookDeliveries(id);
		} catch (e) {
			error = formatApiError(e, 'Could not load deliveries.');
		} finally {
			loaded = true;
		}
	}

	$effect(() => {
		void refreshKey;
		void load(subscriptionId);
	});

	function when(iso: string): string {
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
	}
</script>

{#if error}
	<ErrorAlert message={error} onretry={() => load(subscriptionId)} />
{:else if !loaded}
	<LoadingState label="loading deliveries" />
{:else if deliveries.length === 0}
	<EmptyState compact icon={History} title="No deliveries yet" description="Send a test or wait for a subscribed event." />
{:else}
	<div class="overflow-x-auto">
		<table class="w-full">
			<thead>
				<tr>
					<th>When</th>
					<th>Event</th>
					<th>Status</th>
					<th class="num">HTTP</th>
					<th class="num">Attempts</th>
					<th>Error</th>
				</tr>
			</thead>
			<tbody>
				{#each deliveries as d (d.id)}
					<tr>
						<td class="whitespace-nowrap text-xs">{when(d.sent_at ?? d.created_at)}</td>
						<td><code class="font-mono text-xs">{d.event_type}</code></td>
						<td><StatusBadge status={d.status} {tones} /></td>
						<td class="num">
							{#if d.response_code != null}{d.response_code}{:else}<span class="text-muted-foreground">—</span>{/if}
						</td>
						<td class="num">{d.attempts}</td>
						<td class="max-w-xs truncate text-xs text-muted-foreground" title={d.error || undefined}>
							{d.error || '—'}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
