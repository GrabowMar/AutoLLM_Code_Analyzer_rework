<script lang="ts">
	/**
	 * PresetPicker — choose one run preset (harness / sampling / budget).
	 *
	 * Props:
	 * - `kind` (`PresetKind`, required).
	 * - `selectedId` (bindable `number | null`): RunPreset pk (a specific version).
	 * - `pinned` (`{ id, slug, version }`): an exact version to keep selectable even
	 *   when it is older than the latest or archived (e.g. `?from_job=`). Shown as
	 *   "v1 · latest is v3" with a "Use latest" action.
	 * - `autoSelectDefault` (default true): when `selectedId` is null after loading,
	 *   select the kind's default preset (the composer never offers "none").
	 * - `filter(preset)`: restrict the list (e.g. harness presets of one harness).
	 * - `optionSchema`: harness option schema, for nicer option labels in summaries.
	 * - `label` (default "Sampling preset" etc.; '' hides it), `idPrefix`, `disabled`,
	 *   `showManage` (default true): "Manage" opens the Library presets tab in a new
	 *   tab; the list reloads when the window regains focus.
	 * - `onchange(preset | null)` after the user picks a preset.
	 * Lists are cached per page; call `invalidatePresets(kind)` (presets.svelte.ts)
	 * after creating a preset elsewhere and every picker of that kind reloads.
	 */
	import { untrack } from 'svelte';
	import {
		getRunPreset,
		type HarnessOptionField,
		type PresetKind,
		type PresetRef,
		type RunPreset,
	} from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import {
		PRESET_KIND_LABELS,
		invalidatePresets,
		loadPresets,
		presetManageHref,
		presetsVersion,
		summarizePreset,
	} from './presets.svelte';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		kind: PresetKind;
		selectedId?: number | null;
		pinned?: PresetRef | null;
		autoSelectDefault?: boolean;
		filter?: (preset: RunPreset) => boolean;
		optionSchema?: HarnessOptionField[];
		label?: string;
		idPrefix?: string;
		disabled?: boolean;
		showManage?: boolean;
		onchange?: (preset: RunPreset | null) => void;
	}

	let {
		kind,
		selectedId = $bindable(null),
		pinned = null,
		autoSelectDefault = true,
		filter,
		optionSchema = [],
		label,
		idPrefix,
		disabled = false,
		showManage = true,
		onchange,
	}: Props = $props();

	let all = $state<RunPreset[]>([]);
	let loading = $state(true);
	let error = $state('');
	let extra = $state<RunPreset | null>(null);
	let extraFailed = $state(false);
	let refreshOnFocus = false;

	const triggerId = $derived(`${idPrefix ?? `preset-${kind}`}-select`);
	const heading = $derived(label ?? PRESET_KIND_LABELS[kind]);
	const presets = $derived(filter ? all.filter(filter) : all);
	const systemPresets = $derived(presets.filter((p) => p.is_system));
	const ownPresets = $derived(presets.filter((p) => !p.is_system));

	// The version a job pinned, or a selected id the latest-only list lacks.
	const extraId = $derived.by(() => {
		if (pinned && !presets.some((p) => p.id === pinned.id)) return pinned.id;
		if (!loading && selectedId != null && !presets.some((p) => p.id === selectedId)) return selectedId;
		return null;
	});
	const selected = $derived(
		presets.find((p) => p.id === selectedId) ?? (extra && extra.id === selectedId ? extra : undefined)
	);
	const latestForSelected = $derived(
		selected && !presets.some((p) => p.id === selected.id)
			? presets.find((p) => p.slug === selected.slug)
			: undefined
	);

	$effect(() => {
		const k = kind;
		presetsVersion(k); // reload after invalidatePresets(kind)
		let cancelled = false;
		// Silent refresh keeps the previous list on screen.
		loading = untrack(() => all.length === 0);
		error = '';
		loadPresets(k)
			.then((list) => {
				if (cancelled) return;
				all = list;
				if (autoSelectDefault && selectedId == null && !pinned) {
					const pool = filter ? list.filter(filter) : list;
					const fallback = pool.find((p) => p.is_default) ?? pool.find((p) => p.is_system);
					if (fallback) selectedId = fallback.id;
				}
			})
			.catch((e: unknown) => {
				if (!cancelled) error = formatApiError(e, 'Could not load presets.');
			})
			.finally(() => {
				if (!cancelled) loading = false;
			});
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		const id = extraId;
		if (id == null) {
			extra = null;
			extraFailed = false;
			return;
		}
		if (untrack(() => extra?.id === id)) return;
		let cancelled = false;
		extraFailed = false;
		getRunPreset(id)
			.then((preset) => {
				if (!cancelled) extra = preset;
			})
			.catch(() => {
				if (!cancelled) {
					extra = null;
					extraFailed = true;
				}
			});
		return () => {
			cancelled = true;
		};
	});

	function pick(value: string) {
		const id = value ? Number(value) : null;
		selectedId = id;
		onchange?.(presets.find((p) => p.id === id) ?? (extra?.id === id ? extra : null));
	}

	function useLatest() {
		if (latestForSelected) pick(String(latestForSelected.id));
	}

	function onWindowFocus() {
		if (!refreshOnFocus) return;
		refreshOnFocus = false;
		invalidatePresets(kind);
	}

	const chip = 'rounded-full border px-1.5 text-[11px] font-medium leading-4';
