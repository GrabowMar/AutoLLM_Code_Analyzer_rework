<script lang="ts">
	/**
	 * EndpointCard — one endpoint on the /endpoints grid.
	 *
	 * Shows name, kind, Relay/Direct, status with "last seen" and relay
	 * hostnames, request slots (SlotMeter), active jobs, queued requests, loaded
	 * models and model counts. Actions: Refresh models, Test (owner/staff), Open.
	 * The system OpenRouter row shows the key status instead of relay details.
	 *
	 * Props:
	 * - `endpoint`: `EndpointSummary` (live fields patched by the parent from SSE).
	 * - `now`: clock for relative times (pass a ticking value).
	 * - `onchanged()`: after an action the parent should refetch for.
	 */
	import { toast } from 'svelte-sonner';
	import { refreshEndpoint, runProbe } from '$lib/api/inference';
	import type { EndpointSummary, ProbeResult } from '$lib/api/types/inference';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import { formatNumber } from '$lib/utils/formatters';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import OpenRouterKeyBanner from '$lib/components/generator/OpenRouterKeyBanner.svelte';
	import SlotMeter from './SlotMeter.svelte';
	import {
		CONNECTION_LABELS,
		endpointStatusTone,
		formatAgo,
		formatBytes,
		kindLabel,
		PROBE_TONE_TEXT,
		probeSummary,
		sessionHostnames,
	} from './endpoint-display';
	import Cloud from '@lucide/svelte/icons/cloud';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Server from '@lucide/svelte/icons/server';
	import Zap from '@lucide/svelte/icons/zap';

	interface Props {
		endpoint: EndpointSummary;
		now?: number;
		onchanged?: () => void;
	}

	let { endpoint, now = Date.now(), onchanged }: Props = $props();

	let refreshing = $state(false);
	let testing = $state(false);
	let probe = $state<ProbeResult | null>(null);

	const href = $derived(`/endpoints/${endpoint.id}`);
	const hosts = $derived(sessionHostnames(endpoint));
	const lastSeen = $derived(
		(endpoint.live.sessions ?? []).reduce<string | null>(
			(latest, s) => (!latest || s.last_seen_at > latest ? s.last_seen_at : latest),
			null
		)
	);
	const statusTitle = $derived(
		endpoint.status === 'online' && lastSeen ? `Last seen ${formatAgo(lastSeen, now)}` : endpoint.status
	);

	const probeLine = $derived(probe ? probeSummary(probe) : null);

	// A probe still polling when the card goes away stops with it.
	let probeController: AbortController | null = null;
	$effect(() => () => probeController?.abort());

	async function refresh() {
		refreshing = true;
		try {
			await refreshEndpoint(endpoint.id);
			toast.success(
				endpoint.connection === 'relay'
					? 'Re-scan requested: the relay reports models on its next poll.'
					: 'Re-scan requested.'
			);
			onchanged?.();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not request a re-scan.'));
		} finally {
			refreshing = false;
		}
	}

	async function test() {
		testing = true;
		probe = null;
		probeController?.abort();
		const controller = new AbortController();
		probeController = controller;
		try {
			probe = await runProbe(endpoint.id, null, { timeoutMs: 60_000, signal: controller.signal });
		} catch (e) {
			if (isAbortError(e)) return;
			probe = { status: 'failed', latency_ms: null, error: formatApiError(e, 'Test failed.') };
		} finally {
			if (probeController === controller) {
				probeController = null;
				testing = false;
			}
		}
	}
</script>

