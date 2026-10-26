<script lang="ts">
	/**
	 * DiscoveredModelsTable — models an endpoint reported, with per-model settings.
	 *
	 * Columns: select, name (served name), family, size, quantization, context /
	 * effective num_ctx (inline override), tools, digest (8 characters, copy),
	 * loaded, available, hidden, Test.
	 *
	 * Props:
	 * - `endpointId`.
	 * - `canEdit`: owner/staff — overrides, tool toggles, hide, unload and Test.
	 * - `selectedIds` (bindable `Set<number>`): rows picked for "Use in new experiment".
	 * - `refreshKey`: change it to refetch silently (e.g. on an SSE event).
	 * - `onloaded(models)`: after every fetch (for counts in the parent).
	 * Settings save as they change (num_ctx debounced); failures roll the row back.
	 */
	import { toast } from 'svelte-sonner';
	import { listEndpointModels, runProbe, unloadEndpointModel, updateEndpointModel } from '$lib/api/inference';
	import type { EndpointModel, EndpointModelUpdatePayload, ProbeResult } from '$lib/api/types/inference';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { TableToolbar, tableFrame } from '$lib/components/table';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import Switch from '$lib/components/ui/switch/switch.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import { PROBE_TONE_TEXT, formatCtx, probeSummary, shortDigest } from './endpoint-display';
	import Copy from '@lucide/svelte/icons/copy';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import PackageSearch from '@lucide/svelte/icons/package-search';
	import Search from '@lucide/svelte/icons/search';

	interface Props {
		endpointId: number;
		canEdit?: boolean;
		selectedIds?: Set<number>;
		refreshKey?: unknown;
		onloaded?: (models: EndpointModel[]) => void;
	}

	let {
		endpointId,
		canEdit = false,
		selectedIds = $bindable(new Set<number>()),
		refreshKey,
		onloaded,
	}: Props = $props();

	const CTX_SAVE_DELAY_MS = 800;

	let models = $state<EndpointModel[] | null>(null);
	let error = $state('');
	let showHidden = $state(false);
	let search = $state('');
	let saving = $state(new Set<number>());
	let probing = $state(new Map<number, 'running' | ProbeResult>());
	const ctxTimers = new Map<number, ReturnType<typeof setTimeout>>();

	async function load(signal?: AbortSignal) {
		try {
			const list = await listEndpointModels(endpointId, { includeHidden: showHidden, signal });
			models = list;
			error = '';
			onloaded?.(list);
		} catch (e) {
			if (isAbortError(e)) return;
			if (models === null) error = formatApiError(e, 'Could not load the models.');
			else toast.error(formatApiError(e, 'Could not refresh the models.'));
		}
	}

	$effect(() => {
		void refreshKey;
		void showHidden;
		void endpointId;
		const controller = new AbortController();
		void load(controller.signal);
		return () => controller.abort();
	});

	// Probes still polling when the table goes away stop with it.
	const probeController = new AbortController();

	$effect(() => () => {
		for (const timer of ctxTimers.values()) clearTimeout(timer);
		probeController.abort();
	});

	const query = $derived(search.trim().toLowerCase());
	const visible = $derived(
		(models ?? []).filter(
			(m) =>
				!query ||
				m.model_name.toLowerCase().includes(query) ||
				m.served_model_name.toLowerCase().includes(query) ||
				m.family.toLowerCase().includes(query)
		)
	);
	// Columns empty on every shown row are dropped (and named in the toolbar).
	const showFamily = $derived(visible.some((m) => !!m.family));
	const showSize = $derived(visible.some((m) => !!m.parameter_size || !!m.quantization));
	const showDigest = $derived(visible.some((m) => !!m.digest));
	const emptyCols = $derived(
		[!showFamily && 'family', !showSize && 'size', !showDigest && 'digest'].filter(Boolean).join(', ')
	);
	const selectable = $derived(visible.filter((m) => m.is_available && !m.is_hidden));
	const allSelected = $derived(selectable.length > 0 && selectable.every((m) => selectedIds.has(m.id)));
	const someSelected = $derived(!allSelected && selectable.some((m) => selectedIds.has(m.id)));

	function replaceRow(row: EndpointModel) {
		models = (models ?? []).map((m) => (m.id === row.id ? row : m));
	}

	function setSaving(id: number, on: boolean) {
		const next = new Set(saving);
		if (on) next.add(id);
		else next.delete(id);
		saving = next;
	}

	async function patch(row: EndpointModel, payload: EndpointModelUpdatePayload) {
		// Start from the row as it is now: a debounced save may fire after
		// another setting of the same row changed.
		const previous = models?.find((m) => m.id === row.id) ?? row;
		replaceRow({ ...previous, ...payload } as EndpointModel);
		setSaving(row.id, true);
		try {
			replaceRow(await updateEndpointModel(endpointId, row.id, payload));
		} catch (e) {
			replaceRow(previous);
			toast.error(formatApiError(e, `Could not update ${row.model_name}.`));
		} finally {
			setSaving(row.id, false);
		}
	}

	function setHidden(row: EndpointModel, hidden: boolean) {
		if (hidden && selectedIds.has(row.id)) {
			const next = new Set(selectedIds);
			next.delete(row.id);
			selectedIds = next;
		}
		void patch(row, { is_hidden: hidden });
	}

	function scheduleCtx(row: EndpointModel, value: number | null) {
		clearTimeout(ctxTimers.get(row.id));
		ctxTimers.set(
			row.id,
			setTimeout(() => {
				ctxTimers.delete(row.id);
				void patch(row, { num_ctx_override: value });
			}, CTX_SAVE_DELAY_MS)
		);
	}

	async function test(row: EndpointModel) {
		probing = new Map(probing).set(row.id, 'running');
		try {
			const result = await runProbe(endpointId, row.id, { signal: probeController.signal });
			probing = new Map(probing).set(row.id, result);
		} catch (e) {
			if (isAbortError(e)) return;
			probing = new Map(probing).set(row.id, {
				status: 'failed',
				latency_ms: null,
				error: formatApiError(e, 'Test failed.'),
			});
		}
	}

	async function unload(row: EndpointModel) {
		try {
			await unloadEndpointModel(endpointId, row.id);
			toast.success(`Unloading ${row.served_model_name}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not unload the model.'));
		}
	}

	function toggleRow(id: number, on: boolean) {
		const next = new Set(selectedIds);
		if (on) next.add(id);
		else next.delete(id);
		selectedIds = next;
	}

	function toggleAll(on: boolean) {
		const next = new Set(selectedIds);
		for (const m of selectable) {
			if (on) next.add(m.id);
			else next.delete(m.id);
		}
		selectedIds = next;
	}

	async function copyDigest(digest: string) {
		try {
			await navigator.clipboard.writeText(digest);
			toast.success('Digest copied');
		} catch {
			toast.error('Could not copy the digest.');
		}
	}

	function probeText(result: ProbeResult): string {
		if (result.status === 'done') {
			const ms = result.latency_ms != null ? `${result.latency_ms.toLocaleString()} ms` : 'ok';
			return result.sample ? `${ms} · “${result.sample.slice(0, 40)}”` : ms;
		}
		return probeSummary(result).text;
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Content class="p-0">
		<TableToolbar
			selectedCount={selectedIds.size}
			noun="model"
			onClearSelection={() => (selectedIds = new Set())}
		>
			{#snippet summary()}
				{#if models && models.length > 0}
					{visible.length} of {models.length} models · {models.filter((m) => m.supports_function_calling).length} with tools ·
					{models.filter((m) => m.loaded).length} loaded{#if emptyCols && visible.length > 0}<span class="ml-1 opacity-70">· no {emptyCols} reported</span>{/if}
				{:else}
					Models this endpoint reported
				{/if}
			{/snippet}
			{#snippet controls()}
				<div class="relative w-44 sm:w-56">
					<Search class="pointer-events-none absolute left-2.5 top-2 size-3.5 text-muted-foreground" aria-hidden="true" />
					<Input bind:value={search} type="search" placeholder="Search models…" aria-label="Search models" class="h-7 pl-8 text-xs" />
				</div>
				<label class="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
					<Checkbox bind:checked={showHidden} />
					Show hidden
				</label>
			{/snippet}
		</TableToolbar>

		{#if error}
			<div class="p-4"><ErrorAlert message={error} onretry={() => load()} /></div>
		{:else if models === null}
			<LoadingState label="loading models" class="py-8" />
		{:else if models.length === 0}
			<div class="p-4">
				<EmptyState
					compact
					icon={PackageSearch}
					title="No models discovered yet"
					description="Models appear once the relay (or the executor, for direct endpoints) reports the server's inventory. Pull a model with `ollama pull`, then Refresh models."
				/>
			</div>
		{:else if visible.length === 0}
			<div class="p-4"><EmptyState compact title="No models match" description="Clear the search." /></div>
		{:else}
			<div class="table-frame max-md:p-3" {@attach tableFrame}>
				<table class="table-card-mobile">
					<thead>
						<tr>
							<th scope="col" class="w-8">
								<Checkbox
									checked={allSelected}
									indeterminate={someSelected}
									aria-label="Select all shown models"
									onCheckedChange={(v) => toggleAll(v === true)}
								/>
							</th>
							<th scope="col">Model</th>
							{#if showFamily}<th scope="col" class="hidden xl:table-cell">Family</th>{/if}
							{#if showSize}<th scope="col" class="num">Size</th>{/if}
							<th scope="col">Context</th>
							<th scope="col">Tools</th>
							{#if showDigest}<th scope="col" class="hidden lg:table-cell">Digest</th>{/if}
							<th scope="col">State</th>
							{#if canEdit}<th scope="col">Hidden</th>{/if}
							{#if canEdit}<th scope="col" class="text-right"><span class="sr-only">Actions</span></th>{/if}
						</tr>
					</thead>
					<tbody>
						{#each visible as m (m.id)}
							{@const probe = probing.get(m.id)}
							{@const rowSelectable = m.is_available && !m.is_hidden}
							<tr class={cn((!m.is_available || m.is_hidden) && 'text-muted-foreground')} data-selected={selectedIds.has(m.id)}>
								<td data-label="">
									<Checkbox
										checked={selectedIds.has(m.id)}
										disabled={!rowSelectable}
										aria-label="Select {m.served_model_name}"
										onCheckedChange={(v) => toggleRow(m.id, v === true)}
									/>
								</td>
								<td data-label="Model" class="md:max-w-64">
									<span class="block min-w-0 break-all font-mono text-xs font-medium text-foreground md:truncate" title={m.served_model_name}>{m.served_model_name}</span>
									{#if m.model_name && m.model_name !== m.served_model_name}
										<span class="cell-sub block truncate text-[11px] text-muted-foreground">{m.model_name}</span>
									{/if}
									{#if probe}
										<span
											class={cn(
												'mt-0.5 flex items-center gap-1 text-[11px]',
												probe === 'running' ? 'text-muted-foreground' : PROBE_TONE_TEXT[probeSummary(probe).tone]
											)}
											role="status"
										>
											{#if probe === 'running'}
												<LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />Testing…
											{:else}
												Test: {probeText(probe)}
											{/if}
										</span>
									{/if}
								</td>
								{#if showFamily}<td data-label="Family" class="hidden xl:table-cell">
									{#if m.family}{m.family}{:else}<span class="text-muted-foreground">—</span>{/if}
								</td>{/if}
								{#if showSize}<td data-label="Size" class="num text-xs">
									{#if m.parameter_size}{m.parameter_size}{:else}<span class="text-muted-foreground">—</span>{/if}
									{#if m.quantization}<span class="cell-sub block text-[11px] text-muted-foreground">{m.quantization}</span>{/if}
								</td>{/if}
								<td data-label="Context" class="text-xs tabular-nums">
									{#if canEdit}
										<NumberField
											id="ctx-{endpointId}-{m.id}"
											label="num_ctx override"
											class="w-28 [&_label]:sr-only"
											value={m.num_ctx_override}
											min={512}
											max={m.context_window > 0 ? m.context_window : 1_048_576}
											placeholder={formatCtx(m.num_ctx_effective)}
											onchange={(v) => scheduleCtx(m, v)}
										/>
									{/if}
									<span class={cn('block font-mono', canEdit && 'cell-sub text-[11px] text-muted-foreground')} title="Effective num_ctx / model maximum">
										{formatCtx(m.num_ctx_effective)} of {formatCtx(m.context_window)}
									</span>
								</td>
								<td data-label="Tools">
									{#if canEdit}
										<Switch
											size="sm"
											checked={m.supports_function_calling}
											label="Tool calling for {m.served_model_name}"
											onCheckedChange={(v) => patch(m, { supports_function_calling: v })}
										/>
									{:else}
										<span class="text-xs">{m.supports_function_calling ? 'yes' : 'no'}</span>
									{/if}
								</td>
								{#if showDigest}<td data-label="Digest" class="hidden lg:table-cell">
									{#if m.digest}
										<button
											type="button"
											class="inline-flex cursor-pointer items-center gap-1 rounded font-mono text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
											title="Copy full digest: {m.digest}"
											aria-label="Copy digest {shortDigest(m.digest)}"
											onclick={() => copyDigest(m.digest)}
										>
											{shortDigest(m.digest)}<Copy class="size-3" aria-hidden="true" />
										</button>
									{:else}<span class="text-muted-foreground">—</span>{/if}
								</td>{/if}
								<td data-label="State" class="whitespace-nowrap text-xs">
									<span class="flex flex-wrap items-center gap-1">
										{#if !m.is_available}
											<span class="rounded-full border border-warning/30 bg-warning/10 px-1.5 text-[11px] text-warning-fg" title="The server no longer lists this model">unavailable</span>
										{:else if m.loaded}
											<span class="inline-flex items-center gap-1 text-success-fg" title="Resident in memory">
												<span class="status-dot h-1.5 w-1.5" aria-hidden="true"></span>loaded
											</span>
										{:else}
											<span class="text-muted-foreground">available</span>
										{/if}
									</span>
								</td>
								{#if canEdit}
									<td data-label="Hidden">
										<Switch
											size="sm"
											checked={m.is_hidden}
											label="Hide {m.served_model_name} from model pickers"
											onCheckedChange={(v) => setHidden(m, v)}
										/>
									</td>
									<td data-label="" class="w-px whitespace-nowrap text-right">
										<span class="inline-flex items-center gap-0.5">
											{#if saving.has(m.id)}
												<LoaderCircle class="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="Saving" />
											{/if}
											{#if m.loaded}
												<span class="row-actions-more">
													<Button variant="ghost" size="sm" class="h-7 px-2 text-xs text-muted-foreground hover:text-foreground" title="Unload {m.served_model_name} from memory" onclick={() => unload(m)}>Unload</Button>
												</span>
											{/if}
											<Button
												variant="ghost"
												size="sm"
												class="h-7 px-2 text-xs"
												title="Send a short test prompt to {m.served_model_name}"
												disabled={!m.is_available || probe === 'running'}
												onclick={() => test(m)}
											>
												Test
											</Button>
										</span>
									</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
