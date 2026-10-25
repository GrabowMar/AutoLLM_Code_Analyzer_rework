<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto, replaceState } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import {
		createToken,
		deleteEndpoint,
		endpointChannel,
		getEndpoint,
		isEndpointInUseError,
		listEndpointModels,
		listSessions,
		listTokens,
		refreshEndpoint,
		revokeToken,
		rotateToken,
		unloadEndpointModel,
		updateEndpoint,
	} from '$lib/api/inference';
	import type {
		EndpointDetail,
		EndpointModel,
		EndpointUpdatePayload,
		SessionOut,
		TokenOut,
		TokenSecret,
	} from '$lib/api/types/inference';
	import { getMe } from '$lib/api/users';
	import { apiErrorStatus, apiFieldErrors, formatApiError } from '$lib/api/core';
	import { subscribe } from '$lib/api/sse';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import {
		CONNECTION_LABELS,
		DiscoveredModelsTable,
		EndpointForm,
		EndpointMetricsChart,
		RelayInstallPanel,
		SessionsTable,
		SlotMeter,
		TokenRevealDialog,
		applyEndpointEvent,
		endpointStatusTone,
		formatAgo,
		formatBytes,
		kindLabel,
		newSessionHosts,
		type EndpointFormPayload,
	} from '$lib/components/inference';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	type Tab = 'overview' | 'models' | 'tokens' | 'settings';
	const TABS: Tab[] = ['overview', 'models', 'tokens', 'settings'];
	const POLL_MS = 30_000;

	const endpointId = Number(page.params.id);

	let endpoint = $state<EndpointDetail | null>(null);
	let error = $state('');
	let notFound = $state(false);
	let isStaff = $state(false);
	let now = $state(Date.now());
	let tab = $state<Tab>(initialTab());
	let refreshKey = $state(0);

	let sessions = $state<SessionOut[]>([]);
	let models = $state<EndpointModel[]>([]);
	let selectedModels = $state(new Set<number>());

	let tokens = $state<TokenOut[] | null>(null);
	let tokensError = $state('');
	let revealed = $state<TokenSecret | null>(null);
	let revealOpen = $state(false);
	let revealNote = $state('');
	let newTokenOpen = $state(false);
	let newTokenLabel = $state('');
	let rotateTarget = $state<TokenOut | null>(null);
	let rotateGrace = $state<number | null>(24 * 60);
	let rotateInvalid = $state(false);
	let revokeTarget = $state<TokenOut | null>(null);
	let tokenBusy = $state(false);

	let settingsErrors = $state<Record<string, string>>({});
	let settingsBusy = $state(false);
	let formKey = $state(0);
	let deleteOpen = $state(false);
	let deleteBusy = $state(false);
	let toggleBusy = $state(false);
	let refreshing = $state(false);

	function initialTab(): Tab {
		const t = page.url.searchParams.get('tab');
		return TABS.includes(t as Tab) ? (t as Tab) : 'overview';
	}

	const canEdit = $derived(!!endpoint?.can_edit);
	const isRelay = $derived(endpoint?.connection === 'relay');
	const visibleTabs = $derived(
		!endpoint
			? []
			: endpoint.is_system
				? (['overview', ...(canEdit ? ['settings'] : [])] as Tab[])
				: canEdit
					? isRelay
						? TABS
						: (['overview', 'models', 'settings'] as Tab[])
					: (['overview', 'models'] as Tab[])
	);
	const activeTab = $derived(visibleTabs.includes(tab) ? tab : 'overview');
	const flaggedHosts = $derived(newSessionHosts(sessions));
	const modelByServed = $derived(new Map(models.map((m) => [m.served_model_name, m])));
	const liveTokens = $derived((tokens ?? []).filter((t) => !t.revoked_at));

	function setTab(value: string) {
		tab = value as Tab;
		const url = new URL(page.url);
		if (value === 'overview') url.searchParams.delete('tab');
		else url.searchParams.set('tab', value);
		replaceState(url, page.state);
	}

	async function loadEndpoint() {
		try {
			endpoint = await getEndpoint(endpointId);
			error = '';
		} catch (e) {
			if (apiErrorStatus(e) === 404) notFound = true;
			else if (endpoint === null) error = formatApiError(e, 'Could not load the endpoint.');
		}
	}

	async function loadSessions() {
		if (!canEdit || !isRelay) return;
		try {
			sessions = await listSessions(endpointId);
		} catch {
			// owner-only data; keep the last list
		}
	}

	async function loadModels() {
		try {
			models = await listEndpointModels(endpointId);
		} catch {
			// the Models tab shows its own error
		}
	}

	async function loadTokens() {
		try {
			tokens = await listTokens(endpointId);
			tokensError = '';
		} catch (e) {
			tokensError = formatApiError(e, 'Could not load tokens.');
		}
	}

	function refreshAll() {
		void loadEndpoint().then(() => {
			void loadSessions();
		});
		void loadModels();
		refreshKey += 1;
	}

	onMount(() => {
		void loadEndpoint().then(() => {
			void loadSessions();
			if (canEdit && isRelay) void loadTokens();
		});
		void loadModels();
		getMe()
			.then((me) => (isStaff = me.is_staff ?? false))
			.catch(() => (isStaff = false));
		const poll = setInterval(refreshAll, POLL_MS);
		const clock = setInterval(() => (now = Date.now()), 5_000);
		const stop = subscribe([endpointChannel(endpointId)], (event) => {
			if (event.type === 'session') {
				void loadEndpoint();
				void loadSessions();
				// Token session counts are owner/staff data of relay endpoints.
				if (canEdit && isRelay) void loadTokens();
			} else if (event.type === 'endpoint' && endpoint) {
				const before = endpoint.live.loaded.map((m) => m.name).join('|');
				endpoint = applyEndpointEvent(endpoint, event.data);
				if (endpoint.live.loaded.map((m) => m.name).join('|') !== before) void loadModels();
			}
		});
		return () => {
			clearInterval(poll);
			clearInterval(clock);
			stop();
		};
	});

	// ── actions ──────────────────────────────────────────────────────────

	async function refreshModels() {
		refreshing = true;
		try {
			await refreshEndpoint(endpointId);
			toast.success(isRelay ? 'Re-scan requested: the relay reports models on its next poll.' : 'Re-scan requested.');
			setTimeout(() => {
				refreshKey += 1;
				void loadEndpoint();
			}, 5_000);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not request a re-scan.'));
		} finally {
			refreshing = false;
		}
	}

	async function unload(name: string) {
		const model = modelByServed.get(name);
		if (!model) return;
		try {
			await unloadEndpointModel(endpointId, model.id);
			toast.success(`Unloading ${name}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not unload the model.'));
		}
	}

	function useInExperiment() {
		const ids = [...selectedModels];
		if (ids.length === 0) return;
		void goto(`/experiments/new?models=${ids.join(',')}`);
	}

	function reveal(secret: TokenSecret, note = '') {
		revealed = secret;
		revealNote = note;
		revealOpen = true;
	}

	async function newToken() {
		tokenBusy = true;
		try {
			const secret = await createToken(endpointId, newTokenLabel.trim());
			newTokenOpen = false;
			newTokenLabel = '';
			reveal(secret);
			void loadTokens();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not create a token.'));
		} finally {
			tokenBusy = false;
		}
	}

	async function rotate() {
		if (!rotateTarget || rotateGrace == null || rotateInvalid) return;
		tokenBusy = true;
		try {
			const result = await rotateToken(endpointId, rotateTarget.id, rotateGrace);
			const until = result.old_expires_at ? formatDateTime(result.old_expires_at) : 'now';
			rotateTarget = null;
			reveal(
				result.new,
				`The old token keeps working until ${until}. Replace the relay's token file, then send it SIGHUP (systemd: systemctl kill -s HUP llmlab-relay).`
			);
			void loadTokens();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not rotate the token.'));
		} finally {
			tokenBusy = false;
		}
	}

	async function revoke() {
		if (!revokeTarget) return;
		tokenBusy = true;
		try {
			const result = await revokeToken(endpointId, revokeTarget.id);
			toast.success(
				`Token revoked: ${result.sessions_ended} sessions ended, ${result.requests_requeued} requests requeued.`
			);
			revokeTarget = null;
			void loadTokens();
			void loadSessions();
			void loadEndpoint();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not revoke the token.'));
		} finally {
			tokenBusy = false;
		}
	}

	async function saveSettings(payload: EndpointFormPayload) {
		const patch = payload as EndpointUpdatePayload;
		if (Object.keys(patch).length === 0) {
			toast.info('Nothing changed.');
			return;
		}
		settingsBusy = true;
		settingsErrors = {};
		try {
			endpoint = await updateEndpoint(endpointId, patch);
			formKey += 1; // the form restarts from the saved values
			toast.success('Endpoint saved');
		} catch (e) {
			const fields = apiFieldErrors(e);
			settingsErrors =
				Object.keys(fields).length > 0 ? fields : { __all__: formatApiError(e, 'Could not save the endpoint.') };
		} finally {
			settingsBusy = false;
		}
	}

	async function setEnabled(enabled: boolean) {
		toggleBusy = true;
		try {
			endpoint = await updateEndpoint(endpointId, { enabled });
			toast.success(enabled ? 'Endpoint enabled' : 'Endpoint disabled: no new runs or requests go to it');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not update the endpoint.'));
		} finally {
			toggleBusy = false;
		}
	}

	async function remove() {
		deleteBusy = true;
		try {
			await deleteEndpoint(endpointId);
			toast.success('Endpoint deleted');
			void goto('/endpoints');
		} catch (e) {
			deleteOpen = false;
			if (isEndpointInUseError(e)) {
				toast.error('Runs still reference this endpoint, so it cannot be deleted. Disable it instead.');
			} else {
				toast.error(formatApiError(e, 'Could not delete the endpoint.'));
			}
		} finally {
			deleteBusy = false;
		}
	}
