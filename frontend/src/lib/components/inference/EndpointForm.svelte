<script lang="ts" module>
	import type { EndpointCreatePayload, EndpointUpdatePayload } from '$lib/api/types/inference';

	export type EndpointFormPayload = EndpointCreatePayload | EndpointUpdatePayload;
</script>

<script lang="ts">
	/**
	 * EndpointForm — create or edit a local endpoint.
	 *
	 * Props:
	 * - `mode`: 'create' | 'edit'.
	 * - `initial`: the endpoint being edited (edit mode). The system OpenRouter
	 *   row only exposes its caps.
	 * - `isStaff`: Direct connections and shared visibility are staff-only.
	 * - `step`: create mode shows the wizard steps 'connection' (kind and
	 *   connection) or 'details' (everything else); edit mode shows all.
	 * - `errors`: server field errors (`apiFieldErrors`), keyed by field name.
	 * - `busy`, `submitLabel`.
	 * - `onsubmit(payload)`: create → the full `EndpointCreatePayload`;
	 *   edit → only the changed fields (`api_key: ""` clears the key).
	 * - `oncancel()`, `onback()` (create, details step).
	 */
	import { untrack } from 'svelte';
	import { cn } from '$lib/utils';
	import type { EndpointDetail, EndpointKind, EndpointConnection, ApiFlavor } from '$lib/api/types/inference';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import Switch from '$lib/components/ui/switch/switch.svelte';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import { endpointSlugFromName, slugError, SLUG_MAX_LENGTH } from './endpoint-display';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Lock from '@lucide/svelte/icons/lock';

	type LocalKind = Exclude<EndpointKind, 'openrouter'>;

	interface Props {
		mode: 'create' | 'edit';
		initial?: EndpointDetail | null;
		isStaff?: boolean;
		step?: 'connection' | 'details';
		errors?: Record<string, string>;
		busy?: boolean;
		submitLabel?: string;
		onsubmit: (payload: EndpointFormPayload) => void;
		oncancel?: () => void;
		onback?: () => void;
		onnext?: () => void;
	}

	let {
		mode,
		initial = null,
		isStaff = false,
		step = 'details',
		errors = {},
		busy = false,
		submitLabel,
		onsubmit,
		oncancel,
		onback,
		onnext,
	}: Props = $props();

	const start = untrack(() => initial);
	const systemRow = !!start?.is_system;

	let kind = $state<LocalKind>(start && start.kind !== 'openrouter' ? start.kind : 'ollama');
	let connection = $state<EndpointConnection>(start?.connection ?? 'relay');
	let name = $state(start?.name ?? '');
	let slug = $state(start?.slug ?? '');
	let slugTouched = $state(untrack(() => mode) === 'edit');
	let baseUrl = $state(start?.base_url ?? '');
	let apiKey = $state('');
	let clearKey = $state(false);
	let apiFlavor = $state<ApiFlavor>(start?.api_flavor ?? 'native');
	let maxConcurrency = $state<number | null>(start?.max_concurrency ?? 1);
	let maxActiveJobs = $state<number | null>(null);
	let maxResident = $state<number | null>(start ? start.max_resident_models : 1);
	let requestTimeout = $state<number | null>(start?.request_timeout_s ?? null);
	let numCtxCap = $state<number | null>(start?.num_ctx_cap ?? null);
	let keepAlive = $state(start?.keep_alive ?? '');
	let assumeTools = $state(start?.assume_tools ?? false);
	let visibility = $state<'private' | 'shared'>(start?.visibility ?? 'private');

	let invalid = $state<Record<string, boolean>>({});
	let attempted = $state(false);

	// The slug follows the name until the user edits it (create only).
	$effect(() => {
		if (mode === 'create' && !slugTouched) slug = endpointSlugFromName(name);
	});

	// Mirrors the server's keep_alive check.
	const KEEP_ALIVE_PATTERN = /^(-1|0|\d+(\.\d+)?[smh]?)$/;

	const isDirect = $derived(connection === 'direct');
	const isOllama = $derived(kind === 'ollama');

	const localErrors = $derived.by(() => {
		const out: Record<string, string> = {};
		if (!systemRow && !name.trim()) out.name = 'Required';
		if (mode === 'create') {
			if (!slug) out.slug = 'Required: lowercase letters, digits and dashes';
			else if (slugError(slug)) out.slug = slugError(slug);
		}
		if (isDirect && !systemRow) {
			const url = baseUrl.trim();
			if (!url) out.base_url = 'Required for a direct endpoint';
			else if (!/^https?:\/\/[^\s/]+/i.test(url)) out.base_url = 'An http:// or https:// URL';
		}
		if (maxConcurrency == null) out.max_concurrency = 'Required';
		if (isOllama && keepAlive.trim() && !KEEP_ALIVE_PATTERN.test(keepAlive.trim())) {
			out.keep_alive = 'A duration like 30m, 2h or 300s; 0 unloads at once, -1 keeps it loaded';
		}
		return out;
	});
	const fieldError = (key: string) => (attempted ? localErrors[key] : '') || errors[key] || '';
	const hasInvalid = $derived(Object.values(invalid).some(Boolean));
	const canSubmit = $derived(Object.keys(localErrors).length === 0 && !hasInvalid);

	function setInvalid(key: string, value: boolean) {
		if (!!invalid[key] !== value) invalid = { ...invalid, [key]: value };
	}

	function createPayload(): EndpointCreatePayload {
		const payload: EndpointCreatePayload = {
			name: name.trim(),
			slug,
			kind,
			connection,
			max_concurrency: maxConcurrency ?? 1,
		};
		if (isDirect) {
			payload.base_url = baseUrl.trim();
			if (apiKey.trim()) payload.api_key = apiKey.trim();
		}
		if (isOllama) payload.api_flavor = apiFlavor;
		if (maxActiveJobs != null) payload.max_active_jobs = maxActiveJobs;
		if (maxResident != null) payload.max_resident_models = maxResident;
		if (requestTimeout != null) payload.request_timeout_s = requestTimeout;
		if (numCtxCap != null) payload.num_ctx_cap = numCtxCap;
		if (keepAlive.trim()) payload.keep_alive = keepAlive.trim();
		if (!isOllama) payload.assume_tools = assumeTools;
		if (isStaff) payload.visibility = visibility;
		return payload;
	}

	function updatePayload(): EndpointUpdatePayload {
		const e = start!;
		const out: EndpointUpdatePayload = {};
		if (maxConcurrency != null && maxConcurrency !== e.max_concurrency) out.max_concurrency = maxConcurrency;
		if (maxActiveJobs != null) out.max_active_jobs = maxActiveJobs;
		if (systemRow) return out;
		if (name.trim() !== e.name) out.name = name.trim();
		if (isStaff && connection !== e.connection) out.connection = connection;
		if (isDirect && baseUrl.trim() !== (e.base_url ?? '')) out.base_url = baseUrl.trim();
		if (isDirect && apiKey.trim()) out.api_key = apiKey.trim();
		else if (clearKey) out.api_key = '';
		if (isOllama && apiFlavor !== e.api_flavor) out.api_flavor = apiFlavor;
		// A cleared field means "unlimited" (null), which the server accepts.
		if (maxResident !== e.max_resident_models) out.max_resident_models = maxResident;
		if (requestTimeout != null && requestTimeout !== e.request_timeout_s) out.request_timeout_s = requestTimeout;
		if (numCtxCap != null && numCtxCap !== e.num_ctx_cap) out.num_ctx_cap = numCtxCap;
		if (keepAlive.trim() && keepAlive.trim() !== e.keep_alive) out.keep_alive = keepAlive.trim();
		if (!isOllama && assumeTools !== e.assume_tools) out.assume_tools = assumeTools;
		if (isStaff && visibility !== e.visibility) out.visibility = visibility;
		return out;
	}

	function submit(event: SubmitEvent) {
		event.preventDefault();
		attempted = true;
		if (!canSubmit || busy) return;
		onsubmit(mode === 'create' ? createPayload() : updatePayload());
	}

	const kindItems = [
		{ value: 'ollama', label: 'Ollama', title: 'Ollama server: native API with model discovery, warm-up and unload' },
		{ value: 'openai_compatible', label: 'OpenAI-compatible', title: 'vLLM, llama.cpp server, LM Studio, …' },
	];
	const connectionItems = $derived([
		{ value: 'relay', label: 'Relay', badge: 'Recommended', title: 'A small script on the model machine dials out to this server' },
		{
			value: 'direct',
			label: 'Direct',
			disabled: !isStaff,
			title: isStaff
				? 'The executor connects to the server URL (must be routable from this host, e.g. Tailscale)'
				: 'Direct endpoints can only be created by staff',
		},
	]);
	const flavorItems = [
		{ value: 'native', label: 'Native API', description: 'Recommended: num_ctx per request, warm-up, unload' },
		{ value: 'openai', label: 'OpenAI /v1', description: 'Needs OLLAMA_CONTEXT_LENGTH set on the server' },
	];
	const visibilityItems = [
		{ value: 'private', label: 'Private', description: 'Only you (and staff) can run on it' },
		{ value: 'shared', label: 'Shared', description: 'Every user can run on it; they see counts, never your hosts' },
	];
	const showConnection = $derived(mode === 'edit' || step === 'connection');
	const showDetails = $derived(mode === 'edit' || step === 'details');