</script>

<svelte:window onfocus={onWindowFocus} />

{#snippet presetLine(preset: RunPreset)}
	<span class="flex min-w-0 flex-col">
		<span class="flex min-w-0 flex-wrap items-center gap-1.5">
			<span class="truncate font-medium">{preset.name}</span>
			<span class="font-mono text-[11px] text-muted-foreground">v{preset.version}</span>
			{#if preset.is_default}
				<span class={cn(chip, 'border-transparent bg-primary/15 text-primary')}>default</span>
			{:else if preset.is_system}
				<span class={cn(chip, 'border-border bg-muted text-muted-foreground')}>system</span>
			{/if}
			{#if preset.is_archived}
				<span class={cn(chip, 'border-warning/30 bg-warning/10 text-warning-fg')}>archived</span>
			{/if}
		</span>
		<span class="truncate text-[11px] text-muted-foreground">{summarizePreset(preset, optionSchema)}</span>
	</span>
{/snippet}

<div class="min-w-0 space-y-1.5" data-slot="preset-picker">
	{#if heading || showManage}
		<div class="flex items-center justify-between gap-2">
			{#if heading}<Label for={triggerId}>{heading}</Label>{/if}
			{#if showManage}
				<a
					href={presetManageHref(kind)}
					target="_blank"
					rel="noopener noreferrer"
					class="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
					onclick={() => (refreshOnFocus = true)}
				>
					Manage<ExternalLink class="size-3" aria-hidden="true" /><span class="sr-only"> (opens in a new tab)</span>
				</a>
			{/if}
		</div>
	{/if}

	{#if error && all.length === 0}
		<ErrorAlert message={error} onretry={() => invalidatePresets(kind)} />
	{:else}
		<Select.Root
			type="single"
			value={selectedId != null ? String(selectedId) : ''}
			onValueChange={pick}
			disabled={disabled || loading}
			items={[...(extra ? [extra] : []), ...presets].map((p) => ({ value: String(p.id), label: `${p.name} v${p.version}` }))}
		>
			<Select.Trigger id={triggerId} class="min-h-9 py-1.5 data-[size=default]:h-auto" aria-busy={loading}>
				{#if loading}
					<span class="flex items-center gap-2 text-muted-foreground">
						<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />Loading presets…
					</span>
				{:else if selected}
					{@render presetLine(selected)}
				{:else if selectedId != null && extraFailed}
					<span class="text-muted-foreground">Preset #{selectedId} (not available)</span>
				{:else if presets.length === 0}
					<span class="text-muted-foreground">No presets</span>
				{:else}
					<span class="text-muted-foreground">Choose a preset…</span>
				{/if}
			</Select.Trigger>
			<Select.Content class="max-w-[min(32rem,calc(100vw-2rem))]">
				{#if extra}
					<Select.Group>
						<Select.GroupHeading>Pinned version</Select.GroupHeading>
						<Select.Item value={String(extra.id)} label="{extra.name} v{extra.version}">
							{@render presetLine(extra)}
						</Select.Item>
					</Select.Group>
					<Select.Separator />
				{/if}
				{#if systemPresets.length > 0}
					<Select.Group>
						<Select.GroupHeading>System</Select.GroupHeading>
						{#each systemPresets as preset (preset.id)}
							<Select.Item value={String(preset.id)} label="{preset.name} v{preset.version}">
								{@render presetLine(preset)}
							</Select.Item>
						{/each}
					</Select.Group>
				{/if}
				{#if ownPresets.length > 0}
					{#if systemPresets.length > 0}<Select.Separator />{/if}
					<Select.Group>
						<Select.GroupHeading>Your presets</Select.GroupHeading>
						{#each ownPresets as preset (preset.id)}
							<Select.Item value={String(preset.id)} label="{preset.name} v{preset.version}">
								{@render presetLine(preset)}
							</Select.Item>
						{/each}
					</Select.Group>
				{/if}
				{#if presets.length === 0 && !extra}
					<p class="px-2 py-3 text-xs text-muted-foreground">No presets yet — create one in the Library.</p>
				{/if}
			</Select.Content>
		</Select.Root>

		{#if selected && selected.description}
			<p class="text-[11px] text-muted-foreground">{selected.description}</p>
		{/if}
		{#if selected && !presets.some((p) => p.id === selected.id)}
			<div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-warning-fg" role="status">
				<span>
					v{selected.version}
					{#if latestForSelected}
						· latest is v{latestForSelected.version}
					{:else if selected.is_archived}
						· archived
					{/if}
				</span>
				{#if latestForSelected}
					<Button variant="link" size="sm" class="h-auto px-0 text-[11px]" onclick={useLatest} {disabled}>
						Use latest
					</Button>
				{/if}
			</div>
		{/if}
		{#if error && all.length > 0}
			<p class="text-[11px] text-danger-fg">{error}</p>
		{/if}
	{/if}
</div>