</script>

<div class="space-y-6">
	<PageHeader
		title={endpoint?.name ?? 'Endpoint'}
		description={endpoint
			? `${kindLabel(endpoint.kind)}${endpoint.is_system ? '' : ` · ${CONNECTION_LABELS[endpoint.connection]}`}${endpoint.server_version ? ` · server ${endpoint.server_version}` : ''}`
			: undefined}
		back={{ href: '/endpoints', label: 'Endpoints' }}
	>
		{#snippet meta()}
			{#if endpoint}
				<StatusBadge status={endpoint.status} tone={endpointStatusTone(endpoint.status)} />
				<code class="mono-tag">{endpoint.slug}</code>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if endpoint && canEdit && !endpoint.is_system}
				<Button variant="outline" size="sm" onclick={refreshModels} disabled={refreshing}>
					{#if refreshing}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<RefreshCw />{/if}
					Refresh models
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if notFound}
		<EmptyState title="Endpoint not found" description="It was deleted, or it is private to another user.">
			{#snippet action()}<Button href="/endpoints" size="sm" variant="outline">All endpoints</Button>{/snippet}
		</EmptyState>
	{:else if error}
		<ErrorAlert message={error} onretry={loadEndpoint} />
	{:else if !endpoint}
		<LoadingState label="loading endpoint" />
	{:else}
		{#if !endpoint.enabled}
			<div class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-fg" role="status">
				Disabled: no new runs or requests are sent to this endpoint. Relays stay connected.
			</div>
		{/if}

		<Tabs.Root value={activeTab} onValueChange={setTab}>
			<Tabs.List>
				{#each visibleTabs as t (t)}
					<Tabs.Trigger value={t} class="capitalize">{t}</Tabs.Trigger>
				{/each}
			</Tabs.List>

			<!-- ── Overview ─────────────────────────────────────────── -->
			<Tabs.Content value="overview" class="space-y-6">
				<div class="grid gap-4 lg:grid-cols-3">
					<Card.Root>
						<Card.Header class="pb-3"><Card.Title class="text-base">Now</Card.Title></Card.Header>
						<Card.Content class="space-y-3 text-sm">
							<SlotMeter label="Request slots" used={endpoint.live.inflight_requests} total={endpoint.max_concurrency} />
							<dl class="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
								<dt class="text-muted-foreground">Active jobs</dt>
								<dd class="text-right tabular-nums">
									{endpoint.live.active_jobs} / {endpoint.max_active_jobs_effective.sandbox} agent · {endpoint.max_active_jobs_effective.llm} LLM
								</dd>
								<dt class="text-muted-foreground">Queued requests</dt>
								<dd class="text-right tabular-nums">{endpoint.live.queued_requests}</dd>
								<dt class="text-muted-foreground">Tokens/s (5 min)</dt>
								<dd class="text-right tabular-nums">{endpoint.live.tokens_per_s_5m?.toFixed(1) ?? '—'}</dd>
								{#if !endpoint.is_system}
									<dt class="text-muted-foreground">Max resident models</dt>
									<dd class="text-right tabular-nums">{endpoint.max_resident_models ?? 'unlimited'}</dd>
									<dt class="text-muted-foreground">Models</dt>
									<dd class="text-right tabular-nums">
										{endpoint.models.available}/{endpoint.models.total} · {endpoint.models.tools} tools
									</dd>
									<dt class="text-muted-foreground">Last scan</dt>
									<dd class="text-right">{formatAgo(endpoint.last_inventory_at, now)}</dd>
								{/if}
							</dl>
						</Card.Content>
					</Card.Root>

					{#if !endpoint.is_system}
						<Card.Root class="lg:col-span-2">
							<Card.Header class="pb-3">
								<Card.Title class="text-base">Last hour</Card.Title>
							</Card.Header>
							<Card.Content>
								<EndpointMetricsChart {endpointId} {refreshKey} capacity={endpoint.max_concurrency} />
							</Card.Content>
						</Card.Root>
					{/if}
				</div>

				{#if !endpoint.is_system}
					<Card.Root>
						<Card.Header class="pb-3">
							<Card.Title class="text-base">Loaded models</Card.Title>
							<Card.Description>Resident in the server's memory right now.</Card.Description>
						</Card.Header>
						<Card.Content>
							{#if endpoint.live.loaded.length === 0}
								<p class="text-sm text-muted-foreground">No model is loaded.</p>
							{:else}
								<ul class="divide-y divide-border/60">
									{#each endpoint.live.loaded as m (m.name)}
										<li class="flex flex-wrap items-center gap-3 py-2 text-sm">
											<span class="min-w-0 flex-1 truncate font-mono text-xs">{m.name}</span>
											<span class="text-xs tabular-nums text-muted-foreground">VRAM {formatBytes(m.size_vram)}</span>
											{#if m.cpu_spill}
												<span
													class="rounded-full border border-warning/30 bg-warning/10 px-1.5 text-[11px] text-warning-fg"
													title="Part of the model runs on the CPU: much slower. Lower num_ctx or max resident models."
												>CPU spill</span>
											{/if}
											{#if canEdit && modelByServed.has(m.name)}
												<Button variant="outline" size="sm" class="h-7 px-2" onclick={() => unload(m.name)}>Unload</Button>
											{/if}
										</li>
									{/each}
								</ul>
							{/if}
						</Card.Content>
					</Card.Root>
				{/if}

				{#if canEdit && isRelay}
					<Card.Root>
						<Card.Header class="pb-3">
							<Card.Title class="text-base">Relay sessions</Card.Title>
							<Card.Description>
								One relay is active; others wait as standby and take over if it disappears.
							</Card.Description>
						</Card.Header>
						<Card.Content>
							<SessionsTable {sessions} {now} newHosts={flaggedHosts} />
						</Card.Content>
					</Card.Root>
				{/if}

				{#if canEdit && (endpoint.recent_errors?.length || endpoint.last_error)}
					<Card.Root>
						<Card.Header class="pb-3"><Card.Title class="text-base">Recent errors</Card.Title></Card.Header>
						<Card.Content>
							{#if endpoint.recent_errors?.length}
								<ul class="space-y-2 text-xs">
									{#each endpoint.recent_errors as err, i (i)}
										<li class="flex flex-wrap gap-x-3">
											<span class="tabular-nums text-muted-foreground" title={formatDateTime(err.at)}>{formatAgo(err.at, now)}</span>
											<span class="font-mono text-muted-foreground">{err.kind}</span>
											<span class="min-w-0 flex-1 break-words text-danger-fg">{err.message}</span>
										</li>
									{/each}
								</ul>
							{:else}
								<p class="break-words text-xs text-danger-fg">{endpoint.last_error}</p>
							{/if}
						</Card.Content>
					</Card.Root>
				{/if}
			</Tabs.Content>

			<!-- ── Models ───────────────────────────────────────────── -->
			{#if !endpoint.is_system}
				<Tabs.Content value="models" class="space-y-3">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<p class="text-sm text-muted-foreground">
							Hidden models stay out of the model pickers. Overrides apply to new runs only.
						</p>
						<Button size="sm" onclick={useInExperiment} disabled={selectedModels.size === 0}>
							<FlaskConical />Use {selectedModels.size || ''} in new experiment
						</Button>
					</div>
					<DiscoveredModelsTable
						{endpointId}
						{canEdit}
						{refreshKey}
						bind:selectedIds={selectedModels}
						onloaded={(list) => (models = list.filter((m) => !m.is_hidden))}
					/>
				</Tabs.Content>
			{/if}

			<!-- ── Tokens ───────────────────────────────────────────── -->
			{#if canEdit && isRelay}
				<Tabs.Content value="tokens" class="space-y-6">
					<Card.Root>
						<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2 space-y-0 pb-3">
							<div class="space-y-1">
								<Card.Title class="text-base">Relay tokens</Card.Title>
								<Card.Description>Each relay authenticates with a token scoped to this endpoint.</Card.Description>
							</div>
							<Button size="sm" onclick={() => (newTokenOpen = true)}><Plus />New token</Button>
						</Card.Header>
						<Card.Content>
							{#if tokensError}
								<ErrorAlert message={tokensError} onretry={loadTokens} />
							{:else if tokens === null}
								<LoadingState label="loading tokens" class="py-6" />
							{:else if tokens.length === 0}
								<EmptyState compact icon={KeyRound} title="No tokens" description="Create one for the relay." />
							{:else}
								<div class="relative overflow-x-auto md:rounded-md md:border md:border-border">
									<table class="table-card-mobile w-full text-sm md:[&_td]:px-2 md:[&_th]:px-2">
										<thead>
											<tr>
												<th scope="col">Token</th>
												<th scope="col">Created</th>
												<th scope="col">Last used</th>
												<th scope="col">State</th>
												<th scope="col" class="text-right">Sessions</th>
												<th scope="col" class="text-right"><span class="sr-only">Actions</span></th>
											</tr>
										</thead>
										<tbody>
											{#each tokens as t (t.id)}
												{@const expired = !!t.expires_at && Date.parse(t.expires_at) <= now}
												<tr class={cn((t.revoked_at || expired) && 'text-muted-foreground')}>
													<td data-label="Token">
														<code class="mono-tag">{t.prefix}…</code>
														{#if t.label}<span class="ml-1 text-xs">{t.label}</span>{/if}
													</td>
													<td data-label="Created" class="text-xs" title={formatDateTime(t.created_at)}>{formatAgo(t.created_at, now)}</td>
													<td data-label="Last used" class="text-xs" title={formatDateTime(t.last_used_at)}>{formatAgo(t.last_used_at, now)}</td>
													<td data-label="State" class="text-xs">
														{#if t.revoked_at}
															<StatusBadge status="revoked" tone="neutral" />
														{:else if expired}
															<StatusBadge status="expired" tone="neutral" />
														{:else if t.expires_at}
															<span title={formatDateTime(t.expires_at)}><StatusBadge status="expiring" tone="warning" label="expires {formatDateTime(t.expires_at)}" /></span>
														{:else}
															<StatusBadge status="active" />
														{/if}
													</td>
													<td data-label="Sessions" class="text-right tabular-nums">{t.active_sessions}</td>
													<td data-label="" class="text-right">
														{#if !t.revoked_at && !expired}
															<span class="inline-flex gap-1">
																<Button variant="outline" size="sm" class="h-7 px-2" onclick={() => { rotateGrace = 24 * 60; rotateTarget = t; }}>Rotate</Button>
																<Button variant="outline" size="sm" class="h-7 px-2 text-danger-fg" onclick={() => (revokeTarget = t)}>Revoke</Button>
															</span>
														{/if}
													</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
								{#if liveTokens.length === 0}
									<p class="mt-2 text-xs text-warning-fg">No usable token: relays cannot connect until you create one.</p>
								{/if}
							{/if}
						</Card.Content>
					</Card.Root>

					<Card.Root>
						<Card.Header class="pb-3">
							<Card.Title class="text-base">Install the relay</Card.Title>
							<Card.Description>Run it on the machine with the model server.</Card.Description>
						</Card.Header>
						<Card.Content>
							<RelayInstallPanel {endpointId} />
						</Card.Content>
					</Card.Root>
				</Tabs.Content>
			{/if}

			<!-- ── Settings ─────────────────────────────────────────── -->
			{#if canEdit}
				<Tabs.Content value="settings" class="space-y-6">
					<Card.Root>
						<Card.Content class="pt-6">
							{#key formKey}
								<EndpointForm
									mode="edit"
									initial={endpoint}
									{isStaff}
									errors={settingsErrors}
									busy={settingsBusy}
									onsubmit={saveSettings}
								/>
							{/key}
						</Card.Content>
					</Card.Root>

					{#if !endpoint.is_system}
						<Card.Root class="border-destructive/30">
							<Card.Header class="pb-3">
								<Card.Title class="text-base">Disable or delete</Card.Title>
								<Card.Description>
									Disabling stops new runs and requests but keeps the history and lets relays stay connected. An
									endpoint with runs cannot be deleted.
								</Card.Description>
							</Card.Header>
							<Card.Content class="flex flex-wrap gap-2">
								<Button variant="outline" size="sm" disabled={toggleBusy} onclick={() => setEnabled(!endpoint!.enabled)}>
									{#if toggleBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
									{endpoint.enabled ? 'Disable' : 'Enable'}
								</Button>
								<Button variant="destructive" size="sm" onclick={() => (deleteOpen = true)}>Delete</Button>
							</Card.Content>
						</Card.Root>
					{/if}
				</Tabs.Content>
			{/if}
		</Tabs.Root>
	{/if}
</div>

<TokenRevealDialog
	bind:open={revealOpen}
	token={revealed}
	note={revealNote}
	onclose={() => {
		revealed = null;
		revealNote = '';
	}}
/>

<Dialog.Root bind:open={newTokenOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>New relay token</Dialog.Title>
			<Dialog.Description>For a second relay machine, or to replace a lost token.</Dialog.Description>
		</Dialog.Header>
		<form
			class="mt-4 space-y-4"
			onsubmit={(e) => {
				e.preventDefault();
				void newToken();
			}}
		>
			<div class="space-y-1.5">
				<Label for="token-label">Label <span class="font-normal text-muted-foreground">(optional)</span></Label>
				<Input id="token-label" bind:value={newTokenLabel} maxlength={80} placeholder="gpu-box-1 docker" />
			</div>
			<Dialog.Footer>
				<Button type="button" variant="outline" size="sm" onclick={() => (newTokenOpen = false)} disabled={tokenBusy}>Cancel</Button>
				<Button type="submit" size="sm" disabled={tokenBusy}>
					{#if tokenBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Create token
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

<Dialog.Root
	open={rotateTarget !== null}
	onOpenChange={(open) => {
		if (!open) rotateTarget = null;
	}}
>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Rotate token {rotateTarget?.prefix}…</Dialog.Title>
			<Dialog.Description>
				A new token is issued now. The old one keeps working for the grace period so you can swap the relay's token
				file without downtime.
			</Dialog.Description>
		</Dialog.Header>
		<div class="mt-4">
			<NumberField
				id="rotate-grace"
				label="Grace period (hours)"
				bind:value={rotateGrace}
				bind:invalid={rotateInvalid}
				scale={60}
				min={0}
				max={10_080}
				required
				hint="0 ends the old token immediately; at most 7 days."
			/>
		</div>
		<Dialog.Footer class="mt-4">
			<Button type="button" variant="outline" size="sm" onclick={() => (rotateTarget = null)} disabled={tokenBusy}>Cancel</Button>
			<Button type="button" size="sm" onclick={rotate} disabled={tokenBusy || rotateGrace == null || rotateInvalid}>
				{#if tokenBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Rotate
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={
		() => revokeTarget !== null,
		(v) => {
			if (!v) revokeTarget = null;
		}
	}
	title="Revoke token {revokeTarget?.prefix ?? ''}…?"
	description="Revoking ends its sessions now and requeues their in-flight requests. Relays using it stop until they get a new token."
	confirmLabel="Revoke"
	destructive
	busy={tokenBusy}
	onConfirm={revoke}
/>

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete {endpoint?.name ?? 'endpoint'}?"
	description="This removes the endpoint, its tokens and its sessions. Endpoints with runs cannot be deleted: disable them instead."
	confirmLabel="Delete"
	destructive
	busy={deleteBusy}
	onConfirm={remove}
/>