</script>

<form class="space-y-6" onsubmit={submit} novalidate>
	{#if showConnection && !systemRow}
		<section class="space-y-4" aria-labelledby="endpoint-form-connection">
			<SectionHeading id="endpoint-form-connection" size="sm" title="Server and connection" />
			{#if mode === 'create'}
				<div class="space-y-1.5">
					<Label id="endpoint-kind-label">Server kind</Label>
					<Segmented aria-labelledby="endpoint-kind-label" items={kindItems} bind:value={() => kind, (v) => (kind = v as LocalKind)} />
				</div>
			{/if}
			<div class="space-y-1.5">
				<Label id="endpoint-connection-label">Connection</Label>
				<Segmented
					aria-labelledby="endpoint-connection-label"
					items={connectionItems}
					bind:value={() => connection, (v) => (connection = v as EndpointConnection)}
					disabled={mode === 'edit' && !isStaff}
				/>
				<p class="text-xs text-muted-foreground">
					{#if isDirect}
						The executor calls the server directly. The URL must be reachable from this host (for example over
						Tailscale) and on the deployment's allowlist.
					{:else}
						For machines on other networks: a single-file Python relay on the model machine connects out over HTTPS.
						No inbound ports, no VPN.
					{/if}
				</p>
			</div>
			{#if isDirect}
				<div class="grid gap-4 md:grid-cols-2">
					<div class="space-y-1.5 md:col-span-2">
						<Label for="endpoint-base-url">Base URL</Label>
						<Input
							id="endpoint-base-url"
							bind:value={baseUrl}
							placeholder={isOllama ? 'http://gpu-box.tailnet:11434' : 'https://vllm.internal:8000'}
							class="font-mono text-sm"
							aria-invalid={fieldError('base_url') ? true : undefined}
							aria-describedby="endpoint-base-url-note"
						/>
						<p id="endpoint-base-url-note" class={cn('text-[11px]', fieldError('base_url') ? 'text-danger-fg' : 'text-muted-foreground')}>
							{fieldError('base_url') || 'Redirects are not followed; TLS is verified against this hostname.'}
						</p>
					</div>
					<div class="space-y-1.5">
						<Label for="endpoint-api-key">API key <span class="font-normal text-muted-foreground">(optional)</span></Label>
						<Input
							id="endpoint-api-key"
							type="password"
							autocomplete="off"
							bind:value={apiKey}
							placeholder={start?.has_api_key ? `${start.api_key_prefix ?? ''}… (stored; type to replace)` : 'Bearer token'}
							class="font-mono text-sm"
							disabled={clearKey}
						/>
						{#if mode === 'edit' && start?.has_api_key}
							<label class="flex items-center gap-2 text-xs text-muted-foreground">
								<Switch size="sm" bind:checked={clearKey} label="Remove the stored key" />
								Remove the stored key
							</label>
						{/if}
						{#if errors.api_key}<p class="text-[11px] text-danger-fg">{errors.api_key}</p>{/if}
					</div>
				</div>
			{/if}
			{#if isOllama}
				<div class="max-w-sm space-y-1.5">
					<Label for="endpoint-flavor">API flavor</Label>
					<Select.Simple id="endpoint-flavor" bind:value={() => apiFlavor, (v) => (apiFlavor = v as ApiFlavor)} items={flavorItems} />
				</div>
			{/if}
		</section>
	{/if}

	{#if showDetails}
		{#if !systemRow}
			<section class="space-y-4" aria-labelledby="endpoint-form-identity">
				<SectionHeading id="endpoint-form-identity" size="sm" title="Name" />
				<div class="grid gap-4 md:grid-cols-2">
					<div class="space-y-1.5">
						<Label for="endpoint-name">Name</Label>
						<Input
							id="endpoint-name"
							bind:value={name}
							maxlength={120}
							placeholder="GPU box 1"
							aria-invalid={fieldError('name') ? true : undefined}
						/>
						{#if fieldError('name')}<p class="text-[11px] text-danger-fg">{fieldError('name')}</p>{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="endpoint-slug">Slug</Label>
						{#if mode === 'create'}
							<Input
								id="endpoint-slug"
								value={slug}
								maxlength={SLUG_MAX_LENGTH}
								class="font-mono text-sm"
								placeholder="gpu-box-1"
								aria-invalid={fieldError('slug') ? true : undefined}
								aria-describedby="endpoint-slug-note"
								oninput={(e: Event) => {
									slugTouched = true;
									slug = (e.currentTarget as HTMLInputElement).value.trim().toLowerCase();
								}}
							/>
							<p id="endpoint-slug-note" class={cn('text-[11px]', fieldError('slug') ? 'text-danger-fg' : 'text-muted-foreground')}>
								{fieldError('slug') ||
									`Part of every model id (local-${slug || '<slug>'}/…) and of run fingerprints. It cannot be changed later.`}
							</p>
						{:else}
							<p id="endpoint-slug" class="flex h-9 items-center gap-1.5 font-mono text-sm text-muted-foreground">
								<Lock class="size-3.5" aria-hidden="true" />{slug}
							</p>
							<p class="text-[11px] text-muted-foreground">Immutable: model ids and fingerprints contain it.</p>
						{/if}
					</div>
				</div>
			</section>
		{/if}

		<section class="space-y-4" aria-labelledby="endpoint-form-capacity">
			<SectionHeading id="endpoint-form-capacity" size="sm" title="Capacity" />
			<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
				<NumberField
					id="endpoint-slots"
					label={systemRow ? 'Concurrent requests' : 'Slots (parallel requests)'}
					bind:value={maxConcurrency}
					min={1}
					max={256}
					required
					error={fieldError('max_concurrency')}
					hint={systemRow ? '' : isOllama ? 'Match OLLAMA_NUM_PARALLEL on the server.' : 'Requests the server handles at once.'}
					bind:invalid={() => !!invalid.max_concurrency, (v) => setInvalid('max_concurrency', v)}
				/>
				<NumberField
					id="endpoint-active-jobs"
					label="Max active jobs"
					bind:value={maxActiveJobs}
					min={1}
					max={256}
					placeholder={start
						? `${start.max_active_jobs_effective.sandbox} agent · ${start.max_active_jobs_effective.llm} LLM`
						: 'Same as slots'}
					error={errors.max_active_jobs ?? ''}
					hint="Runs admitted at once; blank keeps the default (the slot count)."
					bind:invalid={() => !!invalid.max_active_jobs, (v) => setInvalid('max_active_jobs', v)}
				/>
				{#if !systemRow}
					<NumberField
						id="endpoint-resident"
						label="Max resident models"
						bind:value={maxResident}
						min={1}
						max={64}
						placeholder="Unlimited"
						error={errors.max_resident_models ?? ''}
						hint={isOllama
							? 'Match OLLAMA_MAX_LOADED_MODELS. Experiments group runs to limit model swaps.'
							: 'Models the server keeps loaded; blank if it serves a fixed set.'}
						bind:invalid={() => !!invalid.max_resident_models, (v) => setInvalid('max_resident_models', v)}
					/>
				{/if}
			</div>
		</section>

		{#if !systemRow}
			<section class="space-y-4" aria-labelledby="endpoint-form-requests">
				<SectionHeading id="endpoint-form-requests" size="sm" title="Requests" />
				<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
					<NumberField
						id="endpoint-timeout"
						label="Request timeout (s)"
						bind:value={requestTimeout}
						min={30}
						max={7200}
						placeholder="Server default"
						error={errors.request_timeout_s ?? ''}
						hint="Per LLM call. Slow GPUs and long contexts need more."
						bind:invalid={() => !!invalid.request_timeout_s, (v) => setInvalid('request_timeout_s', v)}
					/>
					<NumberField
						id="endpoint-num-ctx"
						label="num_ctx cap"
						bind:value={numCtxCap}
						min={512}
						placeholder="Server default"
						error={errors.num_ctx_cap ?? ''}
						hint="Upper bound on the context window sent to every model (VRAM protection)."
						bind:invalid={() => !!invalid.num_ctx_cap, (v) => setInvalid('num_ctx_cap', v)}
					/>
					{#if isOllama}
						<div class="min-w-0 space-y-1.5">
							<Label class="text-xs" for="endpoint-keep-alive">Keep-alive</Label>
							<Input
								id="endpoint-keep-alive"
								bind:value={keepAlive}
								maxlength={16}
								placeholder="30m"
								class="h-8 font-mono text-sm"
								aria-invalid={fieldError('keep_alive') ? true : undefined}
								aria-describedby="endpoint-keep-alive-note"
							/>
							<p
								id="endpoint-keep-alive-note"
								class={cn('text-[11px]', fieldError('keep_alive') ? 'text-danger-fg' : 'text-muted-foreground')}
							>
								{fieldError('keep_alive') || 'How long Ollama keeps a model loaded after a request (e.g. 30m, 2h).'}
							</p>
						</div>
					{:else}
						<div class="min-w-0 space-y-1.5">
							<span class="text-xs font-medium">Tool calling</span>
							<label class="flex items-center gap-2 text-sm">
								<Switch bind:checked={assumeTools} label="Assume every model supports tool calling" />
								Assume tool support
							</label>
							<p class="text-[11px] text-muted-foreground">
								OpenAI-compatible servers do not report it; agent harnesses need it.
							</p>
						</div>
					{/if}
				</div>
			</section>

			{#if isStaff}
				<section class="space-y-4" aria-labelledby="endpoint-form-sharing">
					<SectionHeading id="endpoint-form-sharing" size="sm" title="Sharing" />
					<div class="max-w-sm space-y-1.5">
						<Label for="endpoint-visibility">Visibility</Label>
						<Select.Simple id="endpoint-visibility" bind:value={() => visibility, (v) => (visibility = v as 'private' | 'shared')} items={visibilityItems} />
						{#if errors.visibility}<p class="text-[11px] text-danger-fg">{errors.visibility}</p>{/if}
					</div>
				</section>
			{/if}
		{/if}
	{/if}

	{#if errors.__all__}
		<p class="text-sm text-danger-fg" role="alert">{errors.__all__}</p>
	{/if}

	<div class="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-4">
		{#if oncancel}
			<Button type="button" variant="ghost" size="sm" onclick={oncancel} disabled={busy}>Cancel</Button>
		{/if}
		{#if mode === 'create' && step === 'details' && onback}
			<Button type="button" variant="outline" size="sm" onclick={onback} disabled={busy}>Back</Button>
		{/if}
		{#if mode === 'create' && step === 'connection'}
			<Button
				type="button"
				size="sm"
				onclick={() => {
					attempted = true;
					if (localErrors.base_url) return;
					attempted = false;
					onnext?.();
				}}
			>
				Next
			</Button>
		{:else}
			<Button type="submit" size="sm" disabled={busy || (attempted && !canSubmit)}>
				{#if busy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
				{submitLabel ?? (mode === 'create' ? 'Create endpoint' : 'Save changes')}
			</Button>
		{/if}
	</div>
</form>