<Card.Root class="flex h-full flex-col">
	<Card.Header class="space-y-1.5 pb-3">
		<div class="flex min-w-0 items-start justify-between gap-2">
			<div class="flex min-w-0 items-center gap-2">
				<span class="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-surface-2 text-muted-foreground" aria-hidden="true">
					{#if endpoint.kind === 'openrouter'}<Cloud class="size-4" />{:else}<Server class="size-4" />{/if}
				</span>
				<div class="min-w-0">
					<Card.Title class="truncate text-base">
						<a {href} class="hover:text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 rounded">{endpoint.name}</a>
					</Card.Title>
					<p class="truncate font-mono text-[11px] text-muted-foreground">{endpoint.slug}</p>
				</div>
			</div>
			<span title={statusTitle}>
				<StatusBadge status={endpoint.status} tone={endpointStatusTone(endpoint.status)} />
			</span>
		</div>
		<div class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
			<span class="mono-tag">{kindLabel(endpoint.kind)}</span>
			{#if !endpoint.is_system}
				<span class="mono-tag">{CONNECTION_LABELS[endpoint.connection]}</span>
			{/if}
			{#if endpoint.visibility === 'shared'}<span class="mono-tag">shared</span>{/if}
			{#if endpoint.owner && !endpoint.can_edit}<span>by {endpoint.owner.username}</span>{/if}
			{#if !endpoint.enabled}<span class="text-warning-fg">disabled</span>{/if}
		</div>
	</Card.Header>

	<Card.Content class="flex flex-1 flex-col gap-3 text-sm">
		{#if endpoint.is_system}
			<OpenRouterKeyBanner class="px-0" />
			<dl class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
				<dt class="text-muted-foreground">Agent jobs at once</dt>
				<dd class="text-right tabular-nums">{endpoint.max_active_jobs_effective.sandbox}</dd>
				<dt class="text-muted-foreground">LLM jobs at once</dt>
				<dd class="text-right tabular-nums">{endpoint.max_active_jobs_effective.llm}</dd>
				<dt class="text-muted-foreground">Active jobs</dt>
				<dd class="text-right tabular-nums">{endpoint.live.active_jobs}</dd>
			</dl>
		{:else}
			<div class="space-y-1 text-xs text-muted-foreground">
				{#if endpoint.connection === 'relay'}
					<p>
						{#if endpoint.live.session_count > 0}
							{endpoint.live.session_count === 1 ? '1 relay' : `${endpoint.live.session_count} relays`}
							{#if hosts.length > 0}on <span class="font-mono text-foreground">{hosts.join(', ')}</span>{/if}
							{#if lastSeen}· last seen {formatAgo(lastSeen, now)}{/if}
						{:else}
							No relay connected
						{/if}
					</p>
				{:else if endpoint.base_url}
					<p class="truncate font-mono" title={endpoint.base_url}>{endpoint.base_url}</p>
				{/if}
				{#if endpoint.can_edit && endpoint.last_error}
					<p class="line-clamp-2 text-danger-fg" title={endpoint.last_error}>{endpoint.last_error}</p>
				{/if}
			</div>

			<SlotMeter label="Request slots" used={endpoint.live.inflight_requests} total={endpoint.max_concurrency} />

			<dl class="grid grid-cols-3 gap-2 text-xs">
				<div>
					<dt class="text-muted-foreground">Active jobs</dt>
					<dd class="font-medium tabular-nums">{endpoint.live.active_jobs}</dd>
				</div>
				<div>
					<dt class="text-muted-foreground">Queued req.</dt>
					<dd class="font-medium tabular-nums">{endpoint.live.queued_requests}</dd>
				</div>
				<div>
					<dt class="text-muted-foreground">Tokens/s</dt>
					<dd class="font-medium tabular-nums">{formatNumber(endpoint.live.tokens_per_s_5m)}</dd>
				</div>
			</dl>

			{#if endpoint.live.loaded.length > 0}
				<ul class="flex flex-wrap gap-1" aria-label="Loaded models">
					{#each endpoint.live.loaded as m (m.name)}
						<li
							class={cn(
								'inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px]',
								m.cpu_spill ? 'border-warning/30 bg-warning/10 text-warning-fg' : 'border-border bg-muted text-muted-foreground'
							)}
							title={m.cpu_spill ? 'Partly offloaded to CPU: slower' : `In VRAM: ${formatBytes(m.size_vram)}`}
						>
							<span class="truncate">{m.name}</span>
							{#if m.cpu_spill}<span class="shrink-0 font-sans">CPU spill</span>{/if}
						</li>
					{/each}
				</ul>
			{/if}

			<p class="text-xs text-muted-foreground">
				<span class="font-medium tabular-nums text-foreground">{endpoint.models.available}</span>/{endpoint.models.total} models available ·
				<span class="tabular-nums">{endpoint.models.tools}</span> with tools
				{#if endpoint.last_inventory_at}· scanned {formatAgo(endpoint.last_inventory_at, now)}{/if}
			</p>

			{#if probeLine}
				<p class={cn('text-xs', PROBE_TONE_TEXT[probeLine.tone])} role="status">{probeLine.text}</p>
			{/if}
		{/if}

		<div class="mt-auto flex flex-wrap items-center gap-2 pt-1">
			{#if endpoint.can_edit && !endpoint.is_system}
				<Button variant="outline" size="sm" onclick={refresh} disabled={refreshing}>
					{#if refreshing}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<RefreshCw />{/if}
					Refresh models
				</Button>
				<Button variant="outline" size="sm" onclick={test} disabled={testing || !endpoint.enabled}>
					{#if testing}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Zap />{/if}
					Test
				</Button>
			{/if}
			<Button variant="outline" size="sm" {href} class="ml-auto">Open</Button>
		</div>
	</Card.Content>
</Card.Root>
