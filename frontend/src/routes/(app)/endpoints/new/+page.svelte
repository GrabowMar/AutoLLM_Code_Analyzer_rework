<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { createEndpoint, endpointChannel, getEndpoint, runProbe } from '$lib/api/inference';
	import type { EndpointCreatePayload, EndpointDetail, ProbeResult, TokenSecret } from '$lib/api/types/inference';
	import { getMe } from '$lib/api/users';
	import { apiErrorStatus, apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import { subscribe } from '$lib/api/sse';
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import {
		EndpointForm,
		RelayInstallPanel,
		TokenRevealDialog,
		PROBE_TONE_TEXT,
		probeSummary,
		sessionHostnames,
		type EndpointFormPayload,
	} from '$lib/components/inference';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Zap from '@lucide/svelte/icons/zap';

	type Step = 'connection' | 'details' | 'connect';

	const POLL_MS = 5_000;

	let step = $state<Step>('connection');
	let isStaff = $state(false);
	let busy = $state(false);
	let errors = $state<Record<string, string>>({});
	let created = $state<EndpointDetail | null>(null);
	let token = $state<TokenSecret | null>(null);
	let tokenOpen = $state(false);
	let probe = $state<ProbeResult | 'running' | null>(null);

	const steps: { key: Step; label: string }[] = [
		{ key: 'connection', label: 'Server' },
		{ key: 'details', label: 'Settings' },
		{ key: 'connect', label: 'Connect' },
	];
	const stepIndex = $derived(steps.findIndex((s) => s.key === step));
	// A primitive, so the live-status effect below does not re-subscribe
	// every time a poll replaces `created`.
	const createdId = $derived(created?.id ?? null);
	const probeLine = $derived(probe && probe !== 'running' ? probeSummary(probe) : null);
	const connected = $derived(!!created && created.live.session_count > 0);
	const isRelay = $derived(created?.connection === 'relay');
	const hasModels = $derived(!!created && created.models.total > 0);
	const connectedText = $derived.by(() => {
		const hosts = created ? sessionHostnames(created) : [];
		return hosts.length > 0 ? `Relay connected from ${hosts.join(', ')}.` : 'Relay connected.';
	});

	onMount(() => {
		getMe()
			.then((me) => (isStaff = me.is_staff ?? false))
			.catch(() => (isStaff = false));
	});

	async function submit(payload: EndpointFormPayload) {
		busy = true;
		errors = {};
		try {
			const result = await createEndpoint(payload as EndpointCreatePayload);
			created = result.endpoint;
			step = 'connect';
			if (result.token) {
				token = result.token;
				tokenOpen = true;
			}
		} catch (e) {
			const fields = apiFieldErrors(e);
			if (Object.keys(fields).length === 0) {
				const message = formatApiError(e, 'Could not create the endpoint.');
				// The server answers some field problems with a plain detail.
				if (apiErrorStatus(e) === 409) fields.slug = message;
				else if (/base url|direct endpoint/i.test(message)) fields.base_url = message;
				else fields.__all__ = message;
			}
			errors = fields;
			// Connection-level errors belong to the first step.
			if (fields.base_url || fields.connection || fields.kind) step = 'connection';
		} finally {
			busy = false;
		}
	}

	async function refreshCreated() {
		if (!created) return;
		try {
			created = await getEndpoint(created.id);
		} catch {
			// keep the last state; the next poll retries
		}
	}

	// Waiting for the relay / discovery: SSE `session: connected` flips it at
	// once; a 5 s poll covers a missing stream and the inventory arriving.
	$effect(() => {
		const id = createdId;
		if (id == null || step !== 'connect') return;
		const stop = subscribe([endpointChannel(id)], (event) => {
			if (event.type === 'session' || event.type === 'endpoint') void refreshCreated();
		});
		const poll = setInterval(() => {
			if (!(connected && hasModels)) void refreshCreated();
		}, POLL_MS);
		return () => {
			stop();
			clearInterval(poll);
		};
	});

	// A probe still polling when the page goes away stops with it.
	const probeController = new AbortController();
	$effect(() => () => probeController.abort());

	async function test() {
		if (!created) return;
		probe = 'running';
		try {
			probe = await runProbe(created.id, null, { timeoutMs: 60_000, signal: probeController.signal });
			void refreshCreated();
		} catch (e) {
			if (isAbortError(e)) return;
			probe = { status: 'failed', latency_ms: null, error: formatApiError(e, 'Test failed.') };
		}
	}
</script>

<div class="space-y-6">
	<PageHeader
		title="New endpoint"
		description="Connect an Ollama or OpenAI-compatible server so experiments can run its models."
		back={{ href: '/endpoints', label: 'Endpoints' }}
	/>

	<ol class="flex flex-wrap items-center gap-2 text-xs" aria-label="Steps">
		{#each steps as s, i (s.key)}
			<li class="flex items-center gap-2" aria-current={i === stepIndex ? 'step' : undefined}>
				<span
					class={cn(
						'flex size-6 items-center justify-center rounded-full border text-[11px] font-semibold tabular-nums',
						i < stepIndex && 'border-primary bg-primary text-primary-foreground',
						i === stepIndex && 'border-primary text-success-fg',
						i > stepIndex && 'border-border text-muted-foreground'
					)}
				>
					{i + 1}
				</span>
				<span class={i === stepIndex ? 'font-medium text-foreground' : 'text-muted-foreground'}>{s.label}</span>
				{#if i < steps.length - 1}<span class="h-px w-6 bg-border" aria-hidden="true"></span>{/if}
			</li>
		{/each}
	</ol>

	{#if step !== 'connect'}
		<Card.Root>
			<Card.Content class="pt-6">
				<!-- One form instance across both steps keeps what was typed. -->
				<EndpointForm
					mode="create"
					{isStaff}
					step={step === 'connection' ? 'connection' : 'details'}
					{errors}
					{busy}
					onsubmit={submit}
					oncancel={() => goto('/endpoints')}
					onnext={() => (step = 'details')}
					onback={() => (step = 'connection')}
				/>
			</Card.Content>
		</Card.Root>
	{:else if created}
		<Card.Root>
			<Card.Header>
				<Card.Title class="text-base">
					{isRelay ? 'Run the relay on the model machine' : 'Check the connection'}
				</Card.Title>
				<Card.Description>
					{#if isRelay}
						Endpoint <span class="font-mono">{created.slug}</span> is created. Install the relay where the model server
						runs; it connects out to this site with the token you just stored.
					{:else}
						Endpoint <span class="font-mono">{created.slug}</span> is created. The executor scans
						<span class="font-mono">{created.base_url}</span> for models every two minutes.
					{/if}
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				{#if isRelay}
					<RelayInstallPanel endpointId={created.id} />
				{/if}

				<div
					class={cn(
						'flex flex-wrap items-center gap-3 rounded-md border px-3 py-2.5 text-sm',
						connected || (!isRelay && hasModels) ? toneClasses.success : toneClasses.neutral
					)}
					role="status"
					aria-live="polite"
				>
					{#if isRelay && !connected}
						<LoaderCircle class="size-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
						<span>Waiting for the relay to connect…</span>
					{:else}
						<CircleCheck class="size-4 shrink-0" aria-hidden="true" />
						<span>
							{#if isRelay}{connectedText}{/if}
							{#if hasModels}
								{created.models.total} models · {created.models.tools} with tools
							{:else}
								Discovering models…
							{/if}
						</span>
					{/if}
				</div>

				{#if !isRelay}
					<div class="flex flex-wrap items-center gap-3">
						<Button type="button" variant="outline" size="sm" onclick={test} disabled={probe === 'running'}>
							{#if probe === 'running'}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Zap />{/if}
							Test connection
						</Button>
						{#if probeLine}
							<span class={cn('text-xs', PROBE_TONE_TEXT[probeLine.tone])} role="status">{probeLine.text}</span>
						{/if}
					</div>
				{/if}

				<div class="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
					{#if isRelay && token && !tokenOpen}
						<span class="mr-auto self-center text-xs text-muted-foreground">
							Lost the token? Create a new one on the endpoint's Tokens tab.
						</span>
					{/if}
					<Button variant={connected || hasModels ? 'default' : 'outline'} size="sm" href="/endpoints/{created.id}">
						{connected || hasModels ? 'Open endpoint' : 'Finish later'}
					</Button>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>

<TokenRevealDialog
	bind:open={tokenOpen}
	{token}
	onclose={() => {
		// The secret is gone after the dialog; keep only the prefix.
		if (token) token = { ...token, secret_once: '' };
	}}
/>
