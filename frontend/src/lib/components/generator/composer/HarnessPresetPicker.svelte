<script lang="ts">
	/**
	 * HarnessPresetPicker — the composer's comparison axis: harness preset cards
	 * grouped by harness, multi-select, at least one. Each selected preset adds
	 * one run per (task, model, trial) cell.
	 *
	 * Props:
	 * - `harnesses`: harness catalog from `GET /agent/config/`.
	 * - `presets`: tracker of harness presets (`trackPresets('harness', …)`).
	 * - `selectedIds` (bindable `number[]`): replaced, never mutated.
	 * - `disabled`, `onchange()`.
	 * Pinned older versions (from a job or a draft) show "v1 · latest is v2 · Use latest".
	 */
	import type { AgentHarnessInfo, HarnessPresetConfig, RunPreset } from '$lib/api/generation';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { harnessOptionChips, presetManageHref } from '../presets.svelte';
	import NewHarnessPresetDialog from './NewHarnessPresetDialog.svelte';
	import type { PresetTracker } from './preset-lookup.svelte';
	import Check from '@lucide/svelte/icons/check';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Plus from '@lucide/svelte/icons/plus';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Wrench from '@lucide/svelte/icons/wrench';

	interface Props {
		harnesses: AgentHarnessInfo[];
		presets: PresetTracker<'harness'>;
		selectedIds?: number[];
		disabled?: boolean;
		onchange?: () => void;
	}

	let { harnesses, presets, selectedIds = $bindable([]), disabled = false, onchange }: Props = $props();

	let dialogOpen = $state(false);
	let dialogHarness = $state('');

	const selectedSet = $derived(new Set(selectedIds));
	const harnessOf = (p: RunPreset<'harness'>) => (p.config as HarnessPresetConfig).harness;

	// Selected presets the latest-only list lacks (older or archived versions).
	const pinnedExtras = $derived(
		selectedIds
			.filter((id) => !presets.list.some((p) => p.id === id))
			.map((id) => presets.find(id))
			.filter((p): p is RunPreset<'harness'> => !!p)
	);
	const pending = $derived(
		selectedIds.filter((id) => !presets.find(id) && !presets.isMissing(id)).length
	);

	const groups = $derived.by(() => {
		const all = [...pinnedExtras, ...presets.list];
		const known = harnesses.map((h) => ({
			info: h as AgentHarnessInfo | undefined,
			name: h.name,
			presets: all.filter((p) => harnessOf(p) === h.name),
		}));
		const unknownNames = [...new Set(all.map(harnessOf))].filter((n) => !harnesses.some((h) => h.name === n));
		return [
			...known,
			...unknownNames.map((name) => ({
				info: undefined as AgentHarnessInfo | undefined,
				name,
				presets: all.filter((p) => harnessOf(p) === name),
			})),
		].filter((g) => g.presets.length > 0 || (g.info?.option_schema.length ?? 0) > 0);
	});

	// Two selected presets with the same effective configuration duplicate runs.
	const duplicates = $derived.by(() => {
		const seen = new Map<string, RunPreset<'harness'>>();
		const dupes = new Map<number, RunPreset<'harness'>>();
		for (const id of selectedIds) {
			const p = presets.find(id);
			if (!p) continue;
			const first = seen.get(p.content_hash);
			if (first) dupes.set(p.id, first);
			else seen.set(p.content_hash, p);
		}
		return dupes;
	});

	function toggle(preset: RunPreset<'harness'>) {
		if (disabled) return;
		if (selectedSet.has(preset.id)) {
			if (selectedIds.length <= 1) return; // at least one preset
			selectedIds = selectedIds.filter((id) => id !== preset.id);
		} else {
			selectedIds = [...selectedIds, preset.id];
		}
		onchange?.();
	}

	function useLatest(preset: RunPreset<'harness'>, latest: RunPreset<'harness'>) {
		selectedIds = [...new Set(selectedIds.map((id) => (id === preset.id ? latest.id : id)))];
		onchange?.();
	}

	function openDialog(harness: string) {
		dialogHarness = harness;
		dialogOpen = true;
	}

	function onSaved(preset: RunPreset<'harness'>) {
		if (!selectedSet.has(preset.id)) selectedIds = [...selectedIds, preset.id];
		onchange?.();
	}

	const chip = 'inline-flex items-center gap-1 rounded-full border px-1.5 text-[11px] font-medium leading-4';
	const tunableCount = $derived(harnesses.filter((h) => h.option_schema.length > 0).length);
</script>

<div class="space-y-4" data-slot="harness-preset-picker">
	<div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
		<span aria-live="polite">
			<span class="font-medium text-foreground tabular-nums">{selectedIds.length}</span>
			selected · each adds one run per task, model and trial{selectedIds.length === 1 ? ' · at least one is required' : ''}
		</span>
		<a
			href={presetManageHref('harness')}
			target="_blank"
			rel="noopener noreferrer"
			class="inline-flex items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
		>
			Manage<ExternalLink class="size-3" aria-hidden="true" /><span class="sr-only"> harness presets (opens in a new tab)</span>
		</a>
	</div>

	{#if !presets.loaded}
		<LoadingState label="loading harness presets" class="rounded-md border border-border py-8" />
	{:else if presets.error && presets.list.length === 0}
		<ErrorAlert message={presets.error} onretry={() => presets.retry()} />
	{:else if groups.length === 0}
		<EmptyState
			compact
			class="rounded-md border border-border"
			title="No harness presets"
			description="System presets are seeded on startup; ask an admin to check the deployment."
		/>
	{:else}
		{#each groups as group (group.name)}
			<div class="space-y-2" role="group" aria-labelledby="harness-group-{group.name}">
				<div class="flex flex-wrap items-start justify-between gap-2">
					<div class="min-w-0 flex-1">
						<p id="harness-group-{group.name}" class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">
							{group.info?.label ?? group.name}
							{#if group.info}
								<span class="font-mono text-[11px] font-normal text-muted-foreground">{group.info.version}</span>
								{#if group.info.requires_tool_calling}
									<span class={cn(chip, 'border-border bg-muted font-normal text-muted-foreground')}>
										<Wrench class="size-3" aria-hidden="true" />needs tool calling
									</span>
								{/if}
							{:else}
								<span class={cn(chip, 'border-warning/30 bg-warning/10 text-warning-fg')}>unknown harness</span>
							{/if}
						</p>
						{#if group.info?.description}
							<p class="mt-0.5 text-xs text-muted-foreground">{group.info.description}</p>
						{/if}
					</div>
					{#if group.info && group.info.option_schema.length > 0}
						<Button variant="ghost" size="sm" class="h-7 shrink-0 px-2" {disabled} onclick={() => openDialog(group.name)}>
							<Plus />New preset
						</Button>
					{/if}
				</div>
				<div class="grid gap-2 @xl:grid-cols-2">
					{#each group.presets as preset (preset.id)}
						{@const checked = selectedSet.has(preset.id)}
						{@const latest = presets.newerVersion(preset)}
						{@const chips = harnessOptionChips(preset.config.options, group.info?.option_schema ?? [])}
						{@const dupeOf = duplicates.get(preset.id)}
						{@const lastOne = checked && selectedIds.length === 1}
						<div
							class={cn(
								'flex min-w-0 flex-col rounded-md border transition-colors duration-150',
								checked ? 'border-primary/50 bg-primary/5' : 'border-border hover:border-primary/30'
							)}
						>
							<button
								type="button"
								role="checkbox"
								aria-checked={checked}
								aria-disabled={lastOne || disabled ? true : undefined}
								title={lastOne ? 'At least one harness preset is required' : undefined}
								class="flex w-full min-w-0 flex-1 cursor-pointer items-start gap-2.5 rounded-md p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 aria-disabled:cursor-default"
								onclick={() => toggle(preset)}
							>
								<span
									class={cn(
										'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors duration-150',
										checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/60 bg-surface-1'
									)}
									aria-hidden="true"
								>
									{#if checked}<Check class="size-3" strokeWidth={3} />{/if}
								</span>
								<span class="min-w-0 flex-1 space-y-1">
									<span class="flex flex-wrap items-center gap-1.5">
										<span class="text-sm font-medium text-foreground">{preset.name}</span>
										<span class="font-mono text-[11px] text-muted-foreground">v{preset.version}</span>
										{#if preset.is_default}
											<span class={cn(chip, 'border-transparent bg-primary/15 text-success-fg')}>default</span>
										{:else if preset.is_system}
											<span class={cn(chip, 'border-border bg-muted text-muted-foreground')}>system</span>
										{:else}
											<span class={cn(chip, 'border-border text-muted-foreground')}>yours</span>
										{/if}
										{#if preset.is_archived}
											<span class={cn(chip, 'border-warning/30 bg-warning/10 text-warning-fg')}>archived</span>
										{/if}
									</span>
									<span class="flex flex-wrap gap-1">
										{#if chips.length === 0}
											<span class="text-[11px] text-muted-foreground">Default options</span>
										{:else}
											{#each chips as text (text)}
												<span class={cn(chip, 'border-info/30 bg-info/10 font-mono text-info-fg')}>{text}</span>
											{/each}
										{/if}
									</span>
									{#if preset.description}
										<span class="line-clamp-2 block text-xs text-muted-foreground">{preset.description}</span>
									{/if}
								</span>
							</button>
							{#if (checked && latest) || dupeOf}
								<div class="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-border/60 px-3 py-1.5 text-[11px] text-warning-fg">
									{#if dupeOf}
										<span class="flex items-center gap-1"
											><TriangleAlert class="size-3" aria-hidden="true" />Same configuration as {dupeOf.name}</span
										>
									{:else if latest}
										<span>Pinned v{preset.version} · latest is v{latest.version}</span>
										<Button
											variant="link"
											size="sm"
											class="h-auto px-0 text-[11px]"
											{disabled}
											onclick={() => useLatest(preset, latest)}>Use latest</Button
										>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
					{#if group.presets.length === 0}
						<p class="text-xs text-muted-foreground @xl:col-span-2">No presets for this harness yet.</p>
					{/if}
				</div>
			</div>
		{/each}
		{#if pending > 0 && presets.lookupError}
			<p class="flex flex-wrap items-center gap-x-2 text-[11px] text-warning-fg" role="status">
				<span>
					{pending} selected preset version{pending === 1 ? '' : 's'} couldn't be loaded ({presets.lookupError}); still selected.
				</span>
				<Button variant="link" size="sm" class="h-auto px-0 text-[11px]" onclick={() => presets.retry()}>Retry</Button>
			</p>
		{:else if pending > 0}
			<p class="text-[11px] text-muted-foreground" role="status">Loading {pending} pinned preset version{pending === 1 ? '' : 's'}…</p>
		{/if}
	{/if}
</div>

{#if tunableCount > 0}
	<NewHarnessPresetDialog bind:open={dialogOpen} bind:harness={dialogHarness} {harnesses} onsaved={onSaved} />
{/if}
